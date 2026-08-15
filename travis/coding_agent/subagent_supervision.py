"""Immutable, leak-bounded views over the existing subagent scheduler."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Callable, Literal, Protocol

SnapshotStatus = Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]


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
    "SupervisorSnapshot",
    "SupervisorSnapshotStore",
]
