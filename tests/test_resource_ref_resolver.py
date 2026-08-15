from __future__ import annotations

import os
from pathlib import Path

import pytest

from travis.coding_agent.artifact_manifest import ArtifactManifest
from travis.coding_agent.artifact_store import ArtifactLimits, DurableArtifactStore
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.resource_refs import ResourceRefResolver


def _durable_registry(tmp_path: Path) -> tuple[ArtifactRegistry, ArtifactManifest, DurableArtifactStore]:
    store = DurableArtifactStore(tmp_path / "agent")
    manifest = ArtifactManifest.for_session(
        tmp_path / "session.jsonl",
        limits=ArtifactLimits(min_free_bytes=0),
    )
    return ArtifactRegistry(durable_store=store, manifest=manifest), manifest, store


def test_registry_promotes_for_durable_session_and_survives_close(tmp_path: Path) -> None:
    source = tmp_path / "complete.log"
    source.write_text("complete", encoding="utf-8")
    registry, manifest, store = _durable_registry(tmp_path)

    ref = registry.promote(source, "command-output", tool_call_id="call-1")
    registry.close(remove_files=True)

    reopened = ArtifactRegistry(
        durable_store=store,
        manifest=ArtifactManifest.for_session(
            tmp_path / "session.jsonl",
            limits=ArtifactLimits(min_free_bytes=0),
        ),
    )
    resolved = reopened.resolve_read(ref.id)
    assert resolved is not None
    assert resolved.read_text(encoding="utf-8") == "complete"


def test_foreign_manifest_cannot_resolve_object_digest_or_host_path(tmp_path: Path) -> None:
    registry, manifest, store = _durable_registry(tmp_path)
    source = tmp_path / "complete.log"
    source.write_text("authorized", encoding="utf-8")
    ref = registry.promote(source, "command-output")
    entry = manifest.get(ref.id)
    assert entry is not None
    resolver = ResourceRefResolver(store, manifest)

    digest_result = resolver.resolve_read(entry.digest, byte_offset=0, byte_limit=10)
    path_result = resolver.resolve_read(str(store.objects_dir), byte_offset=0, byte_limit=10)

    assert digest_result.available is False
    assert digest_result.error_code == "unauthorized"
    assert path_result.available is False
    assert path_result.error_code == "unauthorized"


def test_resolver_returns_bounded_pages_with_exact_cursor(tmp_path: Path) -> None:
    registry, manifest, store = _durable_registry(tmp_path)
    source = tmp_path / "complete.log"
    source.write_bytes(b"0123456789")
    ref = registry.promote(source, "command-output")
    resolver = ResourceRefResolver(store, manifest)

    first = resolver.resolve_read(ref.id, byte_offset=0, byte_limit=4)
    final = resolver.resolve_read(ref.id, byte_offset=first.next_offset or 0, byte_limit=10)

    assert first.available is True
    assert first.content == b"0123"
    assert first.next_offset == 4
    assert first.total_bytes == 10
    assert final.content == b"456789"
    assert final.next_offset is None


def test_resolver_reverifies_when_object_metadata_changes(tmp_path: Path) -> None:
    registry, manifest, store = _durable_registry(tmp_path)
    source = tmp_path / "complete.log"
    source.write_bytes(b"original")
    ref = registry.promote(source, "command-output")
    entry = manifest.get(ref.id)
    assert entry is not None
    resolver = ResourceRefResolver(store, manifest)
    assert resolver.resolve_read(ref.id, byte_offset=0, byte_limit=8).available is True
    object_path = store.verify(entry.digest)
    object_path.write_bytes(b"tampered")
    os.utime(object_path, None)

    changed = resolver.resolve_read(ref.id, byte_offset=0, byte_limit=8)

    assert changed.available is False
    assert changed.error_code == "integrity_error"


def test_resolver_rejects_object_replaced_by_symlink_after_first_read(tmp_path: Path) -> None:
    registry, manifest, store = _durable_registry(tmp_path)
    source = tmp_path / "complete.log"
    source.write_text("authorized", encoding="utf-8")
    ref = registry.promote(source, "command-output")
    entry = manifest.get(ref.id)
    assert entry is not None
    resolver = ResourceRefResolver(store, manifest)
    assert resolver.resolve_read(ref.id, byte_offset=0, byte_limit=10).available is True
    object_path = store.verify(entry.digest)
    object_path.unlink()
    object_path.symlink_to(source)

    replaced = resolver.resolve_read(ref.id, byte_offset=0, byte_limit=10)

    assert replaced.available is False
    assert replaced.error_code == "integrity_error"


@pytest.mark.parametrize("byte_limit", [0, 50 * 1024 + 1])
def test_resolver_rejects_unbounded_byte_limits(tmp_path: Path, byte_limit: int) -> None:
    registry, manifest, store = _durable_registry(tmp_path)
    source = tmp_path / "complete.log"
    source.write_text("content", encoding="utf-8")
    ref = registry.promote(source, "command-output")

    result = ResourceRefResolver(store, manifest).resolve_read(
        ref.id,
        byte_offset=0,
        byte_limit=byte_limit,
    )

    assert result.available is False
    assert result.error_code == "invalid_range"


def test_close_removes_only_ephemeral_sources_and_keeps_durable_object(tmp_path: Path) -> None:
    registry, manifest, store = _durable_registry(tmp_path)
    ephemeral = tmp_path / "ephemeral.log"
    durable = tmp_path / "durable.log"
    ephemeral.write_text("temporary", encoding="utf-8")
    durable.write_text("retained", encoding="utf-8")
    registry.register(ephemeral, "command-output")
    durable_ref = registry.promote(durable, "command-output", retained=True)
    entry = manifest.get(durable_ref.id)
    assert entry is not None

    registry.close(remove_files=True)
    registry.close(remove_files=True)

    assert not ephemeral.exists()
    assert store.verify(entry.digest).read_text(encoding="utf-8") == "retained"
