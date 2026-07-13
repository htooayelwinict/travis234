from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from appv231.ai.types import UserMessage, now_ms
from appv231.coding_agent.session_store import SessionCorruptionError, SessionStore


def _store(path: Path, cwd: Path) -> SessionStore:
    return SessionStore(str(path), cwd=str(cwd))


def test_load_recovers_only_truncated_final_record(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    store = _store(path, tmp_path)
    store.append_message(UserMessage(content="kept", timestamp=now_ms()))
    partial = b'{"type":"message"'
    with path.open("ab") as handle:
        handle.write(partial)

    recovered = _store(path, tmp_path)

    assert [entry["type"] for entry in recovered.file_entries] == ["session", "message"]
    assert recovered.recovered_tail_path is not None
    assert recovered.recovered_tail_path.read_bytes() == partial
    assert path.read_bytes().endswith(b"\n")

    recovered.append_message(UserMessage(content="after", timestamp=now_ms()))
    reopened = _store(path, tmp_path)
    assert [entry["type"] for entry in reopened.file_entries] == ["session", "message", "message"]


def test_load_rejects_corruption_before_final_record_without_modifying_file(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    store = _store(path, tmp_path)
    store.append_message(UserMessage(content="first", timestamp=now_ms()))
    lines = path.read_bytes().splitlines(keepends=True)
    corrupted = lines[0] + b"not-json\n" + b"".join(lines[1:])
    path.write_bytes(corrupted)

    with pytest.raises(SessionCorruptionError, match="line 2"):
        _store(path, tmp_path)

    assert path.read_bytes() == corrupted


def test_append_failure_does_not_mutate_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "session.jsonl"
    store = _store(path, tmp_path)
    before_entries = list(store.file_entries)
    before_leaf = store.leaf_id

    def fail_write(*_args, **_kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_record", fail_write)

    with pytest.raises(OSError, match="disk full"):
        store.append_message(UserMessage(content="new", timestamp=now_ms()))

    assert store.file_entries == before_entries
    assert store.leaf_id == before_leaf


def test_two_store_instances_append_without_lost_or_torn_records(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    left = _store(path, tmp_path)
    right = _store(path, tmp_path)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def append(store: SessionStore, text: str) -> None:
        try:
            barrier.wait(timeout=2)
            store.append_message(UserMessage(content=text, timestamp=now_ms()))
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [
        threading.Thread(target=append, args=(left, "left")),
        threading.Thread(target=append, args=(right, "right")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    loaded = _store(path, tmp_path)
    messages = [entry["message"]["content"] for entry in loaded.get_branch() if entry["type"] == "message"]
    assert sorted(messages) == ["left", "right"]
    assert all(line.endswith(b"\n") for line in path.read_bytes().splitlines(keepends=True))


def test_long_lived_stores_alternate_appends_without_full_reload(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    left = _store(path, tmp_path)
    right = _store(path, tmp_path)

    for index in range(100):
        selected = left if index % 2 == 0 else right
        selected.append_message(UserMessage(content=f"message-{index}", timestamp=now_ms()))

    loaded = _store(path, tmp_path)
    assert len(loaded.file_entries) == 101
    assert len({entry.get("id") for entry in loaded.entries}) == 100


def test_explicit_branch_parent_survives_external_append(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    left = _store(path, tmp_path)
    right = _store(path, tmp_path)
    branch_parent = left.append_message(UserMessage(content="parent", timestamp=now_ms()))
    right.append_message(UserMessage(content="external", timestamp=now_ms()))

    left.branch(branch_parent)
    branched = left.append_message(UserMessage(content="branched", timestamp=now_ms()))

    loaded = _store(path, tmp_path)
    assert loaded.get_entry(branched)["parentId"] == branch_parent


def test_store_reloads_after_inode_replacement_and_file_shrink(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    store = _store(path, tmp_path)
    store.append_message(UserMessage(content="before", timestamp=now_ms()))
    replacement = tmp_path / "replacement.jsonl"
    replacement_store = _store(replacement, tmp_path)
    replacement_store.append_message(UserMessage(content="replacement", timestamp=now_ms()))
    os.replace(replacement, path)

    store.append_message(UserMessage(content="after-replace", timestamp=now_ms()))
    assert len(store.file_entries) == 3

    header = path.read_bytes().splitlines(keepends=True)[0]
    path.write_bytes(header)
    store.append_message(UserMessage(content="after-shrink", timestamp=now_ms()))
    assert len(store.file_entries) == 2
