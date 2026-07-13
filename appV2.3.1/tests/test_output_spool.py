from __future__ import annotations

import stat
from pathlib import Path

from appv231.coding_agent.tools.output_spool import OutputSpool
from appv231.coding_agent.artifacts import ArtifactRegistry


def test_output_spool_bounds_memory_and_persists_every_byte(tmp_path: Path) -> None:
    spool = OutputSpool(max_bytes=1024, max_lines=20, directory=tmp_path)
    payload = b"x" * (1024 * 1024)

    for offset in range(0, len(payload), 8192):
        spool.append(payload[offset : offset + 8192])
        spool.snapshot(persist_if_truncated=True)

    spool.finish()
    snapshot = spool.snapshot(persist_if_truncated=True)
    spool.close()

    assert snapshot.truncation.truncated is True
    assert snapshot.truncation.total_bytes == len(payload)
    assert len(snapshot.content.encode("utf-8")) <= 1024
    assert not hasattr(spool, "_raw")
    assert not hasattr(spool, "_text")
    assert len(spool._tail.encode("utf-8")) <= 1024
    assert snapshot.full_output_path is not None
    artifact = Path(snapshot.full_output_path)
    assert artifact.read_bytes() == payload
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


def test_output_spool_keeps_appending_after_first_truncated_snapshot(tmp_path: Path) -> None:
    spool = OutputSpool(max_bytes=8, max_lines=20, directory=tmp_path)
    spool.append(b"first-part")
    first = spool.snapshot(persist_if_truncated=True)
    spool.append(b"-second-part")
    spool.finish()
    final = spool.snapshot(persist_if_truncated=True)
    spool.close()

    assert first.full_output_path == final.full_output_path
    assert Path(final.full_output_path).read_bytes() == b"first-part-second-part"
    assert final.content == "ond-part"
    assert len(final.content.encode("utf-8")) <= 8


def test_output_spool_replaces_invalid_utf8_without_losing_output(tmp_path: Path) -> None:
    spool = OutputSpool(max_bytes=1024, max_lines=20, directory=tmp_path)
    spool.append(b"before-\xff-after")
    spool.finish()
    snapshot = spool.snapshot()
    spool.close()

    assert snapshot.content == "before-\ufffd-after"


def test_output_spool_registers_truncated_artifact(tmp_path: Path) -> None:
    registry = ArtifactRegistry()
    spool = OutputSpool(max_bytes=4, directory=tmp_path, artifact_registry=registry)
    spool.append(b"complete-output")
    spool.finish()
    snapshot = spool.snapshot(persist_if_truncated=True)
    spool.close()

    assert snapshot.artifact_id is not None
    assert registry.resolve_read(snapshot.artifact_id) == Path(snapshot.full_output_path).resolve()


def test_artifact_registry_preserves_borrowed_files_on_close(tmp_path: Path) -> None:
    owned = tmp_path / "owned.log"
    borrowed = tmp_path / "borrowed.log"
    owned.write_text("owned", encoding="utf-8")
    borrowed.write_text("borrowed", encoding="utf-8")
    registry = ArtifactRegistry()
    registry.register(owned, "output")
    registry.register(borrowed, "process-output", remove_on_close=False)

    registry.close(remove_files=True)

    assert not owned.exists()
    assert borrowed.exists()
