from __future__ import annotations

from pathlib import Path

from travis.coding_agent.artifact_gc import ArtifactGarbageCollector
from travis.coding_agent.artifact_manifest import ArtifactManifest
from travis.coding_agent.artifact_store import ArtifactLimits, DurableArtifactStore
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore


def _setup(tmp_path: Path):
    sessions = tmp_path / "sessions"
    session_path = sessions / "session.jsonl"
    SessionStore(str(session_path), cwd=str(tmp_path))
    store = DurableArtifactStore(tmp_path / "agent")
    manifest = ArtifactManifest.for_session(
        session_path,
        limits=ArtifactLimits(min_free_bytes=0),
    )
    registry = ArtifactRegistry(durable_store=store, manifest=manifest)
    referenced_source = tmp_path / "referenced.log"
    orphan_source = tmp_path / "orphan.log"
    referenced_source.write_text("referenced", encoding="utf-8")
    orphan_source.write_text("orphan", encoding="utf-8")
    referenced = registry.promote(referenced_source, "command-output", retained=True)
    orphan = store.promote(orphan_source, ArtifactLimits(min_free_bytes=0))
    catalog = SessionCatalog(str(tmp_path / "agent"), session_dir=str(sessions))
    return store, manifest, registry, referenced, orphan, catalog


def test_gc_deletes_only_objects_unreferenced_by_every_manifest(tmp_path: Path) -> None:
    store, manifest, registry, referenced, orphan, catalog = _setup(tmp_path)
    try:
        report = ArtifactGarbageCollector(store, catalog).collect()

        assert report.completed is True
        assert report.deleted == (orphan.digest,)
        assert manifest.get(referenced.id) is not None
        assert store.verify(manifest.get(referenced.id).digest).read_text(encoding="utf-8") == "referenced"
    finally:
        registry.close(remove_files=True)
        catalog.close()


def test_gc_fails_closed_when_any_manifest_is_unreadable(tmp_path: Path) -> None:
    store, _manifest, registry, _referenced, orphan, catalog = _setup(tmp_path)
    broken_session = tmp_path / "sessions/broken.jsonl"
    SessionStore(str(broken_session), cwd=str(tmp_path))
    broken_manifest = Path(f"{broken_session}.artifacts.jsonl")
    broken_manifest.write_text("not-json\n", encoding="utf-8")

    try:
        report = ArtifactGarbageCollector(store, catalog).collect()

        assert report.completed is False
        assert report.deleted == ()
        assert store.verify(orphan.digest).exists()
    finally:
        registry.close(remove_files=True)
        catalog.close()


def test_gc_dry_run_reports_candidate_without_deleting_it(tmp_path: Path) -> None:
    store, _manifest, registry, _referenced, orphan, catalog = _setup(tmp_path)
    try:
        report = ArtifactGarbageCollector(store, catalog).collect(dry_run=True)

        assert report.completed is True
        assert report.deleted == (orphan.digest,)
        assert store.verify(orphan.digest).exists()
    finally:
        registry.close(remove_files=True)
        catalog.close()


def test_gc_fails_closed_for_symlink_manifest(tmp_path: Path) -> None:
    store, _manifest, registry, _referenced, orphan, catalog = _setup(tmp_path)
    linked_session = tmp_path / "sessions/linked.jsonl"
    SessionStore(str(linked_session), cwd=str(tmp_path))
    external = tmp_path / "external-manifest.jsonl"
    external.write_text("", encoding="utf-8")
    Path(f"{linked_session}.artifacts.jsonl").symlink_to(external)
    try:
        report = ArtifactGarbageCollector(store, catalog).collect()

        assert report.completed is False
        assert report.deleted == ()
        assert store.verify(orphan.digest).exists()
    finally:
        registry.close(remove_files=True)
        catalog.close()
