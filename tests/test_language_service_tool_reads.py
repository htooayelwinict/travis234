from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests._support_coding_agent import faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.language_services.tool import LSP_SCHEMA, create_lsp_tool_definition
from travis.coding_agent.language_services.types import LanguageServiceLimits
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
