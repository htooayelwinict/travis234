from __future__ import annotations

from pathlib import Path

from travis.ai.types import UserMessage, now_ms
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_index import SessionIndex
from travis.coding_agent.session_store import SessionStore


def test_catalog_warm_listing_reads_no_jsonl_history(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    catalog = SessionCatalog(str(agent_dir))
    path, _ = catalog.new_session_path(str(project), session_id="large")
    store = SessionStore(path, cwd=str(project))
    for entry in range(1_000):
        store.append_message(UserMessage(content=f"message-{entry}-" + "x" * 1_000, timestamp=now_ms()))

    catalog.list_all()
    listed = catalog.list_all()

    assert [info.path for info in listed] == [Path(path).resolve()]
    assert catalog.scan_stats.bytes_read == 0
    assert catalog.scan_stats.records_decoded == 0
    assert catalog.scan_stats.files_backfilled == 0
    assert catalog.scan_stats.cache_hits == 1


def test_store_append_updates_preview_without_catalog_history_read(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    catalog = SessionCatalog(str(agent_dir))
    path, _ = catalog.new_session_path(str(project), session_id="demo")
    index = SessionIndex(agent_dir / "sessions" / "catalog.sqlite3")
    store = SessionStore(path, cwd=str(project), index=index)

    store.append_message(UserMessage(content="new preview", timestamp=now_ms()))
    listed = catalog.list_all()

    assert listed[0].preview == "new preview"
    assert catalog.scan_stats.bytes_read == 0
