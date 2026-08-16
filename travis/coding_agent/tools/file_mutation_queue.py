"""Per-file mutation queue."""

from __future__ import annotations

import os
import threading
from typing import Callable, TypeVar

T = TypeVar("T")

_registry_lock = threading.Lock()
_file_locks: dict[str, tuple[threading.RLock, int]] = {}


def _queue_key(file_path: str) -> str:
    return os.path.realpath(os.path.abspath(file_path))


def with_file_mutation_queue(file_path: str, fn: Callable[[], T]) -> T:
    key = _queue_key(file_path)
    with _registry_lock:
        lock, count = _file_locks.get(key, (threading.RLock(), 0))
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


def with_file_mutation_queues(file_paths: list[str], fn: Callable[[], T]) -> T:
    keys = sorted({_queue_key(path) for path in file_paths})
    registered: list[tuple[str, threading.RLock]] = []
    with _registry_lock:
        for key in keys:
            lock, count = _file_locks.get(key, (threading.RLock(), 0))
            _file_locks[key] = (lock, count + 1)
            registered.append((key, lock))

    acquired: list[threading.RLock] = []
    try:
        for _key, lock in registered:
            lock.acquire()
            acquired.append(lock)
        return fn()
    finally:
        for lock in reversed(acquired):
            lock.release()
        with _registry_lock:
            for key, lock in registered:
                current = _file_locks.get(key)
                if current is None:
                    continue
                current_lock, count = current
                if current_lock is lock and count <= 1:
                    _file_locks.pop(key, None)
                elif current_lock is lock:
                    _file_locks[key] = (lock, count - 1)
