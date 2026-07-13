"""Bounded, rebuildable metadata index for authoritative JSONL sessions."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


_HEAD_LIMIT = 8 * 1024
_TAIL_LIMIT = 64 * 1024
_TAIL_RECORD_LIMIT = 256
_PREVIEW_LIMIT = 120


@dataclass(frozen=True)
class SessionIndexRecord:
    path: Path
    session_id: str
    cwd: Path
    created_at: datetime
    modified_ns: int
    size_bytes: int
    device: int
    inode: int
    name: str | None
    preview: str
    model: str | None


@dataclass(frozen=True)
class SessionScanStats:
    files_statted: int = 0
    files_backfilled: int = 0
    bytes_read: int = 0
    records_decoded: int = 0
    cache_hits: int = 0


class SessionIndex:
    """SQLite cache whose rows can always be reconstructed from JSONL files."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._diagnostics: tuple[str, ...] = ()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '1')"
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    path TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    device INTEGER NOT NULL,
                    inode INTEGER NOT NULL,
                    name TEXT,
                    preview TEXT NOT NULL,
                    model TEXT
                )"""
            )
            self._connection.execute("CREATE INDEX IF NOT EXISTS sessions_cwd_idx ON sessions(cwd)")
            self._connection.execute("CREATE INDEX IF NOT EXISTS sessions_id_idx ON sessions(session_id)")
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS sessions_modified_idx ON sessions(modified_ns DESC)"
            )

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._diagnostics

    def upsert(self, record: SessionIndexRecord) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sessions
                   (path, session_id, cwd, created_at, modified_ns, size_bytes, device, inode, name, preview, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     session_id=excluded.session_id, cwd=excluded.cwd,
                     created_at=excluded.created_at, modified_ns=excluded.modified_ns,
                     size_bytes=excluded.size_bytes, device=excluded.device,
                     inode=excluded.inode, name=excluded.name,
                     preview=excluded.preview, model=excluded.model""",
                (
                    str(record.path),
                    record.session_id,
                    str(record.cwd),
                    record.created_at.isoformat(),
                    record.modified_ns,
                    record.size_bytes,
                    record.device,
                    record.inode,
                    record.name,
                    record.preview,
                    record.model,
                ),
            )

    def query(self, cwd: str | os.PathLike[str] | None = None) -> tuple[SessionIndexRecord, ...]:
        sql = (
            "SELECT path, session_id, cwd, created_at, modified_ns, size_bytes, "
            "device, inode, name, preview, model FROM sessions"
        )
        parameters: tuple[str, ...] = ()
        if cwd is not None:
            sql += " WHERE cwd = ?"
            parameters = (str(Path(cwd).expanduser().resolve()),)
        sql += " ORDER BY modified_ns DESC, path DESC"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def record_header(self, path: Path, header: dict[str, object], stat: os.stat_result) -> None:
        resolved = path.expanduser().resolve()
        session_id, cwd = _validate_header(resolved, header)
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        self.upsert(
            SessionIndexRecord(
                path=resolved,
                session_id=session_id,
                cwd=cwd,
                created_at=_parse_timestamp(header.get("timestamp"), modified_at),
                modified_ns=stat.st_mtime_ns,
                size_bytes=stat.st_size,
                device=stat.st_dev,
                inode=stat.st_ino,
                name=None,
                preview="",
                model=None,
            )
        )

    def record_append(self, path: Path, entry: dict[str, object], stat: os.stat_result) -> None:
        resolved = path.expanduser().resolve()
        existing = self._get(resolved)
        if existing is None:
            self.reconcile([resolved])
            return
        name, preview, model = _apply_summary_entry(
            entry,
            name=existing.name,
            preview=existing.preview,
            model=existing.model,
        )
        self.upsert(
            replace(
                existing,
                modified_ns=stat.st_mtime_ns,
                size_bytes=stat.st_size,
                device=stat.st_dev,
                inode=stat.st_ino,
                name=name,
                preview=preview,
                model=model,
            )
        )

    def reconcile(self, paths: Iterable[Path]) -> SessionScanStats:
        files_statted = 0
        files_backfilled = 0
        bytes_read = 0
        records_decoded = 0
        cache_hits = 0
        diagnostics: list[str] = []
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            try:
                stat = path.stat()
            except OSError as error:
                diagnostics.append(f"Session file {path} is invalid: {error}")
                continue
            files_statted += 1
            existing = self._get(path)
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if existing is not None and signature == (
                existing.device,
                existing.inode,
                existing.size_bytes,
                existing.modified_ns,
            ):
                cache_hits += 1
                continue
            files_backfilled += 1
            try:
                record, read_count, decoded_count = self._backfill(path, stat, existing)
                bytes_read += read_count
                records_decoded += decoded_count
                self.upsert(record)
            except (OSError, ValueError) as error:
                diagnostics.append(f"Session file {path} is invalid: {error}")
                self._delete(path)
        self._diagnostics = tuple(diagnostics)
        return SessionScanStats(
            files_statted=files_statted,
            files_backfilled=files_backfilled,
            bytes_read=bytes_read,
            records_decoded=records_decoded,
            cache_hits=cache_hits,
        )

    def remove_missing(self, paths: Iterable[Path]) -> None:
        retained = {str(Path(path).expanduser().resolve()) for path in paths}
        with self._lock, self._connection:
            rows = self._connection.execute("SELECT path FROM sessions").fetchall()
            for (stored_path,) in rows:
                if stored_path not in retained:
                    self._connection.execute("DELETE FROM sessions WHERE path = ?", (stored_path,))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()

    def _get(self, path: Path) -> SessionIndexRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT path, session_id, cwd, created_at, modified_ns, size_bytes, "
                "device, inode, name, preview, model FROM sessions WHERE path = ?",
                (str(path),),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def _delete(self, path: Path) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM sessions WHERE path = ?", (str(path),))

    def _backfill(
        self,
        path: Path,
        stat: os.stat_result,
        existing: SessionIndexRecord | None,
    ) -> tuple[SessionIndexRecord, int, int]:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_LIMIT)
            tail_offset = max(0, stat.st_size - _TAIL_LIMIT)
            handle.seek(tail_offset)
            tail = handle.read(_TAIL_LIMIT)
        bytes_read = len(head) + len(tail)
        header_line = head.splitlines(keepends=True)[0] if head else b""
        if not header_line or (not header_line.endswith((b"\n", b"\r")) and stat.st_size > len(head)):
            raise ValueError("session header exceeds bounded read")
        header = _decode_record(header_line, 1)
        session_id, cwd = _validate_header(path, header)
        decoded = 1

        tail_lines = tail.splitlines(keepends=True)
        if tail_offset > 0 and tail_lines:
            tail_lines = tail_lines[1:]
        elif tail_offset == 0 and tail_lines:
            tail_lines = tail_lines[1:]
        tail_lines = tail_lines[-_TAIL_RECORD_LIMIT:]
        name = existing.name if existing is not None else None
        preview = existing.preview if existing is not None else ""
        model = existing.model if existing is not None else None
        for offset, raw_line in enumerate(tail_lines, start=2):
            if not raw_line.strip():
                continue
            if not raw_line.endswith((b"\n", b"\r")):
                continue
            entry = _decode_record(raw_line, offset)
            decoded += 1
            name, preview, model = _apply_summary_entry(
                entry,
                name=name,
                preview=preview,
                model=model,
            )

        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        return (
            SessionIndexRecord(
                path=path,
                session_id=session_id,
                cwd=cwd,
                created_at=_parse_timestamp(header.get("timestamp"), modified_at),
                modified_ns=stat.st_mtime_ns,
                size_bytes=stat.st_size,
                device=stat.st_dev,
                inode=stat.st_ino,
                name=name,
                preview=preview,
                model=model,
            ),
            bytes_read,
            decoded,
        )


