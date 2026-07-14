"""Exclusive ownership for one active agent run."""

from __future__ import annotations

import threading


class RunLease:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_token: object | None = None
        self._owner_thread_id: int | None = None

    @property
    def active(self) -> bool:
        with self._condition:
            return self._active_token is not None

    @property
    def owned_by_current_thread(self) -> bool:
        with self._condition:
            return self._active_token is not None and self._owner_thread_id == threading.get_ident()

    def acquire(self, error_message: str) -> "RunLeaseToken":
        with self._condition:
            if self._active_token is not None:
                raise RuntimeError(error_message)
            token = object()
            self._active_token = token
            self._owner_thread_id = threading.get_ident()
            return RunLeaseToken(self, token)

    def wait(self, timeout: float | None = None) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._active_token is None, timeout=timeout)

    def _release(self, token: object) -> None:
        with self._condition:
            if token is not self._active_token:
                return
            self._active_token = None
            self._owner_thread_id = None
            self._condition.notify_all()


class RunLeaseToken:
    def __init__(self, lease: RunLease, token: object) -> None:
        self._lease = lease
        self._token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lease._release(self._token)


__all__ = [
    "RunLease",
    "RunLeaseToken",
]
