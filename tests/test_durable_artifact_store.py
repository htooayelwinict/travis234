from __future__ import annotations

import multiprocessing
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from travis.coding_agent.artifact_store import (
    ArtifactLimits,
    ArtifactMaintenanceLock,
    ArtifactPromotionError,
    DurableArtifactStore,
)


def _hold_lock(lock_path: str, entered: multiprocessing.synchronize.Event) -> None:
    with ArtifactMaintenanceLock(Path(lock_path)):
        entered.set()
        time.sleep(0.4)


def test_identical_concurrent_promotions_publish_one_verified_object(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_bytes(b"same sanitized bytes")
    store = DurableArtifactStore(tmp_path / "agent")

    with ThreadPoolExecutor(max_workers=8) as pool:
        promoted = list(pool.map(lambda _: store.promote(source), range(16)))

    assert len({item.digest for item in promoted}) == 1
    objects = [path for path in store.objects_dir.rglob("*") if path.is_file()]
    assert len(objects) == 1
    assert store.verify(promoted[0].digest).read_bytes() == b"same sanitized bytes"
    assert stat.S_IMODE(store.verify(promoted[0].digest).stat().st_mode) == 0o600
    assert stat.S_IMODE(store.objects_dir.stat().st_mode) == 0o700


def test_promotion_rejects_object_limit_without_partial_file(tmp_path: Path) -> None:
    source = tmp_path / "large.log"
    source.write_bytes(b"x" * 9)
    store = DurableArtifactStore(tmp_path / "agent")

    with pytest.raises(ArtifactPromotionError) as raised:
        store.promote(source, ArtifactLimits(max_object_bytes=8, min_free_bytes=0))

    assert raised.value.code == "object_limit"
    assert not [path for path in (tmp_path / "agent/artifacts").rglob("*") if ".tmp" in path.name]
    assert not [path for path in store.objects_dir.rglob("*") if path.is_file()]


def test_distinct_promotions_cannot_exceed_physical_quota(tmp_path: Path) -> None:
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_bytes(b"a" * 8)
    second.write_bytes(b"b" * 8)
    store = DurableArtifactStore(tmp_path / "agent")
    limits = ArtifactLimits(max_physical_bytes=8, min_free_bytes=0)

    def promote(path: Path) -> str:
        try:
            return store.promote(path, limits).digest
        except ArtifactPromotionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = set(pool.map(promote, (first, second)))

    assert "physical_limit" in results
    assert len([item for item in results if len(item) == 64]) == 1
    assert store.physical_bytes() == 8


def test_promotion_rejects_symlink_source_without_creating_object(tmp_path: Path) -> None:
    target = tmp_path / "target.log"
    target.write_text("secret", encoding="utf-8")
    source = tmp_path / "source.log"
    source.symlink_to(target)
    store = DurableArtifactStore(tmp_path / "agent")

    with pytest.raises(ArtifactPromotionError) as raised:
        store.promote(source, ArtifactLimits(min_free_bytes=0))

    assert raised.value.code == "invalid_source"
    assert not [path for path in store.objects_dir.rglob("*") if path.is_file()]


def test_verify_rejects_a_corrupted_existing_object(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_text("original", encoding="utf-8")
    store = DurableArtifactStore(tmp_path / "agent")
    promoted = store.promote(source, ArtifactLimits(min_free_bytes=0))
    object_path = store.verify(promoted.digest)
    object_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(ArtifactPromotionError) as raised:
        store.verify(promoted.digest)

    assert raised.value.code == "integrity_error"


def test_maintenance_lock_is_reentrant_and_excludes_another_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "agent/artifacts/.lock"
    lock = ArtifactMaintenanceLock(lock_path)
    with lock:
        with lock:
            pass

    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_hold_lock, args=(str(lock_path), entered))
    process.start()
    assert entered.wait(timeout=5)

    started = time.monotonic()
    with lock:
        elapsed = time.monotonic() - started
    process.join(timeout=5)

    assert process.exitcode == 0
    assert elapsed >= 0.25
