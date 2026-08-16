"""Private indexed SQLite storage for explicitly retained facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from travis.coding_agent.memory.types import (
    MEMORY_PROVENANCE,
    MemoryFact,
    MemoryProvenance,
    MemoryScope,
    MemorySettings,
)
from travis.coding_agent.sqlite_utils import open_secure_sqlite, secure_sqlite_files


_SCHEMA_VERSION = "1"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"[\w-]+", re.UNICODE)
_GLOBAL_KEY = hashlib.sha256(b"travis234-global-memory").hexdigest()


class MemoryStoreError(RuntimeError):
    code = "memory_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class MemoryStoreUnavailable(MemoryStoreError):
    code = "memory_unavailable"


class MemoryStoreCapacity(MemoryStoreError):
    code = "memory_capacity"


def project_key_for_path(path: str | Path) -> str:
    canonical = str(Path(path).expanduser().resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_content(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("memory content must be a string")
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").strip()
    if not normalized:
        raise ValueError("memory content must be non-empty")
    return normalized


def _normalize_tags(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise TypeError("memory tags must be a list")
    if len(values) > 16:
        raise ValueError("memory accepts at most 16 tags")
    tags: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("memory tags must be strings")
        tag = unicodedata.normalize("NFC", value).strip().casefold()
        if not tag:
            raise ValueError("memory tags must be non-empty")
        if len(tag.encode("utf-8")) > 64:
            raise ValueError("memory tag exceeds 64 bytes")
        tags.add(tag)
    return tuple(sorted(tags))


def _require_project_key(value: object) -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError("invalid project key")
    return value


class MemoryStore:
    def __init__(
        self,
        path: str | Path,
        *,
        settings: MemorySettings | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.settings = settings or MemorySettings()
        self._lock = threading.RLock()
        self._closed = False
        connection: sqlite3.Connection | None = None
        try:
            self.path, connection = open_secure_sqlite(
                path, busy_timeout_ms=busy_timeout_ms
            )
            self._connection = connection
            self._initialize_schema()
        except (OSError, sqlite3.DatabaseError, MemoryStoreUnavailable):
            if connection is not None:
                connection.close()
            raise MemoryStoreUnavailable() from None

    def _initialize_schema(self) -> None:
        tables = {
            row["name"]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "memory_meta" in tables:
            version = self._connection.execute(
                "SELECT value FROM memory_meta WHERE key='schema_version'"
            ).fetchone()
            if version is None or version["value"] != _SCHEMA_VERSION or tables != {
                "memory_meta",
                "memory_facts",
            }:
                raise MemoryStoreUnavailable()
            return
        if tables:
            raise MemoryStoreUnavailable()
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO memory_meta VALUES ('schema_version', '1');
            CREATE TABLE memory_facts (
                memory_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                project_key TEXT NOT NULL,
                content TEXT NOT NULL,
                content_folded TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                provenance TEXT NOT NULL,
                source_session_fingerprint TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER,
                UNIQUE(scope, project_key, digest)
            );
            CREATE INDEX memory_scope_idx
              ON memory_facts(scope, project_key, updated_at_ms);
            COMMIT;
            """
        )
        secure_sqlite_files(self.path)

    def _require_open(self) -> None:
        if self._closed:
            raise MemoryStoreUnavailable()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._ensure_capacity()
                yield self._connection
                self._ensure_capacity()
                self._connection.commit()
                secure_sqlite_files(self.path)
            except MemoryStoreError:
                self._connection.rollback()
                raise
            except sqlite3.DatabaseError:
                self._connection.rollback()
                raise MemoryStoreUnavailable() from None
            except BaseException:
                self._connection.rollback()
                raise

    def _ensure_capacity(self) -> None:
        footprint = sum(
            candidate.stat().st_size
            for candidate in (
                self.path,
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            )
            if candidate.exists()
        )
        if footprint >= self.settings.max_total_bytes:
            raise MemoryStoreCapacity()

    def _scope_key(self, scope: MemoryScope, project_key: str) -> str:
        if scope not in self.settings.allowed_scopes:
            if scope == "global":
                raise ValueError("global scope is not allowed")
            raise ValueError("memory scope is not allowed")
        return _GLOBAL_KEY if scope == "global" else _require_project_key(project_key)

    def retain(
        self,
        content: str,
        *,
        tags: tuple[str, ...] | list[str],
        scope: MemoryScope,
        project_key: str,
        provenance: MemoryProvenance,
        now_ms: int,
        expires_at_ms: int | None = None,
        source_session_fingerprint: str | None = None,
    ) -> MemoryFact:
        normalized = _normalize_content(content)
        if len(normalized.encode("utf-8")) > self.settings.max_fact_bytes:
            raise ValueError("memory fact exceeds maxFactBytes")
        normalized_tags = _normalize_tags(tags)
        key = self._scope_key(scope, project_key)
        if provenance not in MEMORY_PROVENANCE:
            raise ValueError("invalid memory provenance")
        candidate = MemoryFact(
            f"mem_{uuid.uuid4().hex}",
            normalized,
            normalized_tags,
            scope,
            key,
            provenance,
            now_ms,
            now_ms,
            expires_at_ms,
            source_session_fingerprint,
        )
        digest = hashlib.sha256(
            json.dumps(
                [normalized, normalized_tags],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_facts WHERE scope=? AND project_key=? AND digest=?",
                (scope, key, digest),
            ).fetchone()
            if row is not None:
                existing = self._fact(row)
                updated_at_ms = max(existing.updated_at_ms, now_ms)
                connection.execute(
                    """UPDATE memory_facts
                       SET provenance=?, source_session_fingerprint=?, updated_at_ms=?,
                           expires_at_ms=? WHERE memory_id=?""",
                    (
                        provenance,
                        source_session_fingerprint,
                        updated_at_ms,
                        expires_at_ms,
                        existing.memory_id,
                    ),
                )
                return MemoryFact(
                    existing.memory_id,
                    normalized,
                    normalized_tags,
                    scope,
                    key,
                    provenance,
                    existing.created_at_ms,
                    updated_at_ms,
                    expires_at_ms,
                    source_session_fingerprint,
                )
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM memory_facts WHERE scope=? AND project_key=?",
                (scope, key),
            ).fetchone()["count"]
            if count >= self.settings.max_facts_per_scope:
                raise MemoryStoreCapacity()
            connection.execute(
                "INSERT INTO memory_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.memory_id,
                    scope,
                    key,
                    normalized,
                    normalized.casefold(),
                    json.dumps(normalized_tags, ensure_ascii=False, separators=(",", ":")),
                    digest,
                    provenance,
                    source_session_fingerprint,
                    now_ms,
                    now_ms,
                    expires_at_ms,
                ),
            )
            return candidate

    def recall(
        self,
        query: str,
        *,
        project_key: str,
        scope: MemoryScope = "project",
        now_ms: int,
        limit: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[MemoryFact, ...]:
        normalized = _normalize_content(query).casefold()
        if len(normalized.encode("utf-8")) > 1024:
            raise ValueError("memory query exceeds 1 KiB")
        key = self._scope_key(scope, project_key)
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("limit must be a positive integer")
        if max_bytes is not None and (
            isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1
        ):
            raise ValueError("max_bytes must be a positive integer")
        resolved_limit = min(
            limit if limit is not None else self.settings.recall_limit,
            self.settings.recall_limit,
        )
        resolved_bytes = min(
            max_bytes if max_bytes is not None else self.settings.recall_bytes,
            self.settings.recall_bytes,
        )
        tokens = tuple(dict.fromkeys(_TOKEN.findall(normalized))) or (normalized,)
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """SELECT * FROM memory_facts
                   WHERE scope=? AND project_key=?
                     AND (expires_at_ms IS NULL OR expires_at_ms>?)""",
                (scope, key, now_ms),
            ).fetchall()
        ranked: list[tuple[int, int, str, MemoryFact]] = []
        for row in rows:
            fact = self._fact(row)
            tag_score = sum(token in fact.tags for token in tokens)
            content_score = sum(token in row["content_folded"] for token in tokens)
            score = tag_score * 100 + content_score
            if score:
                ranked.append((-score, -fact.updated_at_ms, fact.memory_id, fact))
        result: list[MemoryFact] = []
        used = 0
        for _score, _updated, _memory_id, fact in sorted(ranked):
            size = len(fact.content.encode("utf-8"))
            if len(result) >= resolved_limit or used + size > resolved_bytes:
                continue
            result.append(fact)
            used += size
        return tuple(result)

    def get(
        self,
        memory_id: str,
        *,
        project_key: str,
        scope: MemoryScope = "project",
        now_ms: int | None = None,
    ) -> MemoryFact | None:
        key = self._scope_key(scope, project_key)
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT * FROM memory_facts WHERE memory_id=? AND scope=? AND project_key=?",
                (memory_id, scope, key),
            ).fetchone()
        if row is None or (
            now_ms is not None
            and row["expires_at_ms"] is not None
            and row["expires_at_ms"] <= now_ms
        ):
            return None
        return self._fact(row)

    def delete(
        self,
        memory_id: str,
        *,
        project_key: str,
        scope: MemoryScope = "project",
    ) -> bool:
        key = self._scope_key(scope, project_key)
        with self._transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM memory_facts WHERE memory_id=? AND scope=? AND project_key=?",
                (memory_id, scope, key),
            ).rowcount
        return bool(deleted)

    def counts(self, *, project_key: str, now_ms: int) -> dict[str, int]:
        project = _require_project_key(project_key)
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """SELECT scope, project_key, COUNT(*) AS count FROM memory_facts
                   WHERE (expires_at_ms IS NULL OR expires_at_ms>?)
                     AND ((scope='project' AND project_key=?)
                          OR (scope='global' AND project_key=?))
                   GROUP BY scope, project_key""",
                (now_ms, project, _GLOBAL_KEY),
            ).fetchall()
        counts = {"project": 0, "global": 0}
        for row in rows:
            counts[row["scope"]] = row["count"]
        return counts

    @staticmethod
    def _fact(row: sqlite3.Row) -> MemoryFact:
        return MemoryFact(
            row["memory_id"],
            row["content"],
            tuple(json.loads(row["tags_json"])),
            row["scope"],
            row["project_key"],
            row["provenance"],
            row["created_at_ms"],
            row["updated_at_ms"],
            row["expires_at_ms"],
            row["source_session_fingerprint"],
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.DatabaseError:
                pass
            self._connection.close()
            secure_sqlite_files(self.path)


__all__ = [
    "MemoryStore",
    "MemoryStoreCapacity",
    "MemoryStoreError",
    "MemoryStoreUnavailable",
    "project_key_for_path",
]
