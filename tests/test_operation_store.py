from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

from travis.coding_agent.operations import OperationRegister
from travis.coding_agent.operations.store import (
    OperationStore,
    OperationStoreCapacity,
    OperationStoreConflict,
    OperationStoreUnavailable,
)


RUNTIME_ID = "a" * 32
SESSION_FP = "b" * 64
EFFECT_FP = "c" * 64
SOURCE_KEY = "d" * 64


def _store(tmp_path: Path, **kwargs) -> OperationStore:
    return OperationStore(tmp_path / "operations.sqlite3", **kwargs)


def _runtime_and_operation(store: OperationStore):
    lease = store.open_runtime(RUNTIME_ID, 123, 1.5, 10)
    operation = store.create_operation(RUNTIME_ID, SESSION_FP, "turn", 11)
    return lease, operation


def test_fresh_schema_wal_permissions_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    _runtime_and_operation(store)

    connection = sqlite3.connect(path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    version = connection.execute(
        "SELECT value FROM store_meta WHERE key='schema_version'"
    ).fetchone()[0]
    connection.close()

    assert version == "1"
    assert {
        "store_meta",
        "runtime_leases",
        "operations",
        "registers",
        "effects",
        "usage_ledger",
    }.issubset(tables)
    assert path.stat().st_mode & 0o777 == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            assert sidecar.stat().st_mode & 0o777 == 0o600
    store.close()

    reopened = OperationStore(path)
    assert len(reopened.list_operations()) == 1
    reopened.close()


def test_runtime_lease_heartbeat_and_normal_close(tmp_path: Path) -> None:
    store = _store(tmp_path)
    opened = store.open_runtime(RUNTIME_ID, 123, 1.5, 10)
    heartbeat = store.heartbeat_runtime(RUNTIME_ID, 20)
    closed = store.close_runtime(RUNTIME_ID, 30)

    assert opened.heartbeat_at_ms == 10
    assert heartbeat.heartbeat_at_ms == 20
    assert closed.closed_at_ms == 30
    store.close()


def test_operation_counter_registers_effects_usage_and_snapshot_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _lease, operation = _runtime_and_operation(store)
    advanced = store.advance(
        operation.operation_id,
        "provider_intent",
        {"attempt": 1, "auth_token": "DO_NOT_STORE"},
        12,
    )
    register = store.set_register(operation.operation_id, "role", "worker", 13)
    effect = store.begin_effect(
        operation.operation_id, "provider", "openrouter", EFFECT_FP, 14
    )
    settled_effect = store.settle_effect(effect.effect_id, "settled", "ok", 15)
    usage = store.record_usage(
        operation.operation_id,
        SOURCE_KEY,
        "openrouter",
        "model",
        10,
        4,
        2,
        0,
        0.25,
        16,
    )
    settled = store.settle_operation(operation.operation_id, "settled", "ok", 17)
    snapshot = store.snapshot(operation.operation_id)

    assert advanced.program_counter == 1
    assert register.program_counter == 1
    assert settled_effect.state == "settled"
    assert usage.input_tokens == 10
    assert settled.state == "settled"
    assert snapshot is not None
    assert snapshot.operation == settled
    assert [item.key for item in snapshot.registers] == ["attempt", "auth_token", "phase", "role"]
    assert next(item for item in snapshot.registers if item.key == "auth_token").as_dict()["value"] == "[redacted]"
    assert snapshot.effects == (settled_effect,)
    assert snapshot.usage == (usage,)
    store.close()


def test_advance_rolls_back_counter_when_register_is_invalid(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _lease, operation = _runtime_and_operation(store)

    with pytest.raises(TypeError):
        store.advance(operation.operation_id, "provider_intent", {"bad": {1}}, 12)

    assert store.snapshot(operation.operation_id).operation.program_counter == 0
    store.close()


def test_effect_and_operation_settlement_are_idempotent_but_conflicts_fail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _lease, operation = _runtime_and_operation(store)
    effect = store.begin_effect(operation.operation_id, "tool", "read", EFFECT_FP, 12)

    first = store.settle_effect(effect.effect_id, "settled", "ok", 13)
    assert store.settle_effect(effect.effect_id, "settled", "ok", 14) == first
    with pytest.raises(OperationStoreConflict):
        store.settle_effect(effect.effect_id, "failed", "tool_error", 14)
    settled = store.settle_operation(operation.operation_id, "failed", "turn_error", 15)
    assert store.settle_operation(operation.operation_id, "failed", "turn_error", 16) == settled
    with pytest.raises(OperationStoreConflict):
        store.settle_operation(operation.operation_id, "settled", "ok", 16)
    store.close()


def test_effect_register_and_usage_caps_are_enforced(tmp_path: Path) -> None:
    store = _store(tmp_path, max_effects_per_operation=1, max_registers_per_operation=2)
    _lease, operation = _runtime_and_operation(store)
    store.set_register(operation.operation_id, "one", 1, 12)
    store.set_register(operation.operation_id, "two", 2, 12)
    with pytest.raises(OperationStoreCapacity, match="journal_capacity"):
        store.set_register(operation.operation_id, "three", 3, 12)
    store.begin_effect(operation.operation_id, "tool", "read", EFFECT_FP, 13)
    with pytest.raises(OperationStoreCapacity, match="journal_capacity"):
        store.begin_effect(operation.operation_id, "tool", "grep", "e" * 64, 13)
    store.record_usage(operation.operation_id, SOURCE_KEY, "p", "m", 1, 2, 0, 0, 0, 14)
    assert store.record_usage(
        operation.operation_id, SOURCE_KEY, "p", "m", 1, 2, 0, 0, 0, 15
    ).created_at_ms == 14
    with pytest.raises(OperationStoreConflict):
        store.record_usage(operation.operation_id, SOURCE_KEY, "p", "m", 9, 2, 0, 0, 0, 15)
    store.close()


def test_concurrent_writers_get_unique_effect_ordinals(tmp_path: Path) -> None:
    path = tmp_path / "operations.sqlite3"
    first = OperationStore(path)
    _lease, operation = _runtime_and_operation(first)
    second = OperationStore(path)
    ordinals: list[int] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def write(store: OperationStore, fingerprint: str) -> None:
        try:
            result = store.begin_effect(
                operation.operation_id, "tool", "read", fingerprint, 20
            )
            with lock:
                ordinals.append(result.ordinal)
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [
        threading.Thread(target=write, args=(first, "1" * 64)),
        threading.Thread(target=write, args=(second, "2" * 64)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(ordinals) == [1, 2]
    first.close()
    second.close()


def test_database_capacity_refuses_new_writes_without_pruning(tmp_path: Path) -> None:
    store = _store(tmp_path, max_bytes=1)

    with pytest.raises(OperationStoreCapacity, match="journal_capacity"):
        store.open_runtime(RUNTIME_ID, 123, 1.5, 10)

    store.close()


def test_disk_full_write_is_shaped_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)

    def fail_capacity_check() -> None:
        raise sqlite3.OperationalError("database or disk is full: PRIVATE_PATH")

    monkeypatch.setattr(store, "_check_capacity", fail_capacity_check)

    with pytest.raises(OperationStoreUnavailable, match="^journal_unavailable$"):
        store.open_runtime(RUNTIME_ID, 123, 1.5, 10)

    store.close()


def test_locked_database_timeout_is_shaped_as_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path, busy_timeout_ms=1)
    blocker = sqlite3.connect(path, timeout=0)
    blocker.execute("PRAGMA journal_mode=WAL")
    blocker.execute("BEGIN IMMEDIATE")

    try:
        with pytest.raises(OperationStoreUnavailable, match="^journal_unavailable$"):
            store.open_runtime(RUNTIME_ID, 123, 1.5, 10)
    finally:
        blocker.rollback()
        blocker.close()
        store.close()


def test_explicit_pruning_keeps_running_and_uncertain_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.open_runtime(RUNTIME_ID, 123, 1.5, 1)
    settled = store.create_operation(RUNTIME_ID, SESSION_FP, "turn", 2)
    running = store.create_operation(RUNTIME_ID, "1" * 64, "turn", 2)
    uncertain = store.create_operation(RUNTIME_ID, "2" * 64, "turn", 2)
    store.settle_operation(settled.operation_id, "settled", "ok", 3)
    store.settle_operation(uncertain.operation_id, "uncertain", "runtime_lost", 3)

    counts = store.prune_settled_before(10)

    assert counts["operations"] == 1
    assert store.snapshot(settled.operation_id) is None
    assert store.snapshot(running.operation_id) is not None
    assert store.snapshot(uncertain.operation_id) is not None
    assert [item.operation.operation_id for item in store.list_uncertain()] == [
        uncertain.operation_id
    ]
    store.close()


def test_corrupt_or_incompatible_store_is_not_rebuilt(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")
    before = corrupt.read_bytes()
    with pytest.raises(OperationStoreUnavailable, match="journal_unavailable"):
        OperationStore(corrupt)
    assert corrupt.read_bytes() == before

    incompatible = tmp_path / "incompatible.sqlite3"
    connection = sqlite3.connect(incompatible)
    connection.execute("CREATE TABLE store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO store_meta VALUES ('schema_version', '99')")
    connection.commit()
    connection.close()
    with pytest.raises(OperationStoreUnavailable, match="journal_unavailable"):
        OperationStore(incompatible)
    assert incompatible.exists()


def test_read_only_directory_open_is_shaped_without_exposing_path(tmp_path: Path) -> None:
    directory = tmp_path / "read-only-private-directory"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        with pytest.raises(OperationStoreUnavailable, match="^journal_unavailable$"):
            OperationStore(directory / "operations.sqlite3")
    finally:
        directory.chmod(0o700)


def test_close_checkpoints_wal_without_deleting_database(tmp_path: Path) -> None:
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    _runtime_and_operation(store)
    store.close()

    assert path.is_file()
    assert not Path(str(path) + "-wal").exists()
