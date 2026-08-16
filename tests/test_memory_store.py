from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from travis.coding_agent.memory import (
    MemorySettings,
    MemoryStore,
    MemoryStoreCapacity,
    MemoryStoreUnavailable,
    project_key_for_path,
)


PROJECT_A = hashlib.sha256(b"project-a").hexdigest()
PROJECT_B = hashlib.sha256(b"project-b").hexdigest()
SESSION_FP = hashlib.sha256(b"session-private-id").hexdigest()


def _retain(
    store: MemoryStore,
    content: str,
    *,
    project_key: str = PROJECT_A,
    tags: tuple[str, ...] = ("python",),
    scope: str = "project",
    now_ms: int = 100,
    expires_at_ms: int | None = None,
):
    return store.retain(
        content,
        tags=tags,
        scope=scope,
        project_key=project_key,
        provenance="user_requested",
        now_ms=now_ms,
        expires_at_ms=expires_at_ms,
        source_session_fingerprint=SESSION_FP,
    )


def test_retain_reopen_permissions_and_private_identity(tmp_path: Path) -> None:
    canonical = tmp_path / "Private Workspace"
    project_key = project_key_for_path(canonical)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    fact = _retain(store, "Use Python 3.13", project_key=project_key)

    assert fact.memory_id.startswith("mem_") and len(fact.memory_id) == 36
    assert fact.content == "Use Python 3.13"
    assert fact.tags == ("python",)
    assert fact.project_key == project_key
    assert fact.source_session_fingerprint == SESSION_FP
    store.close()

    reopened = MemoryStore(tmp_path / "memory.sqlite3")
    assert reopened.get(fact.memory_id, project_key=project_key) == fact
    assert (tmp_path / "memory.sqlite3").stat().st_mode & 0o777 == 0o600
    raw = (tmp_path / "memory.sqlite3").read_bytes()
    assert str(canonical.resolve()).encode() not in raw
    assert b"session-private-id" not in raw
    reopened.close()


def test_unicode_idempotency_updates_existing_fact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first = _retain(store, "Cafe\u0301", tags=(" Docs ", "PYTHON"), now_ms=100)
    second = _retain(store, "Caf\u00e9", tags=("python", "docs"), now_ms=200)

    assert second.memory_id == first.memory_id
    assert second.content == "Caf\u00e9"
    assert second.tags == ("docs", "python")
    assert second.updated_at_ms == 200
    assert store.counts(project_key=PROJECT_A, now_ms=200) == {
        "project": 1,
        "global": 0,
    }
    store.close()


def test_project_isolation_global_opt_in_expiry_and_exact_delete(tmp_path: Path) -> None:
    settings = MemorySettings(allowed_scopes=("project", "global"))
    store = MemoryStore(tmp_path / "memory.sqlite3", settings=settings)
    visible = _retain(store, "visible alpha", project_key=PROJECT_A, now_ms=10)
    _retain(store, "hidden alpha", project_key=PROJECT_B, now_ms=11)
    expired = _retain(
        store,
        "expired alpha",
        project_key=PROJECT_A,
        now_ms=12,
        expires_at_ms=20,
    )
    global_fact = _retain(
        store,
        "global alpha",
        project_key=PROJECT_A,
        scope="global",
        now_ms=13,
    )

    assert [fact.memory_id for fact in store.recall("alpha", project_key=PROJECT_A, now_ms=30)] == [visible.memory_id]
    assert [fact.memory_id for fact in store.recall("alpha", project_key=PROJECT_A, scope="global", now_ms=30)] == [global_fact.memory_id]
    assert store.get(expired.memory_id, project_key=PROJECT_A, now_ms=30) is None
    assert store.delete(visible.memory_id, project_key=PROJECT_B) is False
    assert store.delete(visible.memory_id, project_key=PROJECT_A) is True
    assert store.delete(visible.memory_id, project_key=PROJECT_A) is False
    store.close()


