"""Per-file mutation queue."""

from __future__ import annotations

import os
import threading
from typing import Callable, TypeVar

T = TypeVar("T")

_registry_lock = threading.Lock()
_file_locks: dict[str, tuple[threading.Lock, int]] = {}


def _queue_key(file_path: str) -> str:
    return os.path.realpath(os.path.abspath(file_path))


def with_file_mutation_queue(file_path: str, fn: Callable[[], T]) -> T:
    key = _queue_key(file_path)
    with _registry_lock:
        lock, count = _file_locks.get(key, (threading.Lock(), 0))
        _file_locks[key] = (lock, count + 1)

    lock.acquire()
    try:
        return fn()
    finally:
        lock.release()
        with _registry_lock:
            current = _file_locks.get(key)
            if current is None:
                pass
            else:
                current_lock, count = current
                if current_lock is lock and count <= 1:
                    _file_locks.pop(key, None)
                elif current_lock is lock:
                    _file_locks[key] = (lock, count - 1)
