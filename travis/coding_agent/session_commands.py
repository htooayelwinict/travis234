"""Single-owner executor for mutable session commands."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True)
class _Command(Generic[T]):
    name: str
    callback: Callable[[], T]
    future: Future[T]


_STOP = object()


class SessionCommandExecutor:
    def __init__(self, *, thread_name: str = "travis-session-commands", daemon: bool = False) -> None:
        self._queue: queue.Queue[_Command[object] | object] = queue.Queue()
        self._lock = threading.RLock()
        self._closed = False
        self._pending = 0
        self._active_name: str | None = None
        self._thread = threading.Thread(target=self._run, name=thread_name, daemon=daemon)
        self._thread.start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    @property
    def active_name(self) -> str | None:
        with self._lock:
            return self._active_name

    @property
    def owner_thread(self) -> threading.Thread:
        return self._thread

    def is_owner_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def submit(self, name: str, callback: Callable[[], T]) -> Future[T]:
        future: Future[T] = Future()
        with self._lock:
            if self._closed:
                future.set_exception(RuntimeError("session command executor is closed"))
                return future
            self._pending += 1
            self._queue.put(cast(_Command[object], _Command(name=name, callback=callback, future=future)))
        return future

    def close(self, wait: bool = True, timeout: float = 1.0) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be nonnegative")
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put(_STOP)
        if wait and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            command = cast(_Command[object], item)
            with self._lock:
                self._active_name = command.name
            try:
                if command.future.set_running_or_notify_cancel():
                    try:
                        command.future.set_result(command.callback())
                    except BaseException as error:  # noqa: BLE001 - Future transports command failures.
                        command.future.set_exception(error)
            finally:
                with self._lock:
                    self._pending -= 1
                    self._active_name = None


__all__ = ["SessionCommandExecutor"]
