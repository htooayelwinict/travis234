"""Lifecycle helpers shared by SQLite-backed runtime indexes."""

from __future__ import annotations

import sqlite3
import threading
from typing import Protocol


class SqliteIndexOwner(Protocol):
    _lock: threading.RLock | threading.Lock
    _closed: bool
    _connection: sqlite3.Connection


def close_sqlite_index(owner: SqliteIndexOwner) -> None:
    with owner._lock:
        if owner._closed:
            return
        owner._closed = True
        owner._connection.close()
