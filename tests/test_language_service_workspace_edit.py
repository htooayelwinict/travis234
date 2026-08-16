from __future__ import annotations

import re
from pathlib import Path
import asyncio
import json

import pytest

from travis.coding_agent.language_services.documents import DocumentTracker
from travis.coding_agent.language_services.workspace_edit import (
    ActionTokenStore,
    WorkspaceEditError,
    WorkspaceEditPreviewStore,
)
from travis.coding_agent.language_services.types import LanguageServiceLimits
from travis.coding_agent.language_services.tool import create_lsp_tool_definition
from travis.coding_agent.artifacts import ArtifactRegistry


def _range(start_line: int, start_character: int, end_line: int, end_character: int) -> dict:
    return {
        "start": {"line": start_line, "character": start_character},
        "end": {"line": end_line, "character": end_character},
    }


def _create_preview(store, tracker, edit, *, encoding="utf-16", server_generation=1, config_generation=1):
    return store.create(
        edit,
        tracker,
        position_encoding=encoding,
        server_generation=server_generation,
        config_generation=config_generation,
    )


def test_changes_preview_normalizes_utf16_preserves_crlf_and_never_mutates(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    original = "a😀b\r\nsecond\r\n"
    source.write_bytes(original.encode("utf-8"))
    tracker = DocumentTracker(tmp_path)
    store = WorkspaceEditPreviewStore(tmp_path)
    edit = {
        "changes": {
            source.as_uri(): [
                {"range": _range(0, 1, 0, 3), "newText": "EMOJI"},
                {"range": _range(1, 0, 1, 0), "newText": "prefix-"},
                {"range": _range(1, 0, 1, 0), "newText": "ordered-"},
            ]
        }
    }

    preview = _create_preview(store, tracker, edit)

    assert re.fullmatch(r"lsp-preview-[0-9a-f]{32}", preview.token)
    assert source.read_bytes() == original.encode("utf-8")
    assert preview.files[0].path == source.resolve()
    assert preview.files[0].target_bytes.decode("utf-8") == "aEMOJIb\r\nprefix-ordered-second\r\n"
    assert "--- a/main.py" in preview.diff
    assert store.get(preview.token) is preview


def test_document_changes_enforces_version_and_rejects_duplicate_uri(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value\n", encoding="utf-8")
    tracker = DocumentTracker(tmp_path)
    snapshot = tracker.open_or_update(source)
    store = WorkspaceEditPreviewStore(tmp_path)

    with pytest.raises(WorkspaceEditError, match="version"):
        _create_preview(
            store,
            tracker,
            {
                "documentChanges": [
                    {
                        "textDocument": {"uri": source.as_uri(), "version": snapshot.version + 1},
                        "edits": [{"range": _range(0, 0, 0, 1), "newText": "V"}],
                    }
                ]
            },
        )

    with pytest.raises(WorkspaceEditError, match="duplicate"):
        _create_preview(
            store,
            tracker,
            {
                "documentChanges": [
                    {"textDocument": {"uri": source.as_uri(), "version": snapshot.version}, "edits": []},
                    {"textDocument": {"uri": source.as_uri(), "version": snapshot.version}, "edits": []},
                ]
            },
        )


@pytest.mark.parametrize("kind", ["create", "rename", "delete"])
def test_preview_rejects_workspace_resource_operations(tmp_path: Path, kind: str) -> None:
    with pytest.raises(WorkspaceEditError, match="resource operations"):
        _create_preview(
            WorkspaceEditPreviewStore(tmp_path),
            DocumentTracker(tmp_path),
            {"documentChanges": [{"kind": kind, "uri": (tmp_path / "x.py").as_uri()}]},
        )


def test_preview_rejects_outside_paths_symlink_escapes_and_overlapping_ranges(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("abcdef\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    link = workspace / "link.py"
    link.symlink_to(outside)
    store = WorkspaceEditPreviewStore(workspace)
    tracker = DocumentTracker(workspace)

    for uri in (outside.as_uri(), link.as_uri()):
        with pytest.raises(WorkspaceEditError, match="workspace"):
            _create_preview(store, tracker, {"changes": {uri: []}})

    with pytest.raises(WorkspaceEditError, match="overlap"):
        _create_preview(
            store,
            tracker,
            {
                "changes": {
                    source.as_uri(): [
                        {"range": _range(0, 0, 0, 4), "newText": "one"},
                        {"range": _range(0, 2, 0, 5), "newText": "two"},
                    ]
                }
            },
        )


def test_preview_rejects_invalid_ranges_and_non_regular_files(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("abc\n", encoding="utf-8")
    tracker = DocumentTracker(tmp_path)
    store = WorkspaceEditPreviewStore(tmp_path)

    with pytest.raises(WorkspaceEditError, match="range"):
        _create_preview(
            store,
            tracker,
            {"changes": {source.as_uri(): [{"range": _range(0, 3, 0, 1), "newText": "bad"}]}},
        )
    with pytest.raises(WorkspaceEditError, match="regular file"):
        _create_preview(store, tracker, {"changes": {tmp_path.as_uri(): []}})


def test_preview_tokens_expire_evict_oldest_and_bind_generations(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("abc\n", encoding="utf-8")
    now = [100.0]
    limits = LanguageServiceLimits(token_ttl_seconds=10, max_preview_tokens=2)
    store = WorkspaceEditPreviewStore(tmp_path, limits=limits, clock=lambda: now[0])
    tracker = DocumentTracker(tmp_path)
    edit = {"changes": {source.as_uri(): []}}

    first = _create_preview(store, tracker, edit, server_generation=1)
    second = _create_preview(store, tracker, edit, server_generation=2)
    third = _create_preview(store, tracker, edit, server_generation=3)

    with pytest.raises(WorkspaceEditError, match="unknown"):
        store.get(first.token)
    assert store.get(second.token, server_generation=2, config_generation=1) is second
    with pytest.raises(WorkspaceEditError, match="generation"):
        store.get(second.token, server_generation=9, config_generation=1)
    now[0] += 11
    with pytest.raises(WorkspaceEditError, match="expired"):
        store.get(third.token)


def test_action_tokens_are_opaque_bounded_and_reject_stale_bindings(tmp_path: Path) -> None:
    now = [10.0]
    store = ActionTokenStore(
        limits=LanguageServiceLimits(token_ttl_seconds=5, max_action_tokens=2),
        clock=lambda: now[0],
    )
    first = store.create({"title": "one"}, path="main.py", server_generation=1, config_generation=1)
    second = store.create({"title": "two"}, path="main.py", server_generation=1, config_generation=1)
    third = store.create({"title": "three"}, path="main.py", server_generation=1, config_generation=1)

    assert re.fullmatch(r"lsp-action-[0-9a-f]{32}", third.token)
    with pytest.raises(WorkspaceEditError, match="unknown"):
        store.resolve(first.token, server_generation=1, config_generation=1)
    assert store.resolve(second.token, server_generation=1, config_generation=1).action["title"] == "two"
    with pytest.raises(WorkspaceEditError, match="generation"):
        store.resolve(third.token, server_generation=2, config_generation=1)
    now[0] += 6
    with pytest.raises(WorkspaceEditError, match="expired"):
        store.resolve(third.token, server_generation=1, config_generation=1)


class _PreviewManager:
    def __init__(self, workspace: Path, responses: dict[str, object]) -> None:
        self.workspace = workspace
        self.responses = responses
        self.documents = DocumentTracker(workspace)
        self.limits = LanguageServiceLimits()
        self.generation = 1
        self.config_generation = 1

    def status(self):
        return {"configured": 1, "active": 0}

    async def prepare_position(self, path, line, character):
        return {"line": line, "character": character}

    async def request(self, path, method, params, signal=None):
        self.documents.open_or_update(path)
        return self.responses.get(method)

    async def workspace_request(self, method, params, signal=None):
        return self.responses.get(method)

    def response_context(self, path):
        snapshot = self.documents.snapshot(path)
        return {
            "generation": self.generation,
            "configGeneration": self.config_generation,
            "positionEncoding": "utf-16",
            "documentHash": snapshot.sha256,
        }


def _tool_payload(definition, args: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(definition.execute("tool-preview", args, None, None, None))
    return json.loads(result.content[0].text)


def test_rename_preview_returns_token_and_leaves_workspace_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    original = "old_name = 1\n"
    source.write_text(original, encoding="utf-8")
    manager = _PreviewManager(
        tmp_path,
        {
            "textDocument/rename": {
                "changes": {
                    source.as_uri(): [
                        {"range": _range(0, 0, 0, 8), "newText": "new_name"}
                    ]
                }
            }
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    payload = _tool_payload(
        definition,
        {
            "action": "rename_preview",
            "path": "main.py",
            "line": 0,
            "character": 1,
            "newName": "new_name",
        },
    )

    assert payload["previewToken"].startswith("lsp-preview-")
    assert payload["files"][0]["path"] == "main.py"
    assert "new_name" in payload["diff"]
    assert source.read_text(encoding="utf-8") == original


def test_code_actions_return_opaque_tokens_and_preview_only_selected_edit(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    original = "bad\n"
    source.write_text(original, encoding="utf-8")
    edit_action = {
        "title": "Fix spelling",
        "kind": "quickfix",
        "edit": {
            "changes": {
                source.as_uri(): [
                    {"range": _range(0, 0, 0, 3), "newText": "good"}
                ]
            }
        },
    }
    manager = _PreviewManager(tmp_path, {"textDocument/codeAction": [edit_action]})
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    listed = _tool_payload(
        definition,
        {
            "action": "code_actions",
            "path": "main.py",
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 3},
        },
    )
    token = listed["actions"][0]["actionToken"]
    preview = _tool_payload(definition, {"action": "code_action_preview", "actionToken": token})

    assert preview["previewToken"].startswith("lsp-preview-")
    assert "good" in preview["diff"]
    assert source.read_text(encoding="utf-8") == original
    with pytest.raises(WorkspaceEditError, match="unknown"):
        _tool_payload(definition, {"action": "code_action_preview", "actionToken": token})


@pytest.mark.parametrize(
    "action",
    [
        {"title": "Command", "command": {"command": "do.bad"}},
        {
            "title": "Edit and command",
            "edit": {"changes": {}},
            "command": {"command": "do.bad"},
        },
    ],
)
def test_code_action_preview_rejects_command_execution(tmp_path: Path, action: dict[str, object]) -> None:
    source = tmp_path / "main.py"
    source.write_text("bad\n", encoding="utf-8")
    manager = _PreviewManager(tmp_path, {"textDocument/codeAction": [action]})
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)
    listed = _tool_payload(
        definition,
        {
            "action": "code_actions",
            "path": "main.py",
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 3},
        },
    )

    with pytest.raises(WorkspaceEditError, match="command"):
        _tool_payload(
            definition,
            {"action": "code_action_preview", "actionToken": listed["actions"][0]["actionToken"]},
        )
    assert source.read_text(encoding="utf-8") == "bad\n"


def test_code_action_token_is_invalidated_by_config_generation(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("bad\n", encoding="utf-8")
    action = {"title": "Fix", "edit": {"changes": {source.as_uri(): []}}}
    manager = _PreviewManager(tmp_path, {"textDocument/codeAction": [action]})
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)
    listed = _tool_payload(
        definition,
        {
            "action": "code_actions",
            "path": "main.py",
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 3},
        },
    )
    manager.config_generation += 1

    with pytest.raises(WorkspaceEditError, match="generation"):
        _tool_payload(
            definition,
            {"action": "code_action_preview", "actionToken": listed["actions"][0]["actionToken"]},
        )
