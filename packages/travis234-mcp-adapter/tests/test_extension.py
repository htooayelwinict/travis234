from __future__ import annotations

from pathlib import Path
import asyncio
import json
import os
import sys

import psutil
import pytest

from travis.coding_agent.extensions import ExtensionRunner
from travis234_mcp_adapter import packaged_servers
from travis234_mcp_adapter.extension import extension
from travis234_mcp_adapter.output_guard import MAX_INLINE_BYTES
from travis234_mcp_adapter.packaged_servers import PackagedServer, register_packaged_server


FIXTURE = Path(__file__).parent / "fixtures" / "server.py"


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


def test_adapter_extension_is_idempotent_across_duplicate_distribution_paths(
    tmp_path: Path,
) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))

    extension(runner.create_extension_api("/one/extensions/mcp_adapter.py"))
    extension(runner.create_extension_api("/two/extensions/mcp_adapter.py"))

    assert [item.definition.name for item in runner.get_all_registered_tools()] == [
        "mcp"
    ]
    assert len(runner._handlers["session_start"]) == 1
    assert len(runner._handlers["session_shutdown"]) == 1
    assert len(runner._handlers["tool_result"]) == 1


@pytest.mark.anyio
async def test_session_admits_packaged_server_without_mcp_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    binary = tmp_path / "package" / "bin" / "ghost"
    home.mkdir()
    project.mkdir()
    binary.parent.mkdir(parents=True)
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})
    register_packaged_server(
        PackagedServer(
            name="ghost-os",
            package_root=binary.parents[1],
            command=binary,
            args=("mcp",),
        )
    )
    runner = ExtensionRunner(cwd=str(project))
    runner.bind_core(context_actions={"is_project_trusted": lambda: False})
    extension(runner)

    await runner.async_emit({"type": "session_start"})
    definition = runner.get_all_registered_tools()[0].definition
    result = await definition.execute("status", {}, None, None, None)

    assert result.content[0].text == "MCP adapter status\n- ghost-os: disconnected"
    assert not list(home.rglob("mcp.json"))
    await runner.async_emit({"type": "session_shutdown"})


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


def _write_stdio_config(home: Path, servers: dict[str, dict[str, object]]) -> None:
    path = home / ".config" / "mcp" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _stdio_entry(pid_file: Path) -> dict[str, object]:
    return {
        "command": sys.executable,
        "args": [str(FIXTURE)],
        "env": {
            "FIXTURE_TOKEN": "${FIXTURE_TOKEN}",
            "FIXTURE_PID_FILE": str(pid_file),
        },
    }


@pytest.mark.anyio
async def test_extension_lifecycle_is_lazy_replaces_and_cleans_child_and_spill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    pid_file = tmp_path / "fixture.pid"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FIXTURE_TOKEN", "configured")
    _write_stdio_config(home, {"fixture": _stdio_entry(pid_file)})
    runner = ExtensionRunner(cwd=str(project))
    runner.bind_core(context_actions={"is_project_trusted": lambda: True})
    extension(runner)

    await runner.async_emit({"type": "session_start"})
    definition = runner.get_all_registered_tools()[0].definition
    assert not pid_file.exists()

    await definition.execute("list", {"server": "fixture"}, None, None, None)
    first_pid = int(pid_file.read_text(encoding="ascii"))
    oversized = await definition.execute(
        "large",
        {
            "server": "fixture",
            "tool": "large_output",
            "args": {"size": MAX_INLINE_BYTES + 1},
        },
        None,
        None,
        None,
    )
    spill_path = Path(oversized.details["travis234Mcp"]["spillPath"])
    assert spill_path.is_file()

    pid_file.unlink()
    await runner.async_emit({"type": "session_start", "reason": "reload"})
    assert not psutil.pid_exists(first_pid)
    assert not pid_file.exists()
    status = await definition.execute("status", {}, None, None, None)
    assert "disconnected" in status.content[0].text

    await definition.execute("list-2", {"server": "fixture"}, None, None, None)
    second_pid = int(pid_file.read_text(encoding="ascii"))
    assert second_pid != first_pid
    await runner.async_emit({"type": "session_shutdown"})
    await runner.async_emit({"type": "session_shutdown"})

    assert not psutil.pid_exists(second_pid)
    assert not spill_path.exists()


@pytest.mark.anyio
async def test_broken_server_is_isolated_from_healthy_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FIXTURE_TOKEN", "configured")
    _write_stdio_config(
        home,
        {
            "broken": {"command": f"missing-mcp-{os.getpid()}"},
            "healthy": _stdio_entry(tmp_path / "healthy.pid"),
        },
    )
    runner = ExtensionRunner(cwd=str(project))
    runner.bind_core(context_actions={"is_project_trusted": lambda: True})
    extension(runner)
    await runner.async_emit({"type": "session_start"})
    definition = runner.get_all_registered_tools()[0].definition

    first_broken = await definition.execute("broken-1", {"server": "broken"}, None, None, None)
    await definition.execute("healthy-list", {"server": "healthy"}, None, None, None)
    healthy = await definition.execute(
        "healthy-call",
        {"server": "healthy", "tool": "echo", "args": {"text": "isolated"}},
        None,
        None,
        None,
    )
    second_broken = await definition.execute("broken-2", {"server": "broken"}, None, None, None)

    assert first_broken.details["travis234Mcp"]["isError"] is True
    assert healthy.content[0].text == "isolated"
    assert second_broken.details["travis234Mcp"]["isError"] is True
    await runner.async_emit({"type": "session_shutdown"})


@pytest.mark.anyio
async def test_shutdown_cancels_calls_on_two_servers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FIXTURE_TOKEN", "configured")
    _write_stdio_config(
        home,
        {
            "one": _stdio_entry(tmp_path / "one.pid"),
            "two": _stdio_entry(tmp_path / "two.pid"),
        },
    )
    runner = ExtensionRunner(cwd=str(project))
    runner.bind_core(context_actions={"is_project_trusted": lambda: True})
    extension(runner)
    await runner.async_emit({"type": "session_start"})
    definition = runner.get_all_registered_tools()[0].definition
    await asyncio.gather(
        definition.execute("one-list", {"server": "one"}, None, None, None),
        definition.execute("two-list", {"server": "two"}, None, None, None),
    )
    calls = [
        asyncio.create_task(
            definition.execute(
                name,
                {"server": name, "tool": "slow", "args": {"delay_ms": 5_000}},
                None,
                None,
                None,
            )
        )
        for name in ("one", "two")
    ]
    await asyncio.sleep(0.05)

    await runner.async_emit({"type": "session_shutdown"})

    outcomes = await asyncio.gather(*calls, return_exceptions=True)
    assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes), outcomes
    assert not psutil.pid_exists(int((tmp_path / "one.pid").read_text(encoding="ascii")))
    assert not psutil.pid_exists(int((tmp_path / "two.pid").read_text(encoding="ascii")))
