"""Strict SQLite row decoding for operation journal values."""

from __future__ import annotations

import json
import sqlite3

from travis.coding_agent.operations.types import (
    EffectRecord,
    OperationRecord,
    OperationRegister,
    RuntimeLease,
    UsageLedgerEntry,
)


def runtime_from_row(row: sqlite3.Row) -> RuntimeLease:
    return RuntimeLease(
        row["runtime_id"],
        row["pid"],
        row["process_create_time"],
        row["started_at_ms"],
        row["heartbeat_at_ms"],
        row["closed_at_ms"],
    )


def operation_from_row(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        row["operation_id"],
        row["runtime_id"],
        row["session_fingerprint"],
        row["kind"],
        row["state"],
        row["program_counter"],
        row["created_at_ms"],
        row["updated_at_ms"],
        row["settled_at_ms"],
        row["outcome_code"],
    )


def register_from_row(row: sqlite3.Row) -> OperationRegister:
    return OperationRegister(
        row["operation_id"],
        row["key"],
        json.loads(row["value_json"]),
        row["program_counter"],
        row["updated_at_ms"],
    )


def effect_from_row(row: sqlite3.Row) -> EffectRecord:
    return EffectRecord(
        row["effect_id"],
        row["operation_id"],
        row["runtime_id"],
        row["ordinal"],
        row["kind"],
        row["name"],
        row["fingerprint"],
        row["state"],
        row["replay_policy"],
        row["created_at_ms"],
        row["settled_at_ms"],
        row["outcome_code"],
        tuple(json.loads(row["effect_classes_json"])),
    )


def usage_from_row(row: sqlite3.Row) -> UsageLedgerEntry:
    return UsageLedgerEntry(
        row["operation_id"],
        row["source_key"],
        row["provider"],
        row["model"],
        row["input_tokens"],
        row["output_tokens"],
        row["cache_read_tokens"],
        row["cache_write_tokens"],
        row["cost"],
        row["created_at_ms"],
    )


def usage_identity(entry: UsageLedgerEntry) -> tuple[object, ...]:
    return (
        entry.operation_id,
        entry.source_key,
        entry.provider,
        entry.model,
        entry.input_tokens,
        entry.output_tokens,
        entry.cache_read_tokens,
        entry.cache_write_tokens,
        entry.cost,
    )


__all__ = [
    "effect_from_row",
    "operation_from_row",
    "register_from_row",
    "runtime_from_row",
    "usage_from_row",
    "usage_identity",
]
