from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support_coding_agent import *  # noqa: F403
from travis.coding_agent.agent_session_runtime import (
    AgentSessionRuntime,
    CreateAgentSessionRuntimeResult,
    InvalidSessionImportError,
)
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=Usage(),
        stop_reason="stop",
    )


def _runtime(
    tmp_path: Path,
    session: AgentSession,
    *,
    runners: list[ExtensionRunner] | None = None,
) -> AgentSessionRuntime:
    model = faux_model()

    def create_runtime(options: dict) -> CreateAgentSessionRuntimeResult:
        runner = ExtensionRunner()
        if runners is not None:
            runners.append(runner)
        replacement = AgentSession(
            cwd=options["cwd"],
            model=model,
            session_path=options["session_path"],
            parent_session_path=options.get("parent_session_path"),
            extension_runner=runner,
            session_start_event=options.get("session_start_event"),
            defer_session_start=bool(options.get("defer_session_start", False)),
        )
        return CreateAgentSessionRuntimeResult(
            session=replacement,
            services={"cwd": options["cwd"], "agentDir": str(tmp_path / ".travis234")},
        )

    return AgentSessionRuntime(
        session,
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / ".travis234")},
        create_runtime,
    )


def test_session_tree_reports_stable_depth_first_structure_and_active_branch(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "tree.jsonl"), cwd=str(tmp_path))
    root_id = store.append_message(UserMessage("root"))
    assistant_id = store.append_message(_assistant("answer"))
    store.append_model_change("faux", "faux-model")
    store.append_thinking_level_change("medium")
    store.append_compaction("summary", assistant_id, 100)
    store.append_custom_entry("checkpoint", {"ok": True})
    store.append_label_change(assistant_id, "first branch")
    store.branch(assistant_id)
    alternate_id = store.append_message(UserMessage("alternate"))

    tree = store.session_tree()
    by_id = {node["id"]: node for node in tree}

    assert [node["type"] for node in tree] == [
        "message",
        "message",
        "model_change",
        "thinking_level_change",
        "compaction",
        "custom",
        "label",
        "message",
    ]
    assert by_id[root_id]["depth"] == 0
    assert by_id[assistant_id]["depth"] == 1
    assert by_id[assistant_id]["label"] == "first branch"
    assert by_id[alternate_id]["active"] is True
    assert [node["id"] for node in tree if node["inActiveBranch"]] == [
        root_id,
        assistant_id,
        alternate_id,
    ]
    assert by_id[alternate_id]["summary"] == "user: alternate"


def test_fork_preserves_non_label_ids_and_recreates_resolved_labels(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    store = SessionStore(str(source), cwd=str(tmp_path))
    user_id = store.append_message(UserMessage("first"))
    assistant_id = store.append_message(_assistant("answer"))
    original_label_id = store.append_label_change(assistant_id, "checkpoint")
    selected_id = store.append_message(UserMessage("second"))
    source_bytes = source.read_bytes()

    fork_path = Path(store.create_branched_session(selected_id))
    forked = [json.loads(line) for line in fork_path.read_text(encoding="utf-8").splitlines()]

    assert source.read_bytes() == source_bytes
    assert forked[0]["parentSession"] == str(source)
    non_labels = [entry for entry in forked[1:] if entry["type"] != "label"]
    labels = [entry for entry in forked[1:] if entry["type"] == "label"]
    assert [entry["id"] for entry in non_labels] == [user_id, assistant_id, selected_id]
    assert [entry["parentId"] for entry in non_labels] == [None, user_id, assistant_id]
    assert len(labels) == 1
    assert labels[0]["id"] != original_label_id
    assert labels[0]["targetId"] == assistant_id
    assert labels[0]["label"] == "checkpoint"
    assert labels[0]["parentId"] == selected_id


def test_runtime_clone_forks_at_current_leaf_without_mutating_source(tmp_path: Path) -> None:
    source = tmp_path / "clone-source.jsonl"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(source))
    session._session_store.append_message(UserMessage("first"))  # noqa: SLF001
    leaf_id = session._session_store.append_message(_assistant("answer"))  # noqa: SLF001
    source_bytes = source.read_bytes()
    positions: list[tuple[str, str]] = []
    session.extension_runner.on(
        "session_before_fork",
        lambda event: positions.append((event["entryId"], event["position"])),
    )
    runtime = _runtime(tmp_path, session)

    result = runtime.clone()

    assert result == {"cancelled": False}
    assert source.read_bytes() == source_bytes
    assert runtime.session.session_path != str(source)
    cloned = [
        json.loads(line)
        for line in Path(runtime.session.session_path).read_text(encoding="utf-8").splitlines()
    ]
    assert cloned[0]["parentSession"] == str(source)
    assert [entry["id"] for entry in cloned[1:]] == [
        entry["id"] for entry in session.session_entries
    ]
    assert positions == [(leaf_id, "at")]


def test_rename_session_updates_jsonl_events_and_catalog_index(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    session_path, _ = catalog.new_session_path(str(tmp_path), session_id="rename-me")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=session_path,
        session_index=catalog.index,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.rename_session("Release repair")

    assert session.session_name == "Release repair"
    assert catalog.list_for_cwd(str(tmp_path))[0].name == "Release repair"
    assert any(getattr(event, "type", None) == "session_info_changed" for event in events)
    assert json.loads(Path(session_path).read_text(encoding="utf-8").splitlines()[-1])[
        "name"
    ] == "Release repair"


def test_import_validates_before_copy_and_avoids_name_collisions(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    initial_path = sessions / "initial.jsonl"
    initial = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(initial_path))
    runtime = _runtime(tmp_path, initial)

    external = tmp_path / "external"
    external.mkdir()
    invalid = external / "invalid.jsonl"
    invalid.write_text(
        json.dumps({"type": "session", "version": 2, "id": "old", "cwd": str(tmp_path)}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(InvalidSessionImportError, match="version 3"):
        runtime.import_from_jsonl(str(invalid))

    assert not (sessions / invalid.name).exists()
    assert runtime.session is initial

    imported = external / "shared-name.jsonl"
    imported_store = SessionStore(str(imported), cwd=str(tmp_path), session_id="imported")
    imported_store.append_message(UserMessage("imported message"))
    existing = sessions / imported.name
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"existing bytes must survive\n")

    result = runtime.import_from_jsonl(str(imported))

    assert result == {"cancelled": False}
    assert existing.read_bytes() == b"existing bytes must survive\n"
    destination = Path(runtime.session.session_path)
    assert destination != existing
    assert destination.name.startswith("shared-name-import-")
    assert destination.read_bytes() == imported.read_bytes()
