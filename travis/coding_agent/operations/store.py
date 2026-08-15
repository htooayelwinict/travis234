"""Durable SQLite intent/effect/settlement storage."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from travis.coding_agent.operations.types import (
    EffectRecord,
    EffectState,
    OperationRecord,
    OperationRegister,
    OperationSnapshot,
    OperationState,
    RuntimeLease,
    UsageLedgerEntry,
    validate_effect_transition,
    validate_operation_transition,
)
from travis.coding_agent.operations.rows import (
    effect_from_row,
    operation_from_row,
    register_from_row,
    runtime_from_row,
    usage_from_row,
    usage_identity,
)
from travis.coding_agent.sqlite_utils import open_secure_sqlite, secure_sqlite_files

_SCHEMA_VERSION = "1"
_TERMINAL_OPERATION_STATES = ("settled", "failed", "cancelled")


class OperationStoreError(RuntimeError):
    code = "journal_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class OperationStoreUnavailable(OperationStoreError):
    code = "journal_unavailable"


class OperationStoreCapacity(OperationStoreError):
    code = "journal_capacity"


class OperationStoreConflict(OperationStoreError):
    code = "journal_conflict"


class OperationStore:
    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 1024 * 1024 * 1024,
        max_registers_per_operation: int = 128,
        max_effects_per_operation: int = 10_000,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        for value, label in (
            (max_bytes, "max_bytes"),
            (max_registers_per_operation, "max_registers_per_operation"),
            (max_effects_per_operation, "max_effects_per_operation"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{label} must be a positive integer")
        self.max_bytes = max_bytes
        self.max_registers_per_operation = max_registers_per_operation
        self.max_effects_per_operation = max_effects_per_operation
        self._lock = threading.RLock()
        self._closed = False
        connection: sqlite3.Connection | None = None
        try:
            self.path, connection = open_secure_sqlite(
                path, busy_timeout_ms=busy_timeout_ms
            )
            self._connection = connection
            self._initialize_schema()
        except (OSError, sqlite3.DatabaseError, OperationStoreUnavailable):
            if connection is not None:
                connection.close()
            raise OperationStoreUnavailable() from None

    def _initialize_schema(self) -> None:
        tables = {
            row["name"]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "store_meta" in tables:
            row = self._connection.execute(
                "SELECT value FROM store_meta WHERE key='schema_version'"
            ).fetchone()
            required = {
                "store_meta",
                "runtime_leases",
                "operations",
                "registers",
                "effects",
                "usage_ledger",
            }
            if row is None or row["value"] != _SCHEMA_VERSION or not required.issubset(tables):
                raise OperationStoreUnavailable()
            return
        if tables:
            raise OperationStoreUnavailable()
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO store_meta (key, value) VALUES ('schema_version', '1');
            CREATE TABLE runtime_leases (
                runtime_id TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                process_create_time REAL NOT NULL,
                started_at_ms INTEGER NOT NULL,
                heartbeat_at_ms INTEGER NOT NULL,
                closed_at_ms INTEGER
            );
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                runtime_id TEXT NOT NULL REFERENCES runtime_leases(runtime_id),
                session_fingerprint TEXT NOT NULL,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                program_counter INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                settled_at_ms INTEGER,
                outcome_code TEXT
            );
            CREATE INDEX operations_session_idx ON operations(session_fingerprint, created_at_ms);
            CREATE INDEX operations_state_idx ON operations(state, updated_at_ms);
            CREATE INDEX operations_runtime_idx ON operations(runtime_id, state);
            CREATE TABLE registers (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                program_counter INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, key)
            );
            CREATE TABLE effects (
                effect_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
                runtime_id TEXT NOT NULL REFERENCES runtime_leases(runtime_id),
                ordinal INTEGER NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                replay_policy TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                settled_at_ms INTEGER,
                outcome_code TEXT,
                UNIQUE (operation_id, ordinal)
            );
            CREATE INDEX effects_operation_idx ON effects(operation_id, ordinal);
            CREATE INDEX effects_state_idx ON effects(state, created_at_ms);
            CREATE TABLE usage_ledger (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
                source_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                cache_write_tokens INTEGER NOT NULL,
                cost REAL NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, source_key)
            );
            CREATE INDEX usage_created_idx ON usage_ledger(created_at_ms);
            COMMIT;
            """
        )
        secure_sqlite_files(self.path)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._check_capacity()
                yield self._connection
                self._connection.commit()
                secure_sqlite_files(self.path)
            except OperationStoreError:
                self._connection.rollback()
                raise
            except sqlite3.DatabaseError:
                self._connection.rollback()
                raise OperationStoreUnavailable() from None
            except Exception:
                self._connection.rollback()
                raise

    def _require_open(self) -> None:
        if self._closed:
            raise OperationStoreUnavailable()

    def _check_capacity(self) -> None:
        footprint = sum(
            candidate.stat().st_size
            for candidate in (
                self.path,
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            )
            if candidate.exists()
        )
        if footprint >= self.max_bytes:
            raise OperationStoreCapacity()

    def open_runtime(
        self,
        runtime_id: str,
        pid: int,
        process_create_time: float,
        now_ms: int,
    ) -> RuntimeLease:
        candidate = RuntimeLease(runtime_id, pid, process_create_time, now_ms, now_ms)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_leases WHERE runtime_id=?", (runtime_id,)
            ).fetchone()
            if row is not None:
                existing = runtime_from_row(row)
                if (
                    existing.pid != pid
                    or existing.process_create_time != process_create_time
                    or existing.closed_at_ms is not None
                ):
                    raise OperationStoreConflict()
                return existing
            connection.execute(
                "INSERT INTO runtime_leases VALUES (?, ?, ?, ?, ?, NULL)",
                (runtime_id, pid, process_create_time, now_ms, now_ms),
            )
        return candidate

    def heartbeat_runtime(self, runtime_id: str, now_ms: int) -> RuntimeLease:
        with self._transaction() as connection:
            row = self._runtime_row(connection, runtime_id)
            current = runtime_from_row(row)
            if current.closed_at_ms is not None or now_ms < current.heartbeat_at_ms:
                raise OperationStoreConflict()
            connection.execute(
                "UPDATE runtime_leases SET heartbeat_at_ms=? WHERE runtime_id=?",
                (now_ms, runtime_id),
            )
            return RuntimeLease(
                current.runtime_id,
                current.pid,
                current.process_create_time,
                current.started_at_ms,
                now_ms,
                current.closed_at_ms,
            )

    def close_runtime(self, runtime_id: str, now_ms: int) -> RuntimeLease:
        with self._transaction() as connection:
            current = runtime_from_row(self._runtime_row(connection, runtime_id))
            if current.closed_at_ms is not None:
                return current
            if now_ms < current.heartbeat_at_ms:
                raise OperationStoreConflict()
            connection.execute(
                "UPDATE runtime_leases SET heartbeat_at_ms=?, closed_at_ms=? WHERE runtime_id=?",
                (now_ms, now_ms, runtime_id),
            )
            return RuntimeLease(
                current.runtime_id,
                current.pid,
                current.process_create_time,
                current.started_at_ms,
                now_ms,
                now_ms,
            )

    def create_operation(
        self,
        runtime_id: str,
        session_fingerprint: str,
        kind: str,
        now_ms: int,
    ) -> OperationRecord:
        operation = OperationRecord(
            "op_" + uuid.uuid4().hex,
            runtime_id,
            session_fingerprint,
            kind,
            "running",
            0,
            now_ms,
            now_ms,
        )
        with self._transaction() as connection:
            self._runtime_row(connection, runtime_id)
            connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    operation.operation_id,
                    runtime_id,
                    session_fingerprint,
                    kind,
                    "running",
                    0,
                    now_ms,
                    now_ms,
                ),
            )
        return operation

    def advance(
        self,
        operation_id: str,
        phase: str,
        registers: Mapping[str, object] | None,
        now_ms: int,
    ) -> OperationRecord:
        pending = {**dict(registers or {}), "phase": phase}
        for key, value in pending.items():
            OperationRegister(operation_id, key, value, 0, now_ms)
        with self._transaction() as connection:
            current = operation_from_row(self._operation_row(connection, operation_id))
            if current.state != "running" or now_ms < current.updated_at_ms:
                raise OperationStoreConflict()
            counter = current.program_counter + 1
            existing = {
                row["key"]
                for row in connection.execute(
                    "SELECT key FROM registers WHERE operation_id=?", (operation_id,)
                )
            }
            if len(existing.union(pending)) > self.max_registers_per_operation:
                raise OperationStoreCapacity()
            for key, value in pending.items():
                item = OperationRegister(operation_id, key, value, counter, now_ms)
                self._upsert_register(connection, item)
            connection.execute(
                "UPDATE operations SET program_counter=?, updated_at_ms=? WHERE operation_id=?",
                (counter, now_ms, operation_id),
            )
            return OperationRecord(
                current.operation_id,
                current.runtime_id,
                current.session_fingerprint,
                current.kind,
                current.state,
                counter,
                current.created_at_ms,
                now_ms,
                current.settled_at_ms,
                current.outcome_code,
            )

    def set_register(
        self, operation_id: str, key: str, value: object, now_ms: int
    ) -> OperationRegister:
        with self._transaction() as connection:
            operation = operation_from_row(self._operation_row(connection, operation_id))
            existing = connection.execute(
                "SELECT 1 FROM registers WHERE operation_id=? AND key=?",
                (operation_id, key),
            ).fetchone()
            if existing is None:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM registers WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()["count"]
                if count >= self.max_registers_per_operation:
                    raise OperationStoreCapacity()
            item = OperationRegister(
                operation_id, key, value, operation.program_counter, now_ms
            )
            self._upsert_register(connection, item)
            return item

    @staticmethod
    def _upsert_register(
        connection: sqlite3.Connection, register: OperationRegister
    ) -> None:
        connection.execute(
            """INSERT INTO registers VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(operation_id, key) DO UPDATE SET
               value_json=excluded.value_json,
               program_counter=excluded.program_counter,
               updated_at_ms=excluded.updated_at_ms""",
            (
                register.operation_id,
                register.key,
                json.dumps(
                    register.as_dict()["value"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                register.program_counter,
                register.updated_at_ms,
            ),
        )

    def begin_effect(
        self,
        operation_id: str,
        kind: str,
        name: str,
        fingerprint: str,
        now_ms: int,
    ) -> EffectRecord:
        with self._transaction() as connection:
            operation = operation_from_row(self._operation_row(connection, operation_id))
            if operation.state != "running":
                raise OperationStoreConflict()
            count = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(MAX(ordinal), 0) AS ordinal FROM effects WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if count["count"] >= self.max_effects_per_operation:
                raise OperationStoreCapacity()
            effect = EffectRecord(
                "effect_" + uuid.uuid4().hex,
                operation_id,
                operation.runtime_id,
                count["ordinal"] + 1,
                kind,
                name,
                fingerprint,
                "intent",
                "never",
                now_ms,
            )
            connection.execute(
                "INSERT INTO effects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    effect.effect_id,
                    effect.operation_id,
                    effect.runtime_id,
                    effect.ordinal,
                    effect.kind,
                    effect.name,
                    effect.fingerprint,
                    effect.state,
                    effect.replay_policy,
                    effect.created_at_ms,
                ),
            )
            return effect

    def settle_effect(
        self,
        effect_id: str,
        state: EffectState,
        outcome_code: str,
        now_ms: int,
    ) -> EffectRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            if row is None:
                raise OperationStoreConflict()
            current = effect_from_row(row)
            if current.state != "intent":
                if current.state == state and current.outcome_code == outcome_code:
                    return current
                raise OperationStoreConflict()
            validate_effect_transition(current.state, state)
            settled = EffectRecord(
                current.effect_id,
                current.operation_id,
                current.runtime_id,
                current.ordinal,
                current.kind,
                current.name,
                current.fingerprint,
                state,
                current.replay_policy,
                current.created_at_ms,
                now_ms,
                outcome_code,
            )
            connection.execute(
                "UPDATE effects SET state=?, settled_at_ms=?, outcome_code=? WHERE effect_id=?",
                (state, now_ms, outcome_code, effect_id),
            )
            return settled

    def record_usage(
        self,
        operation_id: str,
        source_key: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost: float,
        now_ms: int,
    ) -> UsageLedgerEntry:
        candidate = UsageLedgerEntry(
            operation_id,
            source_key,
            provider,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            cost,
            now_ms,
        )
        with self._transaction() as connection:
            self._operation_row(connection, operation_id)
            row = connection.execute(
                "SELECT * FROM usage_ledger WHERE operation_id=? AND source_key=?",
                (operation_id, source_key),
            ).fetchone()
            if row is not None:
                existing = usage_from_row(row)
                if usage_identity(existing) == usage_identity(candidate):
                    return existing
                raise OperationStoreConflict()
            connection.execute(
                "INSERT INTO usage_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    source_key,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    cost,
                    now_ms,
                ),
            )
            return candidate

    def settle_operation(
        self,
        operation_id: str,
        state: OperationState,
        outcome_code: str,
        now_ms: int,
    ) -> OperationRecord:
        with self._transaction() as connection:
            current = operation_from_row(self._operation_row(connection, operation_id))
            if current.state != "running":
                if current.state == state and current.outcome_code == outcome_code:
                    return current
                raise OperationStoreConflict()
            validate_operation_transition(current.state, state)
            settled = OperationRecord(
                current.operation_id,
                current.runtime_id,
                current.session_fingerprint,
                current.kind,
                state,
                current.program_counter,
                current.created_at_ms,
                now_ms,
                now_ms,
                outcome_code,
            )
            connection.execute(
                "UPDATE operations SET state=?, updated_at_ms=?, settled_at_ms=?, outcome_code=? WHERE operation_id=?",
                (state, now_ms, now_ms, outcome_code, operation_id),
            )
            return settled

    def snapshot(self, operation_id: str) -> OperationSnapshot | None:
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                return None
            return self._snapshot_from_row(row)

    def _snapshot_from_row(self, row: sqlite3.Row) -> OperationSnapshot:
        operation_id = row["operation_id"]
        registers = tuple(
            register_from_row(item)
            for item in self._connection.execute(
                "SELECT * FROM registers WHERE operation_id=? ORDER BY key",
                (operation_id,),
            )
        )
        effects = tuple(
            effect_from_row(item)
            for item in self._connection.execute(
                "SELECT * FROM effects WHERE operation_id=? ORDER BY ordinal",
                (operation_id,),
            )
        )
        usage = tuple(
            usage_from_row(item)
            for item in self._connection.execute(
                "SELECT * FROM usage_ledger WHERE operation_id=? ORDER BY source_key",
                (operation_id,),
            )
        )
        return OperationSnapshot(operation_from_row(row), registers, effects, usage)

    def list_operations(self) -> tuple[OperationSnapshot, ...]:
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                "SELECT * FROM operations ORDER BY created_at_ms, operation_id"
            ).fetchall()
            return tuple(self._snapshot_from_row(row) for row in rows)

    def list_uncertain(
        self, session_fingerprint: str | None = None
    ) -> tuple[OperationSnapshot, ...]:
        sql = "SELECT * FROM operations WHERE state='uncertain'"
        parameters: tuple[str, ...] = ()
        if session_fingerprint is not None:
            sql += " AND session_fingerprint=?"
            parameters = (session_fingerprint,)
        sql += " ORDER BY created_at_ms, operation_id"
        with self._lock:
            self._require_open()
            rows = self._connection.execute(sql, parameters).fetchall()
            return tuple(self._snapshot_from_row(row) for row in rows)

    def prune_settled_before(self, cutoff_ms: int) -> dict[str, int]:
        if isinstance(cutoff_ms, bool) or not isinstance(cutoff_ms, int) or cutoff_ms < 0:
            raise ValueError("cutoff_ms must be a non-negative integer")
        with self._transaction() as connection:
            ids = tuple(
                row["operation_id"]
                for row in connection.execute(
                    "SELECT operation_id FROM operations WHERE state IN (?, ?, ?) AND settled_at_ms < ?",
                    (*_TERMINAL_OPERATION_STATES, cutoff_ms),
                )
            )
            counts = {"operations": len(ids), "registers": 0, "effects": 0, "usage": 0}
            if not ids:
                return counts
            placeholders = ",".join("?" for _ in ids)
            counts["registers"] = connection.execute(
                f"SELECT COUNT(*) AS count FROM registers WHERE operation_id IN ({placeholders})",
                ids,
            ).fetchone()["count"]
            counts["effects"] = connection.execute(
                f"SELECT COUNT(*) AS count FROM effects WHERE operation_id IN ({placeholders})",
                ids,
            ).fetchone()["count"]
            counts["usage"] = connection.execute(
                f"SELECT COUNT(*) AS count FROM usage_ledger WHERE operation_id IN ({placeholders})",
                ids,
            ).fetchone()["count"]
            connection.execute(
                f"DELETE FROM operations WHERE operation_id IN ({placeholders})", ids
            )
            return counts

    @staticmethod
    def _runtime_row(connection: sqlite3.Connection, runtime_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM runtime_leases WHERE runtime_id=?", (runtime_id,)
        ).fetchone()
        if row is None:
            raise OperationStoreConflict()
        return row

    @staticmethod
    def _operation_row(connection: sqlite3.Connection, operation_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise OperationStoreConflict()
        return row

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._closed = True
                self._connection.close()
                secure_sqlite_files(self.path)


__all__ = [
    "OperationStore",
    "OperationStoreCapacity",
    "OperationStoreConflict",
    "OperationStoreError",
    "OperationStoreUnavailable",
]