def _record_from_row(row: tuple[object, ...]) -> SessionIndexRecord:
    return SessionIndexRecord(
        path=Path(str(row[0])),
        session_id=str(row[1]),
        cwd=Path(str(row[2])),
        created_at=datetime.fromisoformat(str(row[3])),
        modified_ns=int(row[4]),
        size_bytes=int(row[5]),
        device=int(row[6]),
        inode=int(row[7]),
        name=str(row[8]) if row[8] is not None else None,
        preview=str(row[9]),
        model=str(row[10]) if row[10] is not None else None,
    )


def _decode_record(raw_line: bytes, line_number: int) -> dict[str, object]:
    try:
        entry = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"line {line_number}: {error}") from error
    if not isinstance(entry, dict):
        raise ValueError(f"line {line_number}: record is not an object")
    return entry


def _validate_header(path: Path, header: dict[str, object]) -> tuple[str, Path]:
    if header.get("type") != "session":
        raise ValueError("first record is not a session header")
    session_id = header.get("id")
    cwd = header.get("cwd")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session header has no ID")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("session header has no cwd")
    return session_id, Path(cwd).expanduser().resolve()


def _parse_timestamp(value: object, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def _apply_summary_entry(
    entry: dict[str, object],
    *,
    name: str | None,
    preview: str,
    model: str | None,
) -> tuple[str | None, str, str | None]:
    entry_type = entry.get("type")
    if entry_type == "session_info":
        raw_name = entry.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    elif entry_type == "model_change":
        provider = entry.get("provider")
        model_id = entry.get("modelId")
        if isinstance(provider, str) and isinstance(model_id, str):
            model = f"{provider}/{model_id}"
    elif entry_type == "message":
        message = entry.get("message")
        if isinstance(message, dict) and message.get("role") == "user":
            preview = _message_preview(message.get("content"))
    return name, preview, model


def _message_preview(content: object) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    else:
        text = ""
    normalized = " ".join(text.split())
    if len(normalized) <= _PREVIEW_LIMIT:
        return normalized
    return f"{normalized[:_PREVIEW_LIMIT]}..."


__all__ = ["SessionIndex", "SessionIndexRecord", "SessionScanStats"]
