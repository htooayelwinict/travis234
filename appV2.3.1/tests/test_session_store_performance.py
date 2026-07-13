from __future__ import annotations

from pathlib import Path

from appv231.ai.types import UserMessage, now_ms
from appv231.coding_agent.session_store import SessionStore


def test_single_writer_appends_parse_only_unseen_suffix(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(str(path), cwd=str(tmp_path))
    parsed_bytes = 0
    full_loads = 0
    original_read = store._read_range
    original_load = store._load

    def measured(start: int) -> bytes:
        nonlocal parsed_bytes
        payload = original_read(start)
        parsed_bytes += len(payload)
        return payload

    def measured_load() -> None:
        nonlocal full_loads
        full_loads += 1
        original_load()

    monkeypatch.setattr(store, "_read_range", measured)
    monkeypatch.setattr(store, "_load", measured_load)
    for index in range(2_000):
        store.append_message(UserMessage(content=f"message-{index}", timestamp=now_ms()))

    final_size = path.stat().st_size
    assert full_loads == 0
    assert parsed_bytes <= final_size * 3
    assert len(store.file_entries) == 2_001
