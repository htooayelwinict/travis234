from __future__ import annotations

from pathlib import Path

import pytest

from travis.coding_agent.extensions import ExtensionRunner
from travis234_mcp_adapter.extension import extension


EXPECTED_SCHEMA = {
    "type": "object",
    "properties": {
        "server": {"type": "string"},
        "search": {"type": "string"},
        "describe": {"type": "string"},
        "tool": {"type": "string"},
        "args": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}


def test_factory_registers_one_proxy_without_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_read(*_args, **_kwargs):
        raise AssertionError("extension factory performed file I/O")

    monkeypatch.setattr(Path, "read_text", fail_read)
    runner = ExtensionRunner(cwd=str(tmp_path))

    extension(runner)

    registered = runner.get_all_registered_tools()
    assert len(registered) == 1
    assert registered[0].definition.name == "mcp"
    assert registered[0].definition.parameters == EXPECTED_SCHEMA
    assert runner.get_registered_command("mcp-package-probe") is None


@pytest.mark.anyio
async def test_session_status_lists_disconnected_servers_without_connecting(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("global", {"command": "fixture"})
    config_tree.write_project_shared("project", {"command": "ignored"})
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    runner.bind_core(context_actions={"is_project_trusted": lambda: False})
    extension(runner)

    await runner.async_emit({"type": "session_start"})
    definition = runner.get_all_registered_tools()[0].definition
    result = await definition.execute("call-1", {}, None, None, None)

    assert [block.text for block in result.content] == [
        "MCP adapter status\n- global: disconnected\n- 1 project configuration file ignored until trust and reload"
    ]
    assert result.details == {
        "travis234Mcp": {
            "operation": "status",
            "servers": [{"name": "global", "status": "disconnected"}],
            "ignoredProjectSources": 1,
            "isError": False,
        }
    }


@pytest.mark.anyio
async def test_invalid_config_status_is_bounded_and_source_attributed(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    invalid = config_tree.cwd / ".mcp.json"
    invalid.write_text("not-json", encoding="utf-8")
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    runner.bind_core(context_actions={"is_project_trusted": lambda: True})
    extension(runner)

    await runner.async_emit({"type": "session_start"})
    definition = runner.get_all_registered_tools()[0].definition
    result = await definition.execute("call-1", {}, None, None, None)

    assert str(invalid) in result.content[0].text
    assert len(result.content[0].text.encode("utf-8")) <= 4_096
    assert result.details["travis234Mcp"]["isError"] is True


def test_tool_result_bridge_is_scoped_to_adapter_marker(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    extension(runner)

    adapted = runner.emit_tool_result(
        {
            "type": "tool_result",
            "toolName": "mcp",
            "content": [],
            "details": {"travis234Mcp": {"isError": True}},
            "isError": False,
        }
    )
    unrelated = runner.emit_tool_result(
        {
            "type": "tool_result",
            "toolName": "bash",
            "content": [],
            "details": {"exitCode": 1},
            "isError": False,
        }
    )

    assert adapted == {
        "content": [],
        "details": {"travis234Mcp": {"isError": True}},
        "isError": True,
    }
    assert unrelated is None
