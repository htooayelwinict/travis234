"""Schema identity checks for the operation journal."""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 1
REQUIRED_COLUMNS = {
    "store_meta": frozenset({"key", "value"}),
    "runtime_leases": frozenset(
        {
            "runtime_id",
            "pid",
            "process_create_time",
            "started_at_ms",
            "heartbeat_at_ms",
            "closed_at_ms",
        }
    ),
    "operations": frozenset(
        {
            "operation_id",
            "runtime_id",
            "session_fingerprint",
            "kind",
            "state",
            "program_counter",
            "created_at_ms",
            "updated_at_ms",
            "settled_at_ms",
            "outcome_code",
        }
    ),
    "registers": frozenset(
        {
            "operation_id",
            "key",
            "value_json",
            "program_counter",
            "updated_at_ms",
        }
    ),
    "effects": frozenset(
        {
            "effect_id",
            "operation_id",
            "runtime_id",
            "ordinal",
            "kind",
            "name",
            "effect_classes_json",
            "fingerprint",
            "state",
            "replay_policy",
            "created_at_ms",
            "settled_at_ms",
            "outcome_code",
        }
    ),
    "usage_ledger": frozenset(
        {
            "operation_id",
            "source_key",
            "provider",
            "model",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "cost",
            "created_at_ms",
        }
    ),
}


def has_required_schema(connection: sqlite3.Connection) -> bool:
    for table, expected in REQUIRED_COLUMNS.items():
        actual = {
            row["name"]
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if actual != expected:
            return False
    return True


__all__ = ["REQUIRED_COLUMNS", "SCHEMA_VERSION", "has_required_schema"]
