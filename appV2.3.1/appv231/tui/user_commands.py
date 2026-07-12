"""Asynchronous user-shell control plane for the interactive TUI."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from appv231.agent.types import AbortSignal
from appv231.coding_agent.agent_session import BashResult
from appv231.coding_agent.processes.service import ProcessSessionService, ProcessTransportFactory
from appv231.coding_agent.processes.types import (
    ProcessLaunchRequest,
    ProcessOwner,
    ProcessState,
    ProcessWaitCancelledError,
)

if TYPE_CHECKING:
    from appv231.coding_agent.agent_session import AgentSession

UserCommandResolver = Callable[[str, "UserCommandBinding", AbortSignal], "ResolvedUserCommand"]
CustomUserCommandRunner = Callable[[AbortSignal, Callable[[str], None]], BashResult]


class UserCommandLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class UserCommandBinding:
    session: "AgentSession" = field(repr=False, compare=False)
    session_id: str | None
    session_path: str | None
    exclude_from_context: bool


@dataclass(frozen=True)
class UserCommandHandle:
    command_id: str
    binding: UserCommandBinding
    command: str


@dataclass(frozen=True)
class ResolvedUserCommand:
    result: BashResult | None = None
    managed_request: ProcessLaunchRequest | None = None
    custom_runner: CustomUserCommandRunner | None = None

    def __post_init__(self) -> None:
        variants = sum(
            item is not None
            for item in (self.result, self.managed_request, self.custom_runner)
        )
        if variants != 1:
            raise ValueError("ResolvedUserCommand requires exactly one execution variant")

    @classmethod
    def immediate(cls, result: BashResult) -> "ResolvedUserCommand":
        return cls(result=result)

    @classmethod
    def managed(cls, request: ProcessLaunchRequest) -> "ResolvedUserCommand":
        return cls(managed_request=request)

    @classmethod
    def custom(cls, runner: CustomUserCommandRunner) -> "ResolvedUserCommand":
        return cls(custom_runner=runner)


@dataclass(frozen=True)
class UserCommandInspection:
    handle: UserCommandHandle
    owner: ProcessOwner
    process_id: str | None
    done: bool
    interrupt_requested: bool


@dataclass
class _UserCommandState:
    handle: UserCommandHandle
    owner: ProcessOwner
    signal: AbortSignal
    process_id: str | None = None
    interrupt_requested: bool = False
    done: bool = False
    thread: threading.Thread | None = None


class UserCommandController:
    def __init__(
        self,
        *,
        service: ProcessSessionService,
        owner_factory: Callable[[], ProcessOwner],
        resolver: UserCommandResolver,
        transport_factory: ProcessTransportFactory,
        on_output: Callable[[str, str], None] | None = None,
        on_complete: Callable[[UserCommandHandle, BashResult], None] | None = None,
        on_error: Callable[[UserCommandHandle, str], None] | None = None,
        max_active: int = 4,
    ) -> None:
        if max_active < 1:
            raise ValueError("max_active must be positive")
        self._service = service
        self._owner_factory = owner_factory
        self._resolver = resolver
        self._transport_factory = transport_factory
        self._on_output = on_output or (lambda _command_id, _text: None)
        self._on_complete = on_complete or (lambda _handle, _result: None)
        self._on_error = on_error or (lambda _handle, _message: None)
        self._max_active = max_active
        self._states: dict[str, _UserCommandState] = {}
        self._focused_id: str | None = None
        self._closed = False
        self._lock = threading.RLock()

    def start(self, command: str, binding: UserCommandBinding) -> UserCommandHandle:
        handle = UserCommandHandle(f"user_{uuid.uuid4().hex}", binding, command)
        state = _UserCommandState(handle, self._owner_factory(), AbortSignal())
        with self._lock:
            if self._closed:
                raise RuntimeError("user command controller is closed")
            active = sum(not item.done for item in self._states.values())
            if active >= self._max_active:
                raise UserCommandLimitError(
                    f"Reached active user command limit of {self._max_active}"
                )
            self._states[handle.command_id] = state
            self._focused_id = handle.command_id
            thread = threading.Thread(
                target=self._run,
                args=(state,),
                name=f"appv231-{handle.command_id}",
                daemon=True,
            )
            state.thread = thread
            thread.start()
        return handle

    def inspect(self, command_id: str) -> UserCommandInspection:
        with self._lock:
            state = self._states.get(command_id)
            if state is None:
                raise KeyError(command_id)
            return self._inspection(state)

    def list(self) -> tuple[UserCommandInspection, ...]:
        with self._lock:
            return tuple(
                self._inspection(state) for state in self._states.values() if not state.done
            )

    def interrupt_focused(self) -> bool:
        with self._lock:
            state = self._states.get(self._focused_id or "")
            if state is None or state.done or state.interrupt_requested:
                return False
            state.interrupt_requested = True
            process_id = state.process_id
        state.signal.abort()
        if process_id is not None:
            try:
                self._service.interrupt(state.owner, process_id, wait_ms=0)
            except Exception:
                pass
        return True

    def terminate(self, command_id: str) -> bool:
        with self._lock:
            state = self._states.get(command_id)
            if state is None or state.done:
                return False
            state.interrupt_requested = True
            process_id = state.process_id
        state.signal.abort()
        if process_id is not None:
            try:
                self._service.terminate(state.owner, process_id, wait_ms=0)
            except Exception:
                pass
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            states = [state for state in self._states.values() if not state.done]
        for state in states:
            state.signal.abort()
            if state.process_id is not None:
                try:
                    self._service.terminate(state.owner, state.process_id, wait_ms=0)
                except Exception:
                    pass
        for state in states:
            thread = state.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
            if thread is not None and thread.is_alive():
                self._finalize_error(state, "User command did not stop during shutdown")

    def _run(self, state: _UserCommandState) -> None:
        try:
            resolved = self._resolver(
                state.handle.command,
                state.handle.binding,
                state.signal,
            )
            if resolved.result is not None:
                self._finalize_result(state, resolved.result)
            elif resolved.custom_runner is not None:
                result = resolved.custom_runner(
                    state.signal,
                    lambda text: self._emit_output(state, text),
                )
                self._finalize_result(state, result)
            else:
                assert resolved.managed_request is not None
                self._run_managed(state, resolved.managed_request)
        except BaseException as error:  # noqa: BLE001 - worker errors become bounded UI events.
            self._finalize_error(state, f"{type(error).__name__}: {error}"[:500])

    def _run_managed(
        self,
        state: _UserCommandState,
        request: ProcessLaunchRequest,
    ) -> None:
        if state.signal.aborted:
            self._finalize_result(state, BashResult("", None, True, False))
            return
        snapshot = self._service.start(
            state.owner,
            request,
            self._transport_factory,
            yield_time_ms=0,
            signal=state.signal,
        )
        with self._lock:
            state.process_id = snapshot.session_id
        if snapshot.output:
            self._emit_output(state, snapshot.output)
        cursor = snapshot.next_cursor
        while not snapshot.state.terminal:
            try:
                snapshot = self._service.wait_terminal(
                    state.owner,
                    snapshot.session_id,
                    cursor,
                    wait_ms=900_000,
                    signal=state.signal,
                    on_update=lambda update: self._emit_output(state, update.output),
                )
            except ProcessWaitCancelledError:
                try:
                    self._service.interrupt(state.owner, snapshot.session_id, wait_ms=0)
                except Exception:
                    pass
                snapshot = self._service.wait_terminal(
                    state.owner,
                    snapshot.session_id,
                    cursor,
                    wait_ms=60_000,
                )
            cursor = snapshot.next_cursor
        tail = self._service.tail_snapshot(state.owner, snapshot.session_id)
        cancelled = state.signal.aborted or snapshot.state is ProcessState.TERMINATED
        self._finalize_result(
            state,
            BashResult(
                output=tail.content,
                exit_code=snapshot.exit_code,
                cancelled=cancelled,
                truncated=tail.truncated,
                full_output_path=snapshot.full_output_path,
            ),
        )

    def _emit_output(self, state: _UserCommandState, text: str) -> None:
        if not text:
            return
        try:
            self._on_output(state.handle.command_id, text)
        except BaseException:
            pass

    def _finalize_result(self, state: _UserCommandState, result: BashResult) -> None:
        if not self._claim_done(state):
            return
        try:
            self._on_complete(state.handle, result)
        except BaseException:
            pass

    def _finalize_error(self, state: _UserCommandState, message: str) -> None:
        if not self._claim_done(state):
            return
        try:
            self._on_error(state.handle, message)
        except BaseException:
            pass

    def _claim_done(self, state: _UserCommandState) -> bool:
        with self._lock:
            if state.done:
                return False
            state.done = True
            if self._focused_id == state.handle.command_id:
                active = [
                    item.handle.command_id
                    for item in self._states.values()
                    if not item.done and item is not state
                ]
                self._focused_id = active[-1] if active else None
            return True

    @staticmethod
    def _inspection(state: _UserCommandState) -> UserCommandInspection:
        return UserCommandInspection(
            handle=state.handle,
            owner=state.owner,
            process_id=state.process_id,
            done=state.done,
            interrupt_requested=state.interrupt_requested,
        )


__all__ = [
    "ResolvedUserCommand",
    "UserCommandBinding",
    "UserCommandController",
    "UserCommandHandle",
    "UserCommandInspection",
    "UserCommandLimitError",
]