def test_recall_ranking_limits_and_deterministic_order(tmp_path: Path) -> None:
    settings = MemorySettings(recall_limit=2, recall_bytes=100)
    store = MemoryStore(tmp_path / "memory.sqlite3", settings=settings)
    tag = _retain(store, "unrelated", tags=("alpha",), now_ms=10)
    newer = _retain(store, "alpha content newer", tags=("misc",), now_ms=30)
    _retain(store, "alpha content older", tags=("misc", "old"), now_ms=20)

    result = store.recall("alpha", project_key=PROJECT_A, now_ms=40)

    assert [fact.memory_id for fact in result] == [tag.memory_id, newer.memory_id]
    assert sum(len(fact.content.encode()) for fact in result) <= 100
    with pytest.raises(ValueError, match="1 KiB"):
        store.recall("x" * 1025, project_key=PROJECT_A, now_ms=40)
    store.close()


def test_fact_tag_scope_and_capacity_limits_are_strict(tmp_path: Path) -> None:
    settings = MemorySettings(
        max_fact_bytes=8,
        max_facts_per_scope=1,
        max_total_bytes=1024 * 1024,
    )
    store = MemoryStore(tmp_path / "memory.sqlite3", settings=settings)
    with pytest.raises(ValueError, match="fact exceeds"):
        _retain(store, "123456789")
    with pytest.raises(ValueError, match="16 tags"):
        _retain(store, "short", tags=tuple(f"t{i}" for i in range(17)))
    with pytest.raises(ValueError, match="64 bytes"):
        _retain(store, "short", tags=("x" * 65,))
    _retain(store, "first")
    with pytest.raises(MemoryStoreCapacity):
        _retain(store, "second", now_ms=200)
    with pytest.raises(ValueError, match="global scope"):
        _retain(store, "global", scope="global")
    store.close()


def test_default_fact_limit_uses_utf8_bytes_exactly(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    accepted = _retain(store, "é" * (32 * 1024), tags=())
    assert len(accepted.content.encode("utf-8")) == 64 * 1024
    with pytest.raises(ValueError, match="fact exceeds"):
        _retain(store, "é" * (32 * 1024) + "x", tags=("second",), now_ms=200)
    store.close()


def test_database_capacity_refuses_writes_without_pruning(tmp_path: Path) -> None:
    settings = MemorySettings(max_total_bytes=1)
    store = MemoryStore(tmp_path / "memory.sqlite3", settings=settings)

    with pytest.raises(MemoryStoreCapacity):
        _retain(store, "fact")

    assert store.counts(project_key=PROJECT_A, now_ms=100) == {"project": 0, "global": 0}
    store.close()


def test_concurrent_same_digest_retain_is_one_row(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    ids: list[str] = []

    def retain() -> None:
        ids.append(_retain(store, "same", now_ms=100 + len(ids)).memory_id)

    threads = [threading.Thread(target=retain) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(ids)) == 1
    assert store.counts(project_key=PROJECT_A, now_ms=200)["project"] == 1
    store.close()


def test_separate_connections_serialize_same_digest_and_expiry_is_not_pruned(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    stores = [MemoryStore(path) for _ in range(4)]
    ids: list[str] = []

    def retain(store: MemoryStore, now_ms: int) -> None:
        ids.append(
            _retain(
                store,
                "shared",
                now_ms=now_ms,
                expires_at_ms=500,
            ).memory_id
        )

    threads = [
        threading.Thread(target=retain, args=(store, 100 + index))
        for index, store in enumerate(stores)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(ids)) == 1
    assert stores[0].counts(project_key=PROJECT_A, now_ms=600)["project"] == 0
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0] == 1
    connection.close()
    for store in stores:
        store.close()


def test_recall_rejects_nonpositive_explicit_limits(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    _retain(store, "alpha")

    with pytest.raises(ValueError, match="limit"):
        store.recall("alpha", project_key=PROJECT_A, now_ms=200, limit=0)
    with pytest.raises(ValueError, match="max_bytes"):
        store.recall("alpha", project_key=PROJECT_A, now_ms=200, max_bytes=-1)
    store.close()


def test_corrupt_database_fails_safely_without_replacement(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    before = b"not sqlite private bytes"
    path.write_bytes(before)

    with pytest.raises(MemoryStoreUnavailable):
        MemoryStore(path)

    assert path.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_read_only_parent_failure_is_sanitized(tmp_path: Path) -> None:
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        if os.access(parent, os.W_OK):
            pytest.skip("effective user can write read-only directories")
        with pytest.raises(MemoryStoreUnavailable):
            MemoryStore(parent / "memory.sqlite3")
    finally:
        parent.chmod(0o700)
