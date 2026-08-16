from __future__ import annotations

import threading
from pathlib import Path

import pytest

from travis.coding_agent.artifact_manifest import (
    ArtifactManifest,
    ArtifactManifestCorruptionError,
    ArtifactManifestEntry,
    ArtifactProducer,
)
from travis.coding_agent.artifact_store import ArtifactLimits, ArtifactPromotionError


def _entry(
    artifact_id: str,
    *,
    digest_char: str = "d",
    byte_size: int = 7,
    session_entry_id: str | None = None,
    tool_call_id: str | None = None,
    retained: bool = False,
) -> ArtifactManifestEntry:
    return ArtifactManifestEntry(
        id=artifact_id,
        digest=digest_char * 64,
        kind="command-output",
        byte_size=byte_size,
        created_at_ms=1_723_700_000_000,
        producer=ArtifactProducer(
            session_entry_id=session_entry_id,
            tool_call_id=tool_call_id,
        ),
        retained=retained,
    )


def test_manifest_append_reloads_exact_id_and_recovers_only_torn_tail(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    session.write_text('{"type":"session"}\n', encoding="utf-8")
    manifest = ArtifactManifest.for_session(session)
    entry = _entry("artifact-" + "a" * 32, tool_call_id="call-1")
    manifest.append(entry)
    with manifest.path.open("ab") as handle:
        handle.write(b'{"type":"artifact"')

    reopened = ArtifactManifest.for_session(session)

    assert reopened.get(entry.id) == entry
    assert reopened.recovered_tail_path is not None
    assert reopened.recovered_tail_path.read_bytes() == b'{"type":"artifact"'
    assert reopened.path.read_bytes().endswith(b"\n")


def test_manifest_rejects_corruption_before_final_record_without_rewriting(tmp_path: Path) -> None:
    manifest = ArtifactManifest.for_session(tmp_path / "session.jsonl")
    manifest.append(_entry("artifact-" + "a" * 32))
    original = manifest.path.read_bytes()
    corrupted = b"not-json\n" + original
    manifest.path.write_bytes(corrupted)

    with pytest.raises(ArtifactManifestCorruptionError, match="line 1"):
        ArtifactManifest.for_session(tmp_path / "session.jsonl")

    assert manifest.path.read_bytes() == corrupted


def test_manifest_does_not_treat_complete_invalid_final_record_as_torn(tmp_path: Path) -> None:
    manifest = ArtifactManifest.for_session(tmp_path / "session.jsonl")
    invalid = b'{"type":"not-an-artifact"}'
    manifest.path.write_bytes(invalid)

    with pytest.raises(ArtifactManifestCorruptionError, match="line 1"):
        ArtifactManifest.for_session(tmp_path / "session.jsonl")

    assert manifest.path.read_bytes() == invalid


def test_fork_copies_only_references_reachable_from_target_branch(tmp_path: Path) -> None:
    source = ArtifactManifest.for_session(tmp_path / "source.jsonl")
    kept = _entry("artifact-" + "b" * 32, session_entry_id="entry-kept")
    dropped = _entry("artifact-" + "c" * 32, session_entry_id="entry-other")
    retained = _entry("artifact-" + "d" * 32, retained=True)
    source.append(kept)
    source.append(dropped)
    source.append(retained)

    forked = source.fork_to(
        tmp_path / "fork.jsonl",
        allowed_entry_ids={"entry-kept"},
        allowed_tool_call_ids=set(),
    )

    assert forked.entries == (kept, retained)


def test_fork_keeps_reachable_tool_call_reference(tmp_path: Path) -> None:
    source = ArtifactManifest.for_session(tmp_path / "source.jsonl")
    kept = _entry("artifact-" + "e" * 32, tool_call_id="call-kept")
    source.append(kept)

    forked = source.fork_to(
        tmp_path / "fork.jsonl",
        allowed_entry_ids=set(),
        allowed_tool_call_ids={"call-kept"},
    )

    assert forked.entries == (kept,)


def test_duplicate_id_with_different_metadata_is_rejected(tmp_path: Path) -> None:
    manifest = ArtifactManifest.for_session(tmp_path / "session.jsonl")
    artifact_id = "artifact-" + "f" * 32
    manifest.append(_entry(artifact_id, digest_char="a"))

    with pytest.raises(ArtifactPromotionError) as raised:
        manifest.append(_entry(artifact_id, digest_char="b"))

    assert raised.value.code == "duplicate_artifact_id"
    assert len(manifest.entries) == 1


def test_manifest_enforces_logical_byte_and_reference_limits(tmp_path: Path) -> None:
    manifest = ArtifactManifest.for_session(
        tmp_path / "session.jsonl",
        limits=ArtifactLimits(max_session_logical_bytes=8, max_session_objects=1),
    )
    manifest.append(_entry("artifact-" + "1" * 32, byte_size=8))

    with pytest.raises(ArtifactPromotionError) as raised:
        manifest.append(_entry("artifact-" + "2" * 32, byte_size=1))

    assert raised.value.code == "session_limit"
    assert len(manifest.entries) == 1


def test_two_manifest_instances_append_without_losing_records(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    left = ArtifactManifest.for_session(session)
    right = ArtifactManifest.for_session(session)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def append(manifest: ArtifactManifest, entry: ArtifactManifestEntry) -> None:
        try:
            barrier.wait(timeout=2)
            manifest.append(entry)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [
        threading.Thread(target=append, args=(left, _entry("artifact-" + "3" * 32))),
        threading.Thread(target=append, args=(right, _entry("artifact-" + "4" * 32))),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert {entry.id for entry in ArtifactManifest.for_session(session).entries} == {
        "artifact-" + "3" * 32,
        "artifact-" + "4" * 32,
    }
