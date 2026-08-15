"""Immutable, leak-bounded views over the existing subagent scheduler."""

from __future__ import annotations

import threading
import hashlib
from dataclasses import dataclass, replace
from typing import Callable, Literal, Protocol

SnapshotStatus = Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]


@dataclass(frozen=True)
class ControlResult:
    accepted: bool
    code: str


class SubagentControlHandle(Protocol):
    def steer(self, message: str) -> ControlResult:
        raise NotImplementedError

    def cancel(self, reason: str) -> ControlResult:
        raise NotImplementedError


class SubagentControlStore:
    def __init__(self) -> None:
        self._handles: dict[str, SubagentControlHandle] = {}
        self._pending: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def attach(
        self,
        task_id: str,
        handle: SubagentControlHandle,
        *,
        known: bool,
        settled: bool,
    ) -> ControlResult:
        if not callable(getattr(handle, "steer", None)) or not callable(
            getattr(handle, "cancel", None)
        ):
            raise ValueError("subagent control handle must support steer and cancel")
        if not known:
            return ControlResult(False, "unknown_task")
        if settled:
            return ControlResult(False, "task_settled")
        with self._lock:
            self._handles[task_id] = handle
            pending = self._pending.pop(task_id, [])
        for message in pending:
            _invoke_control(handle.steer, message)
        return ControlResult(True, "control_attached")

    def detach(self, task_id: str) -> None:
        with self._lock:
            self._handles.pop(task_id, None)

    def steer(
        self,
        task_id: str,
        backend: str | None,
        message: str,
        *,
        settled: bool,
    ) -> ControlResult:
        if not isinstance(message, str) or not message.strip() or len(message) > 8192:
            raise ValueError("steering message must contain 1..8192 characters")
        if backend is None:
            return ControlResult(False, "unknown_task")
        if settled:
            return ControlResult(False, "task_settled")
        text = message.strip()
        with self._lock:
            handle = self._handles.get(task_id)
            if handle is None:
                if backend != "internal":
                    return ControlResult(False, "steering_unsupported")
                self._pending.setdefault(task_id, []).append(text)
                return ControlResult(True, "steering_queued")
        return _invoke_control(handle.steer, text)

    def cancel(self, task_id: str, reason: str) -> ControlResult | None:
        with self._lock:
            handle = self._handles.pop(task_id, None)
            self._pending.pop(task_id, None)
        return _invoke_control(handle.cancel, reason) if handle is not None else None

    def settle(self, task_id: str) -> None:
        with self._lock:
            self._handles.pop(task_id, None)
            self._pending.pop(task_id, None)


def control_event(
    task: SnapshotTask,
    action: str,
    value: str,
    result: ControlResult,
) -> dict[str, object]:
    return {
        "type": "subagent_control",
        "child_subagent_id": task.id,
        "child_role": task.role,
        "action": action,
        "accepted": result.accepted,
        "code": result.code,
        "message_length": len(value),
        "message_fingerprint": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16],
    }


def _invoke_control(
    callback: Callable[[str], ControlResult], value: str
) -> ControlResult:
    try:
        result = callback(value)
        return result if isinstance(result, ControlResult) else ControlResult(False, "control_failed")
    except Exception:
        return ControlResult(False, "control_failed")

class SnapshotTask(Protocol):
    id: str
    role: str
    backend: str


class SnapshotResult(Protocol):
    summary: str
    ended_at_ms: int


@dataclass(frozen=True)
class SubagentSnapshot:
    task_id: str
    role: str
    backend: str
    status: SnapshotStatus
    started_at_ms: int
    ended_at_ms: int
    summary_preview: str
    controllable: bool


@dataclass(frozen=True)
class SupervisorSnapshot:
    revision: int
    active_count: int
    capacity: int
    tasks: tuple[SubagentSnapshot, ...]


class SupervisorSnapshotStore:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._revision = 0
        self._tasks: dict[str, SubagentSnapshot] = {}
        self._subscribers: dict[int, Callable[[SupervisorSnapshot], None]] = {}
        self._next_subscriber = 0
        self._lock = threading.RLock()

    def snapshot(self) -> SupervisorSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def subscribe(
        self, callback: Callable[[SupervisorSnapshot], None]
    ) -> Callable[[], None]:
        if not callable(callback):
            raise ValueError("snapshot subscriber must be callable")
        with self._lock:
            token = self._next_subscriber
            self._next_subscriber += 1
            self._subscribers[token] = callback
            initial = self._snapshot_locked()
        self._safe_call(callback, initial)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(token, None)

        return unsubscribe

    def publish(
        self,
        task: SnapshotTask,
        status: SnapshotStatus,
        *,
        started_at_ms: int,
        result: SnapshotResult | None = None,
    ) -> None:
        terminal = status not in {"queued", "running"}
        summary = "" if result is None else _preview(result.summary)
        ended = 0 if result is None else result.ended_at_ms
        with self._lock:
            self._tasks[task.id] = SubagentSnapshot(
                task_id=task.id,
                role=task.role,
                backend=task.backend,
                status=status,
                started_at_ms=started_at_ms,
                ended_at_ms=ended if terminal else 0,
                summary_preview=summary if terminal else "",
                controllable=not terminal,
            )
            snapshot, callbacks = self._advance_locked()
        self._dispatch(callbacks, snapshot)

    def publish_shutdown(self) -> None:
        with self._lock:
            self._tasks = {
                task_id: replace(item, controllable=False)
                for task_id, item in self._tasks.items()
            }
            snapshot, callbacks = self._advance_locked()
        self._dispatch(callbacks, snapshot)

    def _advance_locked(
        self,
    ) -> tuple[SupervisorSnapshot, tuple[Callable[[SupervisorSnapshot], None], ...]]:
        self._revision += 1
        return self._snapshot_locked(), tuple(self._subscribers.values())

    def _snapshot_locked(self) -> SupervisorSnapshot:
        tasks = tuple(self._tasks.values())
        return SupervisorSnapshot(
            revision=self._revision,
            active_count=sum(item.status in {"queued", "running"} for item in tasks),
            capacity=self._capacity,
            tasks=tasks,
        )

    @classmethod
    def _dispatch(
        cls,
        callbacks: tuple[Callable[[SupervisorSnapshot], None], ...],
        snapshot: SupervisorSnapshot,
    ) -> None:
        for callback in callbacks:
            cls._safe_call(callback, snapshot)

    @staticmethod
    def _safe_call(
        callback: Callable[[SupervisorSnapshot], None], snapshot: SupervisorSnapshot
    ) -> None:
        try:
            callback(snapshot)
        except Exception:
            pass


def _preview(value: str) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= 160 else text[:157].rstrip() + "..."


__all__ = [
    "SubagentSnapshot",
    "ControlResult",
    "SubagentControlHandle",
    "SubagentControlStore",
    "control_event",
    "SupervisorSnapshot",
    "SupervisorSnapshotStore",
]
