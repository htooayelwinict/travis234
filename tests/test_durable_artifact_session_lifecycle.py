from __future__ import annotations

from pathlib import Path

from tests._support_coding_agent import *  # noqa: F403
from travis.coding_agent.artifact_manifest import ArtifactManifest
from travis.coding_agent.session_catalog import SessionCatalog


def _session(tmp_path: Path, session_path: Path | None) -> AgentSession:
    return AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tools=[],
        session_path=str(session_path) if session_path is not None else None,
        agent_dir=str(tmp_path / "agent"),
    )


def _object_paths(tmp_path: Path) -> set[Path]:
    root = tmp_path / "agent/artifacts/objects"
    if not root.exists():
        return set()
    return {path for path in root.rglob("*") if path.is_file() and len(path.name) == 64}


def test_promoted_artifact_survives_shutdown_and_resume(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    source = tmp_path / "complete.log"
    source.write_text("complete durable output", encoding="utf-8")
    session = _session(tmp_path, session_path)
    ref = session._artifacts.promote(source, "command-output", retained=True)
    session.dispose()

    resumed = _session(tmp_path, session_path)
    try:
        resolved = resumed._artifacts.resolve_read(ref.id)
        assert resolved is not None
        assert resolved.read_text(encoding="utf-8") == "complete durable output"
    finally:
        resumed.dispose()


def test_fork_copies_reachable_reference_without_copying_object(tmp_path: Path) -> None:
    session_path = tmp_path / "parent.jsonl"
    source = tmp_path / "complete.log"
    source.write_text("forked output", encoding="utf-8")
    parent = _session(tmp_path, session_path)
    entry_id = parent._session_store.append_message(UserMessage("keep this branch"))
    ref = parent._artifacts.promote(
        source,
        "command-output",
        session_entry_id=entry_id,
    )
    objects_before = _object_paths(tmp_path)

    fork_path = Path(parent.create_branched_session(entry_id))
    forked = _session(tmp_path, fork_path)
    try:
        resolved = forked._artifacts.resolve_read(ref.id)
        assert resolved is not None
        assert resolved.read_text(encoding="utf-8") == "forked output"
        assert _object_paths(tmp_path) == objects_before
        assert ArtifactManifest.for_session(fork_path).get(ref.id) is not None
    finally:
        forked.dispose()
        parent.dispose()


def test_fork_excludes_reference_from_another_branch(tmp_path: Path) -> None:
    session_path = tmp_path / "parent.jsonl"
    parent = _session(tmp_path, session_path)
    kept_entry = parent._session_store.append_message(UserMessage("kept"))
    other_entry = parent._session_store.append_message(UserMessage("other"))
    source = tmp_path / "other.log"
    source.write_text("other output", encoding="utf-8")
    ref = parent._artifacts.promote(
        source,
        "command-output",
        session_entry_id=other_entry,
    )

    fork_path = Path(parent.create_branched_session(kept_entry))
    forked = _session(tmp_path, fork_path)
    try:
        assert forked._artifacts.resolve_read(ref.id) is None
    finally:
        forked.dispose()
        parent.dispose()


def test_in_memory_session_never_creates_artifact_root(tmp_path: Path) -> None:
    session = _session(tmp_path, None)
    source = tmp_path / "small.log"
    source.write_text("small", encoding="utf-8")
    session._artifacts.register(source, "command-output", remove_on_close=False)
    session.dispose()

    assert not (tmp_path / "agent/artifacts").exists()


def test_historical_session_without_manifest_resumes_unchanged(tmp_path: Path) -> None:
    session_path = tmp_path / "historical.jsonl"
    store = SessionStore(str(session_path), cwd=str(tmp_path))
    entry_id = store.append_message(UserMessage("historical"))

    resumed = _session(tmp_path, session_path)
    try:
        assert resumed.get_session_entry(entry_id) is not None
        assert resumed._artifacts.is_durable is True
    finally:
        resumed.dispose()


def test_session_catalog_ignores_artifact_sidecar_jsonl(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    session_path = sessions / "session.jsonl"
    SessionStore(str(session_path), cwd=str(tmp_path))
    ArtifactManifest.for_session(session_path).path.write_text("", encoding="utf-8")
    catalog = SessionCatalog(str(tmp_path / "agent"), session_dir=str(sessions))
    try:
        listed = catalog.list_all()

        assert [item.path for item in listed] == [session_path]
        assert catalog.diagnostics == ()
    finally:
        catalog.close()
