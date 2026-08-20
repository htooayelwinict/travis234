from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests._support_coding_agent import faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.language_services import tool as language_service_tool
from travis.coding_agent.language_services.tool import LSP_SCHEMA, create_lsp_tool_definition
from travis.coding_agent.language_services.types import LanguageServiceLimits
from travis.coding_agent.language_services.workspace_edit import ActionTokenStore
from travis.coding_agent.settings_manager import SettingsManager


class RecordingManager:
    def __init__(self, workspace: Path, responses: dict[str, object] | None = None) -> None:
        self.workspace = workspace
        self.responses = responses or {}
        self.calls: list[tuple[str, object]] = []
        self.limits = LanguageServiceLimits()
        self.configs = [object()]

    def status(self):
        return {"configured": 1, "active": 0, "servers": [], "limits": {}}

    async def request(self, path, method, params, signal=None):
        self.calls.append((method, params))
        return self.responses.get(method)

    async def workspace_request(self, method, params, signal=None):
        self.calls.append((method, params))
        return self.responses.get(method)

    def response_context(self, path):
        source = Path(path)
        raw = source.read_bytes()
        import hashlib

        return {
            "generation": 4,
            "positionEncoding": "utf-16",
            "documentHash": hashlib.sha256(raw).hexdigest(),
        }


class PreparingManager(RecordingManager):
    def __init__(self, workspace: Path, responses: dict[str, object] | None = None) -> None:
        super().__init__(workspace, responses)
        self.prepared: list[tuple[str, int, int]] = []

    async def prepare_position(self, path: str, line: int, character: int) -> dict[str, int]:
        self.prepared.append((path, line, character))
        return {"line": line + 10, "character": character + 20}


def _run(coro):
    return asyncio.run(coro)


def _json_result(definition, args: dict[str, object]) -> tuple[dict[str, object], object]:
    result = _run(definition.execute("tool-1", args, None, None, None))
    return json.loads(result.content[0].text), result


def test_lsp_tool_has_one_exact_schema_and_all_conservative_effects(tmp_path: Path) -> None:
    definition = create_lsp_tool_definition(RecordingManager(tmp_path), ArtifactRegistry(), tmp_path)

    assert definition.name == "lsp"
    assert definition.parameters == LSP_SCHEMA
    assert set(LSP_SCHEMA["properties"]["action"]["enum"]) == {
        "status",
        "diagnostics",
        "symbols",
        "hover",
        "definition",
        "references",
        "code_actions",
        "rename_preview",
        "code_action_preview",
        "apply",
    }
    assert definition.effects == frozenset({"read", "write", "execute", "network"})


def test_session_registers_lsp_only_when_configured_and_respects_filters(tmp_path: Path) -> None:
    configured = SettingsManager.in_memory(
        {
            "languageServers": [
                {
                    "name": "python",
                    "command": "fixture-lsp",
                    "languages": ["python"],
                    "extensions": {".py": "python"},
                }
            ]
        }
    )
    absent = AgentSession(cwd=str(tmp_path), model=faux_model(), settings_manager=SettingsManager.in_memory())
    enabled = AgentSession(cwd=str(tmp_path), model=faux_model(), settings_manager=configured)
    excluded = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        settings_manager=configured,
        excluded_tool_names=["lsp"],
    )
    allowlisted = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        settings_manager=configured,
        allowed_tool_names=["read"],
    )

    assert absent.get_tool_definition("lsp") is None
    assert enabled.get_tool_definition("lsp") is not None
    assert "lsp" in enabled.get_active_tool_names()
    assert enabled._runtime._language_services.status()["active"] == 0
    assert excluded.get_tool_definition("lsp") is None
    assert allowlisted.get_tool_definition("lsp") is None


@pytest.mark.parametrize(
    "args",
    [
        {"action": "unknown"},
        {"action": "hover", "path": "main.py", "line": -1, "character": 0},
        {"action": "definition", "path": "main.py", "line": 0},
        {"action": "symbols"},
        {"action": "symbols", "path": "main.py", "query": "both"},
        {"action": "code_actions", "path": "main.py", "start": {}, "end": {}},
    ],
)
def test_invalid_action_inputs_fail_before_server_contact(tmp_path: Path, args: dict[str, object]) -> None:
    manager = RecordingManager(tmp_path)
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    with pytest.raises(ValueError):
        _run(definition.execute("tool-1", args, None, None, None))
    assert manager.calls == []


def test_status_is_read_only_and_does_not_start_server(tmp_path: Path) -> None:
    manager = RecordingManager(tmp_path)
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    payload, _result = _json_result(definition, {"action": "status"})

    assert payload["configured"] == 1
    assert payload["active"] == 0
    assert manager.calls == []


