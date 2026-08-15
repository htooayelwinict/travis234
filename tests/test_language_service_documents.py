from __future__ import annotations

from pathlib import Path

import pytest

from travis.coding_agent.language_services.documents import (
    DocumentTracker,
    PositionEncoding,
    position_from_server,
    position_to_server,
)
from travis.coding_agent.language_services.types import DocumentPosition


def test_document_versions_increment_only_when_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("alpha\n", encoding="utf-8")
    tracker = DocumentTracker(tmp_path)

    first = tracker.open_or_update(source)
    unchanged = tracker.open_or_update(source)
    source.write_text("beta\n", encoding="utf-8")
    changed = tracker.open_or_update(source)

    assert first.version == unchanged.version == 1
    assert changed.version == 2
    assert first.sha256 != changed.sha256
    assert changed.uri == source.resolve().as_uri()
    tracker.mark_saved(source)
    assert tracker.snapshot(source).saved_hash == changed.sha256
    tracker.close(source)
    with pytest.raises(KeyError):
        tracker.snapshot(source)


def test_document_tracker_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.py"
    workspace.mkdir()
    outside.write_text("secret\n", encoding="utf-8")
    link = workspace / "link.py"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="escapes"):
        DocumentTracker(workspace).open_or_update(link)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_position_encoding_round_trips_astral_and_combining_text(encoding: PositionEncoding) -> None:
    text = "a😀éz\n"
    stable = DocumentPosition(line=0, character=3)

    server = position_to_server(text, stable, encoding)
    restored = position_from_server(text, server, encoding)

    assert restored == stable


def test_positions_reject_invalid_lines_and_mid_code_unit_offsets() -> None:
    text = "😀x\n"
    with pytest.raises(ValueError, match="line"):
        position_to_server(text, DocumentPosition(4, 0), "utf-16")
    with pytest.raises(ValueError, match="boundary"):
        position_from_server(text, DocumentPosition(0, 1), "utf-8")
