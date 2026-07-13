"""Owner-thread dispatcher for TUI state changes and rendering."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class UiDispatcher:
    def __init__(
        self,
        *,
        render: Callable[[bool], object],
        clock: Callable[[], float] = time.monotonic,
        owner_thread_id: int | None = None,
        render_interval: float = 0.016,
    ) -> None:
        self._render = render
        self._clock = clock
        self._owner_thread_id = owner_thread_id if owner_thread_id is not None else threading.get_ident()
        self._render_interval = max(0.0, float(render_interval))
        self._lock = threading.RLock()
        self._callbacks: deque[Callable[[], None]] = deque()
        self._render_requested = False
        self._force_render = False
        self._last_render_at: float | None = None
        self._last_render_result: object | None = None
        self._drain_depth = 0

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    @property
    def last_render_result(self) -> object | None:
        with self._lock:
            return self._last_render_result

    def is_owner_thread(self) -> bool:
        return threading.get_ident() == self._owner_thread_id

    def adopt_current_thread(self) -> None:
        with self._lock:
            self._owner_thread_id = threading.get_ident()

    def post(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def request_render(self, force: bool = False) -> object | None:
        with self._lock:
            self._render_requested = True
            self._force_render = self._force_render or bool(force)
            should_drain = self._drain_depth == 0
        if self.is_owner_thread() and should_drain:
            self.drain()
        return self.last_render_result

    def drain(self) -> int:
        if not self.is_owner_thread():
            raise RuntimeError("UI dispatcher may only be drained by its owner thread")

        with self._lock:
            self._drain_depth += 1
        try:
            applied = 0
            while True:
                with self._lock:
                    if not self._callbacks:
                        break
                    callback = self._callbacks.popleft()
                callback()
                applied += 1

            with self._lock:
                requested = self._render_requested
                force = self._force_render
                last_render_at = self._last_render_at
            now = self._clock()
            due = force or last_render_at is None or now - last_render_at >= self._render_interval
            if requested and due:
                result = self._render(force)
                with self._lock:
                    self._render_requested = False
                    self._force_render = False
                    self._last_render_at = now
                    self._last_render_result = result
            return applied
        finally:
            with self._lock:
                self._drain_depth -= 1

    def time_until_next_work(self, default: float) -> float:
        fallback = max(0.0, float(default))
        with self._lock:
            if self._callbacks:
                return 0.0
            if not self._render_requested:
                return fallback
            if self._force_render or self._last_render_at is None:
                return 0.0
            due_at = self._last_render_at + self._render_interval
        return min(fallback, max(0.0, due_at - self._clock()))


__all__ = ["UiDispatcher"]
