"""Durable, owner-scoped terminal process results."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from travis.coding_agent.processes.types import (
    InvalidCursorError,
    ProcessClosedError,
    ProcessCompletionRecord,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from travis.coding_agent.output_utils import line_count as _line_count
from travis.coding_agent.session_lock import SessionFileLock
from travis.coding_agent.sqlite_utils import close_sqlite_index
from travis.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    truncate_tail,
)


_SCHEMA_VERSION = 1
_PROCESS_ID = re.compile(r"^proc_[0-9a-f]{32}$")
_TERMINAL_STATES = frozenset(state.value for state in ProcessState if state.terminal)


@dataclass(frozen=True)
class _StoredCompletion:
    workspace_digest: str
    origin: str
    session_id: str
    state: ProcessState
    exit_code: int | None
    output_size: int
    total_lines: int
    elapsed_ms: int
    completed_at: float
    launch_session_id: str | None
    failure_code: str | None
    tty: bool
    relative_output_path: str


@dataclass(frozen=True)
class _OutputMetrics:
    size: int
    total_lines: int


class ProcessCompletionStore:
    def __init__(
        self,
        root: str | Path,
        *,
        retention_seconds: float = 7 * 24 * 60 * 60,
        max_total_bytes: int = 256 * 1024 * 1024,
        max_records: int = 10_000,
        orphan_grace_seconds: float = 60 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.objects.chmod(0o700)
        self.index_path = self.root / "index.sqlite3"
        self.retention_seconds = max(0.0, float(retention_seconds))
        self.max_total_bytes = max(0, int(max_total_bytes))
        self.max_records = max(0, int(max_records))
        self.orphan_grace_seconds = max(0.0, float(orphan_grace_seconds))
        self.clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._connection = self._open_index()
        self._cleanup_orphans()

    def persist(
        self,
        owner: ProcessOwner,
        record: ProcessCompletionRecord,
        sanitized_output: Path,
    ) -> Path:
        source = Path(sanitized_output)
        self._validate_record(record, source)
        digest = self._workspace_digest(owner)
        directory = self.objects / digest
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        output_path = directory / f"{record.session_id}-{uuid.uuid4().hex}.log"
        stale_paths: tuple[Path, ...] = ()
        with SessionFileLock(self.index_path):
            metrics = _atomic_copy_0600(source, output_path)
            if metrics.size != record.output_size:
                output_path.unlink(missing_ok=True)
                raise ValueError("Process completion output changed while it was persisted")
            try:
                with self._lock:
                    self._ensure_open()
                    with self._transaction() as connection:
                        connection.execute(
                            """
                            INSERT INTO completions (
                                workspace_digest, origin, session_id, state, exit_code,
                                output_size, total_lines, elapsed_ms, completed_at, launch_session_id,
                                failure_code, tty, relative_output_path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                digest,
                                owner.origin,
                                record.session_id,
                                record.state.value,
                                record.exit_code,
                                record.output_size,
                                metrics.total_lines,
                                record.elapsed_ms,
                                record.completed_at,
                                record.launch_session_id,
                                record.failure_code,
                                int(record.tty),
                                str(output_path.relative_to(self.root)),
                            ),
                        )
                        self._update_state(connection, record.output_size, 1)
                        stale_paths = self._prune_transaction(
                            connection,
                            keep=(digest, owner.origin, record.session_id),
                        )
            except Exception:
                output_path.unlink(missing_ok=True)
                raise
            self._unlink_paths(stale_paths)
        self._secure_sqlite_files()
        return output_path

    def resolve(
        self,
        owner: ProcessOwner,
        session_id: str,
        *,
        cursor: int,
        max_bytes: int,
    ) -> ProcessSnapshot | None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        stored = self._lookup(owner, session_id)
        if stored is None:
            return None
        output_path = self._validated_output_path(stored)
        if output_path is None:
            self._remove_invalid(stored)
            return None
        if cursor < 0 or cursor > stored.output_size:
            raise InvalidCursorError(cursor, stored.output_size)
        with output_path.open("rb") as handle:
            handle.seek(cursor)
            first = handle.read(1)
            if first and first[0] & 0xC0 == 0x80:
                raise InvalidCursorError(cursor, stored.output_size)
            handle.seek(cursor)
            raw = handle.read(min(max_bytes, stored.output_size - cursor))
        valid = _valid_utf8_prefix(raw)
        return self._snapshot(
            stored,
            output=valid.decode("utf-8"),
            cursor=cursor,
            next_cursor=cursor + len(valid),
            output_path=output_path,
        )

    def tail_snapshot(
        self,
        owner: ProcessOwner,
        session_id: str,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> TruncationResult:
        if max_lines <= 0 or max_bytes <= 0:
            raise ValueError("tail limits must be positive")
        stored = self._lookup(owner, session_id)
        if stored is None:
            raise FileNotFoundError(f"Process completion not found: {session_id}")
        output_path = self._validated_output_path(stored)
        if output_path is None:
            self._remove_invalid(stored)
            raise FileNotFoundError(f"Process completion output unavailable: {session_id}")
        text, starts_partial = _read_bounded_tail(output_path, max_lines=max_lines, max_bytes=max_bytes)
        bounded = truncate_tail(text, max_lines=max_lines, max_bytes=max_bytes)
        output_bytes = len(bounded.content.encode("utf-8"))
        output_lines = _line_count(bounded.content)
        truncated = stored.output_size > output_bytes or stored.total_lines > output_lines
        truncated_by = bounded.truncated_by
        if truncated and truncated_by is None:
            truncated_by = "bytes" if stored.output_size > output_bytes else "lines"
        return replace(
            bounded,
            truncated=truncated,
            truncated_by=truncated_by,
            total_lines=stored.total_lines,
            total_bytes=stored.output_size,
            output_lines=output_lines,
            output_bytes=output_bytes,
            last_line_partial=bounded.last_line_partial
            or bool(starts_partial and bounded.content and text.startswith(bounded.content)),
        )

    def inspect(self, owner: ProcessOwner, session_id: str) -> ProcessSnapshot | None:
        stored = self._lookup(owner, session_id)
        if stored is None:
            return None
        output_path = self._validated_output_path(stored)
        if output_path is None:
            self._remove_invalid(stored)
            return None
        return self._snapshot(
            stored,
            output="",
            cursor=stored.output_size,
            next_cursor=stored.output_size,
            output_path=output_path,
        )

    def inspect_many(
        self,
        owner: ProcessOwner,
        session_ids: Sequence[str],
    ) -> tuple[ProcessSnapshot | None, ...]:
        ids = tuple(session_ids)
        if len(ids) > 64:
            raise ValueError("inspect_many accepts at most 64 process IDs")
        if len(set(ids)) != len(ids):
            raise ValueError("inspect_many requires unique process IDs")
        if any(not _valid_process_id(value) for value in ids):
            raise ValueError("inspect_many received an invalid process ID")
        if not ids:
            return ()
        digest = self._workspace_digest(owner)
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                f"SELECT * FROM completions WHERE workspace_digest = ? AND origin = ? "
                f"AND session_id IN ({placeholders})",
                (digest, owner.origin, *ids),
            ).fetchall()
        by_id: dict[str, _StoredCompletion] = {}
        for row in rows:
            stored = self._stored_or_remove(row)
            if stored is not None:
                by_id[stored.session_id] = stored
        results: list[ProcessSnapshot | None] = []
        for session_id in ids:
            stored = by_id.get(session_id)
            if stored is None:
                results.append(None)
                continue
            output_path = self._validated_output_path(stored)
            if output_path is None:
                self._remove_invalid(stored)
                results.append(None)
                continue
            results.append(
                self._snapshot(
                    stored,
                    output="",
                    cursor=stored.output_size,
                    next_cursor=stored.output_size,
                    output_path=output_path,
                )
            )
        return tuple(results)

    def prune(self) -> None:
        with self._lock:
            self._ensure_open()
            with self._transaction() as connection:
                stale_paths = self._prune_transaction(connection, keep=None)
        self._unlink_paths(stale_paths)

    def close(self) -> None:
        close_sqlite_index(self)

    def _open_index(self) -> sqlite3.Connection:
        try:
            return self._initialize_index()
        except sqlite3.DatabaseError as error:
            if not _is_corrupt_database_error(error):
                raise
        with SessionFileLock(self.index_path):
            try:
                return self._initialize_index()
            except sqlite3.DatabaseError as error:
                if not _is_corrupt_database_error(error):
                    raise
                self._quarantine_index()
                return self._initialize_index()

    def _initialize_index(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.index_path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS store_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                schema_version INTEGER NOT NULL,
                total_output_bytes INTEGER NOT NULL,
                record_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS completions (
                workspace_digest TEXT NOT NULL,
                origin TEXT NOT NULL CHECK (origin IN ('agent', 'user')),
                session_id TEXT NOT NULL,
                state TEXT NOT NULL,
                exit_code INTEGER,
                output_size INTEGER NOT NULL CHECK (output_size >= 0),
                total_lines INTEGER NOT NULL CHECK (total_lines >= 0),
                elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
                completed_at REAL NOT NULL,
                launch_session_id TEXT,
                failure_code TEXT,
                tty INTEGER NOT NULL CHECK (tty IN (0, 1)),
                relative_output_path TEXT NOT NULL,
                PRIMARY KEY (workspace_digest, origin, session_id)
            );
            CREATE INDEX IF NOT EXISTS completions_completed_at
                ON completions(completed_at, session_id);
            """
            )
            connection.execute(
                "INSERT OR IGNORE INTO store_meta "
                "(id, schema_version, total_output_bytes, record_count) VALUES (1, ?, 0, 0)",
                (_SCHEMA_VERSION,),
            )
            version = connection.execute("SELECT schema_version FROM store_meta WHERE id = 1").fetchone()[0]
            if version != _SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported process completion schema version: {version}")
            self._secure_sqlite_files()
            return connection
        except Exception:
            connection.close()
            raise

    def _quarantine_index(self) -> None:
        quarantine_id = uuid.uuid4().hex
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.index_path}{suffix}")
            if not source.exists():
                continue
            destination = self.root / f"{self.index_path.name}.corrupt-{quarantine_id}{suffix}"
            os.replace(source, destination)
            destination.chmod(0o600)

    def _cleanup_orphans(self) -> None:
        cutoff = self.clock() - self.orphan_grace_seconds
        with SessionFileLock(self.index_path):
            with self._lock:
                self._ensure_open()
                indexed = {
                    str(row[0])
                    for row in self._connection.execute("SELECT relative_output_path FROM completions")
                }
            for path in self.objects.rglob("*.log"):
                try:
                    relative = str(path.relative_to(self.root))
                    if relative in indexed or path.stat().st_mtime > cutoff:
                        continue
                    path.unlink(missing_ok=True)
                except (FileNotFoundError, OSError, ValueError):
                    continue

    @contextmanager
    def _transaction(self):
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def _lookup(self, owner: ProcessOwner, session_id: str) -> _StoredCompletion | None:
        if not _valid_process_id(session_id):
            return None
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT * FROM completions WHERE workspace_digest = ? AND origin = ? AND session_id = ?",
                (self._workspace_digest(owner), owner.origin, session_id),
            ).fetchone()
        return self._stored_or_remove(row) if row is not None else None

    def _stored_or_remove(self, row: sqlite3.Row) -> _StoredCompletion | None:
        try:
            return self._stored(row)
        except (TypeError, ValueError, OverflowError):
            self._remove_raw_row(row)
            return None

    def _stored(self, row: sqlite3.Row) -> _StoredCompletion:
        state_value = str(row["state"])
        if state_value not in _TERMINAL_STATES:
            raise ValueError(f"Invalid terminal process state: {state_value}")
        return _StoredCompletion(
            workspace_digest=str(row["workspace_digest"]),
            origin=str(row["origin"]),
            session_id=str(row["session_id"]),
            state=ProcessState(state_value),
            exit_code=row["exit_code"],
            output_size=int(row["output_size"]),
            total_lines=int(row["total_lines"]),
            elapsed_ms=int(row["elapsed_ms"]),
            completed_at=float(row["completed_at"]),
            launch_session_id=row["launch_session_id"],
            failure_code=row["failure_code"],
            tty=bool(row["tty"]),
            relative_output_path=str(row["relative_output_path"]),
        )

    def _snapshot(
        self,
        stored: _StoredCompletion,
        *,
        output: str,
        cursor: int,
        next_cursor: int,
        output_path: Path,
    ) -> ProcessSnapshot:
        return ProcessSnapshot(
            session_id=stored.session_id,
            state=stored.state,
            output=output,
            cursor=cursor,
            next_cursor=next_cursor,
            output_size=stored.output_size,
            exit_code=stored.exit_code,
            tty=stored.tty,
            elapsed_ms=stored.elapsed_ms,
            durable_output=True,
            full_output_path=str(output_path),
            failure_code=stored.failure_code,
        )

    def _validated_output_path(self, stored: _StoredCompletion) -> Path | None:
        try:
            path = (self.root / stored.relative_output_path).resolve(strict=True)
            path.relative_to(self.objects)
        except (FileNotFoundError, OSError, ValueError):
            return None
        if not path.is_file() or path.stat().st_size != stored.output_size:
            return None
        return path

    def _validate_record(self, record: ProcessCompletionRecord, source: Path) -> None:
        self._ensure_open()
        if not _valid_process_id(record.session_id):
            raise ValueError("Invalid process completion ID")
        if not record.state.terminal:
            raise ValueError("Only terminal process states can be persisted")
        if record.output_size < 0 or record.elapsed_ms < 0:
            raise ValueError("Process completion sizes and duration must be nonnegative")
        if not source.is_file() or source.stat().st_size != record.output_size:
            raise ValueError("Process completion output size does not match the sanitized output file")
        if self.retention_seconds <= 0 or self.max_records < 1:
            raise ValueError("Process completion retention is disabled")
        if record.output_size > self.max_total_bytes:
            raise ValueError("Process completion output exceeds the durable store limit")

    def _prune_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        keep: tuple[str, str, str] | None,
    ) -> tuple[Path, ...]:
        stale: list[Path] = []
        cutoff = self.clock() - self.retention_seconds
        expired = connection.execute(
            "SELECT * FROM completions WHERE completed_at <= ? ORDER BY completed_at, session_id",
            (cutoff,),
        ).fetchall()
        for row in expired:
            stored = self._stored(row)
            if keep == (stored.workspace_digest, stored.origin, stored.session_id):
                continue
            stale.append(self.root / stored.relative_output_path)
            self._delete_record(connection, stored)
        total, count = self._state(connection)
        while total > self.max_total_bytes or count > self.max_records:
            query = "SELECT * FROM completions"
            params: tuple[object, ...] = ()
            if keep is not None:
                query += " WHERE NOT (workspace_digest = ? AND origin = ? AND session_id = ?)"
                params = keep
            row = connection.execute(query + " ORDER BY completed_at, session_id LIMIT 1", params).fetchone()
            if row is None:
                break
            stored = self._stored(row)
            stale.append(self.root / stored.relative_output_path)
            self._delete_record(connection, stored)
            total, count = self._state(connection)
        return tuple(stale)

    def _delete_record(self, connection: sqlite3.Connection, stored: _StoredCompletion) -> None:
        deleted = connection.execute(
            "DELETE FROM completions WHERE workspace_digest = ? AND origin = ? AND session_id = ?",
            (stored.workspace_digest, stored.origin, stored.session_id),
        ).rowcount
        if deleted:
            self._update_state(connection, -stored.output_size, -1)

    def _remove_invalid(self, stored: _StoredCompletion) -> None:
        with self._lock:
            self._ensure_open()
            with self._transaction() as connection:
                self._delete_record(connection, stored)

    def _remove_raw_row(self, row: sqlite3.Row) -> None:
        try:
            key = (str(row["workspace_digest"]), str(row["origin"]), str(row["session_id"]))
            relative_path = str(row["relative_output_path"])
        except (IndexError, TypeError, ValueError):
            return
        with self._lock:
            self._ensure_open()
            with self._transaction() as connection:
                connection.execute(
                    "DELETE FROM completions WHERE workspace_digest = ? AND origin = ? AND session_id = ?",
                    key,
                )
                total, count = connection.execute(
                    "SELECT COALESCE(SUM(output_size), 0), COUNT(*) FROM completions"
                ).fetchone()
                connection.execute(
                    "UPDATE store_meta SET total_output_bytes = ?, record_count = ? WHERE id = 1",
                    (int(total), int(count)),
                )
        self._unlink_paths((self.root / relative_path,))

    def _state(self, connection: sqlite3.Connection) -> tuple[int, int]:
        row = connection.execute(
            "SELECT total_output_bytes, record_count FROM store_meta WHERE id = 1"
        ).fetchone()
        return int(row[0]), int(row[1])

    def _update_state(self, connection: sqlite3.Connection, byte_delta: int, count_delta: int) -> None:
        connection.execute(
            "UPDATE store_meta SET total_output_bytes = total_output_bytes + ?, "
            "record_count = record_count + ? WHERE id = 1",
            (byte_delta, count_delta),
        )

    def _workspace_digest(self, owner: ProcessOwner) -> str:
        workspace = str(Path(owner.workspace_key).expanduser().resolve(strict=False))
        return hashlib.sha256(workspace.encode("utf-8")).hexdigest()

    def _scope_directory(self, owner: ProcessOwner) -> Path:
        return self.objects / self._workspace_digest(owner)

    def _secure_sqlite_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.index_path}{suffix}")
            if path.exists():
                path.chmod(0o600)

    def _unlink_paths(self, paths: Sequence[Path]) -> None:
        for path in paths:
            try:
                resolved = path.resolve(strict=False)
                resolved.relative_to(self.objects)
                resolved.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProcessClosedError("Process completion store is closed")


def _valid_process_id(value: object) -> bool:
    return isinstance(value, str) and _PROCESS_ID.fullmatch(value) is not None


def _is_corrupt_database_error(error: sqlite3.DatabaseError) -> bool:
    message = str(error).lower()
    return "not a database" in message or "malformed" in message or "file is encrypted" in message


def _atomic_copy_0600(source: Path, destination: Path) -> _OutputMetrics:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    size = 0
    newline_count = 0
    saw_data = False
    ends_with_newline = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                target.write(chunk)
                size += len(chunk)
                newline_count += chunk.count(b"\n")
                saw_data = True
                ends_with_newline = chunk.endswith(b"\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        total_lines = newline_count + int(saw_data and not ends_with_newline)
        return _OutputMetrics(size=size, total_lines=total_lines)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _read_bounded_tail(path: Path, *, max_lines: int, max_bytes: int) -> tuple[str, bool]:
    block_size = min(64 * 1024, max(4096, max_bytes))
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        position = size
        chunks: list[bytes] = []
        buffered = 0
        newlines = 0
        while position > 0 and buffered < max_bytes + block_size and newlines <= max_lines:
            count = min(block_size, position)
            position -= count
            handle.seek(position)
            chunk = handle.read(count)
            chunks.append(chunk)
            buffered += len(chunk)
            newlines += chunk.count(b"\n")
        raw = b"".join(reversed(chunks))
        starts_partial = False
        if position > 0:
            handle.seek(position - 1)
            starts_partial = handle.read(1) != b"\n"
        if starts_partial:
            while raw and raw[0] & 0xC0 == 0x80:
                raw = raw[1:]
        return raw.decode("utf-8"), starts_partial


def _valid_utf8_prefix(data: bytes) -> bytes:
    if not data:
        return b""
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError as error:
        if error.reason != "unexpected end of data":
            raise
        prefix = data[: error.start]
        prefix.decode("utf-8")
        return prefix


__all__ = [
    "ProcessCompletionStore",
]