def test_hover_and_diagnostics_include_generation_and_document_hash(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    manager = RecordingManager(
        tmp_path,
        {
            "textDocument/hover": {"contents": {"kind": "markdown", "value": "**int**"}},
            "textDocument/diagnostic": {
                "items": [
                    {
                        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}},
                        "message": "example",
                        "severity": 2,
                    }
                ]
            },
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    hover, _ = _json_result(
        definition,
        {"action": "hover", "path": "main.py", "line": 0, "character": 1},
    )
    diagnostics, _ = _json_result(definition, {"action": "diagnostics", "path": "main.py"})

    assert hover["contents"] == "**int**"
    assert hover["generation"] == 4
    assert len(diagnostics["documentHash"]) == 64
    assert diagnostics["diagnostics"][0]["message"] == "example"


def test_locations_are_workspace_relative_sorted_and_omit_outside_paths(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    other = tmp_path / "z.py"
    outside = tmp_path.parent / "outside.py"
    for path in (source, other, outside):
        path.write_text("value\n", encoding="utf-8")
    response = [
        {"uri": outside.as_uri(), "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}},
        {"uri": other.as_uri(), "range": {"start": {"line": 0, "character": 2}, "end": {"line": 0, "character": 3}}},
        {"uri": source.as_uri(), "range": {"start": {"line": 0, "character": 1}, "end": {"line": 0, "character": 2}}},
    ]
    manager = RecordingManager(tmp_path, {"textDocument/definition": response})
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    payload, _ = _json_result(
        definition,
        {"action": "definition", "path": "main.py", "line": 0, "character": 0},
    )

    assert [item["path"] for item in payload["locations"]] == ["main.py", "z.py"]
    assert payload["omittedOutsideWorkspace"] == 1


def test_workspace_symbols_are_deterministically_sorted(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    response = [
        {"name": "Zulu", "kind": 12, "location": {"uri": source.as_uri(), "range": {"start": {"line": 0, "character": 1}, "end": {"line": 0, "character": 2}}}},
        {"name": "Alpha", "kind": 12, "location": {"uri": source.as_uri(), "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}},
    ]
    manager = RecordingManager(tmp_path, {"workspace/symbol": response})
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    payload, _ = _json_result(definition, {"action": "symbols", "query": "a"})

    assert [symbol["name"] for symbol in payload["symbols"]] == ["Alpha", "Zulu"]


def test_references_and_rename_use_prepared_positions_and_exact_request_params(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    manager = PreparingManager(
        tmp_path,
        {
            "textDocument/references": [],
            "textDocument/rename": {"changes": {}},
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    references, _ = _json_result(
        definition,
        {"action": "references", "path": "main.py", "line": 1, "character": 2},
    )
    rename = _run(
        language_service_tool._execute_read_action(
            manager,
            tmp_path,
            "rename_preview",
            {
                "action": "rename_preview",
                "path": "main.py",
                "line": 3,
                "character": 4,
                "newName": "renamed",
            },
            None,
            ActionTokenStore(),
        )
    )

    uri = source.as_uri()
    assert manager.prepared == [("main.py", 1, 2), ("main.py", 3, 4)]
    assert manager.calls == [
        (
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 11, "character": 22},
                "context": {"includeDeclaration": True},
            },
        ),
        (
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 13, "character": 24},
                "newName": "renamed",
            },
        ),
    ]
    assert references["locations"] == []
    assert references["omittedOutsideWorkspace"] == 0
    assert rename["workspaceEdit"] == {"changes": {}}


def test_code_actions_prepare_range_filter_sort_and_bind_tokens(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    manager = PreparingManager(
        tmp_path,
        {
            "textDocument/codeAction": [
                {"title": "Zulu", "kind": "quickfix", "command": {"command": "z"}},
                {"title": 42, "edit": {}},
                {"title": "alpha", "edit": {"changes": {}}},
                "invalid",
            ]
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    payload, _ = _json_result(
        definition,
        {
            "action": "code_actions",
            "path": "main.py",
            "start": {"line": 0, "character": 1},
            "end": {"line": 0, "character": 5},
        },
    )

    assert manager.prepared == [("main.py", 0, 1), ("main.py", 0, 5)]
    assert manager.calls == [
        (
            "textDocument/codeAction",
            {
                "textDocument": {"uri": source.as_uri()},
                "range": {
                    "start": {"line": 10, "character": 21},
                    "end": {"line": 10, "character": 25},
                },
                "context": {"diagnostics": []},
            },
        )
    ]
    assert [action["title"] for action in payload["actions"]] == ["alpha", "Zulu"]
    assert payload["actions"][0]["hasEdit"] is True
    assert payload["actions"][0]["hasCommand"] is False
    assert payload["actions"][0]["kind"] is None
    assert payload["actions"][1]["hasEdit"] is False
    assert payload["actions"][1]["hasCommand"] is True
    assert payload["actions"][1]["kind"] == "quickfix"
    assert all(
        str(action["actionToken"]).startswith("lsp-action-") and len(str(action["actionToken"])) == 43
        for action in payload["actions"]
    )


def test_diagnostics_filter_invalid_items_and_sort_normalized_ranges(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("abc\ndef\n", encoding="utf-8")
    valid_range = {
        "start": {"line": 0, "character": 1},
        "end": {"line": 0, "character": 2},
    }
    manager = RecordingManager(
        tmp_path,
        {
            "textDocument/diagnostic": {
                "items": [
                    {"range": valid_range, "message": "z-last", "severity": True},
                    {"range": valid_range, "message": "a-first", "severity": 2, "source": "fixture", "code": 7},
                    {"range": valid_range, "message": 42},
                    {"message": "missing range"},
                    "invalid",
                ]
            }
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)

    payload, _ = _json_result(definition, {"action": "diagnostics", "path": "main.py"})

    assert payload["diagnostics"] == [
        {
            "range": valid_range,
            "message": "a-first",
            "severity": 2,
            "source": "fixture",
            "code": 7,
        },
        {"range": valid_range, "message": "z-last"},
    ]


def test_oversized_normalized_result_becomes_readable_artifact(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    huge = [{"name": f"symbol-{index:06d}-" + ("x" * 300), "kind": 12} for index in range(2000)]
    manager = RecordingManager(tmp_path, {"textDocument/documentSymbol": huge})
    artifacts = ArtifactRegistry()
    definition = create_lsp_tool_definition(manager, artifacts, tmp_path)

    _payload, result = _json_result(definition, {"action": "symbols", "path": "main.py"})

    artifact_id = result.details["artifactId"]
    assert artifact_id.startswith("artifact-")
    artifact_path = artifacts.resolve_read(artifact_id)
    assert artifact_path is not None
    assert artifact_path.stat().st_size > 256 * 1024
