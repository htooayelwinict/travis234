"""Owner-thread native-TUI broker for tool policy approvals."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, replace

from travis.agent.types import AbortSignal
from travis.coding_agent.policy import ApprovalResponse, ToolApprovalRequest


@dataclass(eq=False)
class _PendingApproval:
    request: ToolApprovalRequest
    signal: AbortSignal | None
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[ApprovalResponse]
    cancel_event: threading.Event


class _ChildApprovalBroker:
    def __init__(self, owner: "InteractiveToolApprovalBroker", role: str, task_id: str) -> None:
        self._owner = owner
        self._role = str(role)
        self._task_id = str(task_id)

    @property
    def pending_count(self) -> int:
        return self._owner.pending_count

    async def request(
        self,
        request: ToolApprovalRequest,
        signal: AbortSignal | None,
    ) -> ApprovalResponse:
        return await self._owner.request(
            replace(
                request,
                child_role=self._role,
                child_task_id=self._task_id,
            ),
            signal,
        )


class InteractiveToolApprovalBroker:
    """Queue approval requests while keeping all UI work on its owner thread."""

    def __init__(self) -> None:
        self._view: object | None = None
        self._pending: deque[_PendingApproval] = deque()
        self._active: _PendingApproval | None = None
        self._shutdown = False
        self._lock = threading.RLock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending) + (1 if self._active is not None else 0)

    def bind(self, view: object) -> None:
        with self._lock:
            self._view = view
        self._schedule_show()

    def bind_session(self, session: object) -> None:
        engine = getattr(session, "_tool_policy_engine", None)
        if engine is None:
            return
        engine.broker = self
        setattr(session, "_tool_approval_broker", self)

    def for_child(self, role: str, task_id: str) -> _ChildApprovalBroker:
        return _ChildApprovalBroker(self, role, task_id)

    async def request(
        self,
        request: ToolApprovalRequest,
        signal: AbortSignal | None,
    ) -> ApprovalResponse:
        if signal is not None and signal.aborted:
            return ApprovalResponse(scope="deny")
        loop = asyncio.get_running_loop()
        pending = _PendingApproval(
            request=request,
            signal=signal,
            loop=loop,
            future=loop.create_future(),
            cancel_event=threading.Event(),
        )
        with self._lock:
            if self._shutdown:
                return ApprovalResponse(scope="deny")
            self._pending.append(pending)
        unsubscribe = signal.add_callback(lambda: self._cancel(pending)) if signal is not None else None
        self._schedule_show()
        try:
            return await pending.future
        finally:
            if unsubscribe is not None:
                unsubscribe()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            pending = list(self._pending)
            self._pending.clear()
            if self._active is not None:
                pending.append(self._active)
                self._active.cancel_event.set()
        for item in pending:
            item.cancel_event.set()
            self._resolve(item, ApprovalResponse(scope="deny"))

    def _cancel(self, pending: _PendingApproval) -> None:
        pending.cancel_event.set()
        with self._lock:
            if self._active is pending:
                return
            try:
                self._pending.remove(pending)
            except ValueError:
                return
        self._resolve(pending, ApprovalResponse(scope="deny"))

    def _schedule_show(self) -> None:
        with self._lock:
            view = self._view
            ready = view is not None and not self._shutdown and self._active is None and bool(self._pending)
        if not ready:
            return
        tui = getattr(view, "tui", None)
        dispatcher = getattr(tui, "dispatcher", None)
        post = getattr(dispatcher, "post", None)
        if callable(post):
            post(self._show_next_owner)

    def _show_next_owner(self) -> None:
        with self._lock:
            view = self._view
            if self._shutdown or self._active is not None or not self._pending or view is None:
                return
            pending = self._pending.popleft()
            if pending.cancel_event.is_set():
                self._resolve(pending, ApprovalResponse(scope="deny"))
                self._schedule_show()
                return
            self._active = pending

        tui = getattr(view, "tui")
        if not tui.dispatcher.is_owner_thread():
            raise RuntimeError("Tool approvals must render on the TUI owner thread")
        choice = view.prompt_extension_select(
            self._title(pending.request),
            ("allow once", "allow for session", "deny"),
            {
                "cancelEvent": pending.cancel_event,
                "cancelOnEscape": True,
            },
            kind="policy",
        )
        response = {
            "allow once": ApprovalResponse(scope="once"),
            "allow for session": ApprovalResponse(scope="session"),
            "deny": ApprovalResponse(scope="deny"),
        }.get(choice, ApprovalResponse(scope="deny"))
        with self._lock:
            if self._active is pending:
                self._active = None
        self._resolve(pending, response)
        self._schedule_show()

    @staticmethod
    def _title(request: ToolApprovalRequest) -> str:
        lines = [
            f"Tool approval: {request.tool_name}",
            f"Effects: {', '.join(sorted(request.effects)) or 'undeclared'}",
        ]
        if request.child_role or request.child_task_id:
            lines.append(
                f"Child: {request.child_role or 'unknown'} ({request.child_task_id or 'unknown'})"
            )
        if request.safe_context:
            lines.append(
                "Context: "
                + ", ".join(f"{key}={value}" for key, value in request.safe_context.items())
            )
        lines.append(f"Fingerprint: {request.argument_fingerprint[:12]}")
        return "\n".join(lines)

    @staticmethod
    def _resolve(pending: _PendingApproval, response: ApprovalResponse) -> None:
        def settle() -> None:
            if not pending.future.done():
                pending.future.set_result(response)

        pending.loop.call_soon_threadsafe(settle)


__all__ = ["InteractiveToolApprovalBroker"]
