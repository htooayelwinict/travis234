"""Lifecycle helpers shared by SQLite-backed runtime indexes."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
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


def open_secure_sqlite(
    path: str | os.PathLike[str], *, busy_timeout_ms: int = 5_000
) -> tuple[Path, sqlite3.Connection]:
    """Open a private WAL database without interpreting or repairing its schema."""

    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(resolved, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    connection = sqlite3.connect(
        resolved,
        timeout=max(0, busy_timeout_ms) / 1000,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {max(0, int(busy_timeout_ms))}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    secure_sqlite_files(resolved)
    return resolved, connection


def secure_sqlite_files(path: str | os.PathLike[str]) -> None:
    resolved = Path(path)
    for candidate in (
        resolved,
        Path(str(resolved) + "-wal"),
        Path(str(resolved) + "-shm"),
    ):
        try:
            candidate.chmod(0o600)
        except FileNotFoundError:
            pass
