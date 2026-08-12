from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import psutil
import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent as McpTextContent, Tool
from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.tools.types import ToolDefinition

from travis234_mcp_adapter.extension import extension
from travis234_mcp_adapter.output_guard import MAX_INLINE_BYTES


FIXTURE = Path(__file__).parent / "fixtures" / "server.py"
extension_module = importlib.import_module("travis234_mcp_adapter.extension")
EXPECTED_STATUS_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _bind(runner: ExtensionRunner, *, active: tuple[str, ...], trusted: bool = True) -> None:
    runner.bind_core(
        {"getActiveTools": lambda: list(active)},
        context_actions={"is_project_trusted": lambda: trusted},
    )


def _write_stdio_config(home: Path, servers: dict[str, dict[str, object]]) -> None:
    path = home / ".config" / "mcp" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _stdio_entry(pid_file: Path, **extra: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "command": sys.executable,
        "args": [str(FIXTURE)],
        "env": {
            "FIXTURE_TOKEN": "${FIXTURE_TOKEN}",
            "FIXTURE_PID_FILE": str(pid_file),
        },
    }
    entry.update(extra)
    return entry


def _registered(runner: ExtensionRunner) -> dict[str, ToolDefinition]:
    return {
        item.definition.name: item.definition
        for item in runner.get_all_registered_tools()
    }


def test_factory_registers_status_controller_without_io(
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
    assert registered[0].definition.parameters == EXPECTED_STATUS_SCHEMA
    assert registered[0].definition.activation_group == "mcp"


@pytest.mark.anyio
async def test_inactive_mcp_family_validates_status_without_connecting(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("global", {"command": "fixture"})
    config_tree.write_project_shared("project", {"command": "ignored"})
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=(), trusted=False)
    state = extension(runner)

    await runner.async_emit({"type": "session_start"})
    result = _registered(runner)["mcp"].execute("status", {}, None, None, None)

    assert state.runtime is None
    assert list(_registered(runner)) == ["mcp"]
    assert "global: disconnected" in result.content[0].text
    assert "1 project configuration file ignored" in result.content[0].text


@pytest.mark.anyio
async def test_active_family_discovers_and_calls_native_stdio_tools(
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
    _bind(runner, active=("mcp",))
    state = extension(runner)

    await runner.async_emit({"type": "session_start"})

    definitions = _registered(runner)
    assert list(definitions) == [
        "mcp__fixture__echo",
        "mcp__fixture__configured_secret_name",
        "mcp__fixture__slow",
        "mcp__fixture__large_output",
        "mcp__fixture__controlled_error",
        "mcp__fixture__emit_tools_changed",
        "mcp",
    ]
    assert pid_file.exists()
    result = await definitions["mcp__fixture__echo"].execute(
        "call-1", {"text": "native"}, None, None, None
    )
    assert result.content[0].text == "native"
    assert state.native_names[0] == "mcp__fixture__echo"
    await runner.async_emit({"type": "session_shutdown"})


@pytest.mark.anyio
async def test_invalid_config_updates_bounded_status(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    invalid = config_tree.cwd / ".mcp.json"
    invalid.write_text("not-json", encoding="utf-8")
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",), trusted=True)
    extension(runner)

    await runner.async_emit({"type": "session_start"})
    result = _registered(runner)["mcp"].execute("status", {}, None, None, None)

    assert str(invalid) in result.content[0].text
    assert len(result.content[0].text.encode("utf-8")) <= 16 * 1024
    assert result.details["travis234Mcp"]["isError"] is True


@pytest.mark.anyio
async def test_broken_server_isolated_from_healthy_native_catalog(
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
            "healthy": _stdio_entry(tmp_path / "healthy.pid", includeTools=["echo"]),
        },
    )
    runner = ExtensionRunner(cwd=str(project))
    _bind(runner, active=("mcp",))
    extension(runner)

    await runner.async_emit({"type": "session_start"})

    definitions = _registered(runner)
    assert "mcp__healthy__echo" in definitions
    assert not any(name.startswith("mcp__broken__") for name in definitions)
    status = definitions["mcp"].execute("status", {}, None, None, None)
    assert "broken" in status.content[0].text
    assert "diagnostic" in status.content[0].text
    await runner.async_emit({"type": "session_shutdown"})


@pytest.mark.anyio
async def test_reload_replaces_owned_names_runtime_and_spills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    pid_file = tmp_path / "fixture.pid"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FIXTURE_TOKEN", "configured")
    _write_stdio_config(home, {"fixture": _stdio_entry(pid_file, includeTools=["large_output"])})
    runner = ExtensionRunner(cwd=str(project))
    _bind(runner, active=("mcp",))
    extension(runner)

    await runner.async_emit({"type": "session_start"})
    first_pid = int(pid_file.read_text(encoding="ascii"))
    oversized = await _registered(runner)["mcp__fixture__large_output"].execute(
        "large", {"size": MAX_INLINE_BYTES + 1}, None, None, None
    )
    spill_path = Path(oversized.details["travis234Mcp"]["spillPath"])
    assert spill_path.is_file()

    _write_stdio_config(home, {"fixture": _stdio_entry(pid_file, includeTools=["echo"])})
    pid_file.unlink()
    await runner.async_emit({"type": "session_start", "reason": "reload"})

    assert not psutil.pid_exists(first_pid)
    assert not spill_path.exists()
    assert "mcp__fixture__large_output" not in _registered(runner)
    assert "mcp__fixture__echo" in _registered(runner)
    second_pid = int(pid_file.read_text(encoding="ascii"))
    await runner.async_emit({"type": "session_shutdown"})
    await runner.async_emit({"type": "session_shutdown"})
    assert not psutil.pid_exists(second_pid)


def test_tool_result_bridge_is_scoped_to_adapter_marker(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    extension(runner)

    adapted = runner.emit_tool_result(
        {
            "type": "tool_result",
            "toolName": "mcp__fixture__echo",
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

    assert adapted["isError"] is True
    assert unrelated is None


class _FakeConnected:
    def __init__(self, server_name: str, tools: list[Tool]) -> None:
        self.server_name = server_name
        self.tools = tools
        self.metadata = type(
            "Metadata",
            (),
            {"instructions": f"guidance for {server_name}", "protocol_version": "fixture"},
        )()

    async def list_tools(self, _signal, cursor=None):
        return ListToolsResult(tools=self.tools)

    async def call_tool(self, name, arguments, _signal):
        return CallToolResult(content=[McpTextContent(type="text", text=name)])


def _fake_tool(name: str) -> Tool:
    return Tool(name=name, inputSchema={"type": "object", "properties": {}})


@pytest.mark.anyio
async def test_discovery_concurrency_is_bounded_to_four(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    path = config_tree.home / ".config" / "mcp" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"mcpServers": {f"s{index}": {"command": "fixture"} for index in range(6)}}),
        encoding="utf-8",
    )
    observed = {"active": 0, "peak": 0}

    class FakeRuntime:
        def __init__(self, servers, environ):
            self.servers = servers

        async def connect(self, name, signal):
            observed["active"] += 1
            observed["peak"] = max(observed["peak"], observed["active"])
            await asyncio.sleep(0.02)
            observed["active"] -= 1
            return _FakeConnected(name, [_fake_tool("read")])

        def is_connected(self, name):
            return True

        async def close(self):
            return None

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    extension(runner)

    await runner.async_emit({"type": "session_start"})

    assert observed["peak"] == 4
    assert len([name for name in _registered(runner) if name.startswith("mcp__s")]) == 6


@pytest.mark.anyio
async def test_discovery_timeout_keeps_completed_healthy_server(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    path = config_tree.home / ".config" / "mcp" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"mcpServers": {"healthy": {"command": "fixture"}, "hung": {"command": "fixture"}}}
        ),
        encoding="utf-8",
    )

    class FakeRuntime:
        def __init__(self, servers, environ):
            pass

        async def connect(self, name, signal):
            if name == "hung":
                await asyncio.Event().wait()
            return _FakeConnected(name, [_fake_tool("read")])

        def is_connected(self, name):
            return name == "healthy"

        async def close(self):
            return None

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    monkeypatch.setattr(extension_module, "DISCOVERY_TIMEOUT_SECONDS", 0.03)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    extension(runner)

    await runner.async_emit({"type": "session_start"})

    assert "mcp__healthy__read" in _registered(runner)
    assert "mcp__hung__read" not in _registered(runner)
    assert "timed out" in _registered(runner)["mcp"].execute("status", {}, None, None, None).content[0].text


@pytest.mark.anyio
async def test_generated_name_collision_preserves_other_extension_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FIXTURE_TOKEN", "configured")
    _write_stdio_config(home, {"fixture": _stdio_entry(tmp_path / "fixture.pid", includeTools=["echo"])})
    runner = ExtensionRunner(cwd=str(project))
    _bind(runner, active=("mcp",))
    external = ToolDefinition(
        name="mcp__fixture__echo",
        label="external",
        description="external",
        parameters={"type": "object"},
        execute=lambda *_args: AgentToolResult(content=[TextContent(text="external")]),
    )
    runner.register_tool(external)
    extension(runner)

    await runner.async_emit({"type": "session_start"})

    assert _registered(runner)["mcp__fixture__echo"] is external
    status = _registered(runner)["mcp"].execute("status", {}, None, None, None)
    assert "collides" in status.content[0].text
    await runner.async_emit({"type": "session_shutdown"})


@pytest.mark.anyio
async def test_stale_session_generation_cannot_publish_discovered_tools(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("fixture", {"command": "fixture"})
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    instances: list[object] = []

    class FakeRuntime:
        def __init__(self, servers, environ):
            self.index = len(instances)
            self.closed = False
            instances.append(self)

        async def connect(self, name, signal):
            if self.index == 0:
                first_started.set()
                await release_first.wait()
                return _FakeConnected(name, [_fake_tool("stale")])
            return _FakeConnected(name, [_fake_tool("current")])

        def is_connected(self, name):
            return not self.closed

        async def close(self):
            self.closed = True

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    state = extension(runner)

    first_start = asyncio.create_task(runner.async_emit({"type": "session_start"}))
    await first_started.wait()
    await runner.async_emit({"type": "session_start", "reason": "reload"})
    release_first.set()
    await first_start

    definitions = _registered(runner)
    assert "mcp__fixture__current" in definitions
    assert "mcp__fixture__stale" not in definitions
    assert instances[0].closed is True
    assert state.diagnostics == {}


@pytest.mark.anyio
async def test_tool_list_change_reconciles_once_at_before_agent_start(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("fixture", {"command": "fixture"})

    class FakeRuntime:
        def __init__(self, servers, environ):
            self.tools = [_fake_tool("read")]
            self.dirty: set[str] = set()
            self.connect_calls: list[str] = []

        async def connect(self, name, signal):
            self.connect_calls.append(name)
            return _FakeConnected(name, list(self.tools))

        def is_connected(self, name):
            return True

        def mark_dirty(self, name):
            self.dirty.add(name)

        def take_dirty_servers(self):
            snapshot = tuple(sorted(self.dirty))
            self.dirty.clear()
            return snapshot

        def take_notification_errors(self):
            return ()

        async def close(self):
            return None

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    state = extension(runner)
    await runner.async_emit({"type": "session_start"})
    runtime = state.runtime
    assert runtime is not None

    runtime.tools = [_fake_tool("inspect")]
    for _index in range(50):
        runtime.mark_dirty("fixture")

    assert "mcp__fixture__read" in _registered(runner)
    assert "mcp__fixture__inspect" not in _registered(runner)

    await asyncio.to_thread(runner.emit_before_agent_start, "prompt", None, "system")

    assert runtime.connect_calls == ["fixture", "fixture"]
    assert "mcp__fixture__read" not in _registered(runner)
    assert "mcp__fixture__inspect" in _registered(runner)
    assert runtime.take_dirty_servers() == ()


@pytest.mark.anyio
async def test_notification_during_reconciliation_waits_for_next_boundary(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("fixture", {"command": "fixture"})

    class FakeRuntime:
        def __init__(self, servers, environ):
            self.tools = [_fake_tool("read")]
            self.dirty: set[str] = set()
            self.refreshes = 0

        async def connect(self, name, signal):
            connected = _FakeConnected(name, list(self.tools))
            original = connected.list_tools

            async def list_tools(signal, cursor=None):
                result = await original(signal, cursor)
                if self.refreshes:
                    self.dirty.add(name)
                self.refreshes += 1
                return result

            connected.list_tools = list_tools
            return connected

        def is_connected(self, name):
            return True

        def take_dirty_servers(self):
            snapshot = tuple(sorted(self.dirty))
            self.dirty.clear()
            return snapshot

        def take_notification_errors(self):
            return ()

        async def close(self):
            return None

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    state = extension(runner)
    await runner.async_emit({"type": "session_start"})
    runtime = state.runtime
    runtime.tools = [_fake_tool("inspect")]
    runtime.dirty.add("fixture")

    await asyncio.to_thread(runner.emit_before_agent_start, "one", None, "system")

    assert runtime.take_dirty_servers() == ("fixture",)
    runtime.dirty.add("fixture")
    await asyncio.to_thread(runner.emit_before_agent_start, "two", None, "system")
    assert "mcp__fixture__inspect" in _registered(runner)


@pytest.mark.anyio
async def test_reconciliation_failure_removes_stale_server_definitions(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("fixture", {"command": "fixture"})

    class FakeRuntime:
        def __init__(self, servers, environ):
            self.fail = False
            self.dirty: set[str] = set()

        async def connect(self, name, signal):
            if self.fail:
                raise RuntimeError("sensitive failure detail")
            return _FakeConnected(name, [_fake_tool("read")])

        def is_connected(self, name):
            return True

        def take_dirty_servers(self):
            snapshot = tuple(sorted(self.dirty))
            self.dirty.clear()
            return snapshot

        def take_notification_errors(self):
            return ()

        async def close(self):
            return None

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    state = extension(runner)
    await runner.async_emit({"type": "session_start"})
    runtime = state.runtime
    runtime.fail = True
    runtime.dirty.add("fixture")

    await asyncio.to_thread(runner.emit_before_agent_start, "prompt", None, "system")

    assert "mcp__fixture__read" not in _registered(runner)
    status = _registered(runner)["mcp"].execute("status", {}, None, None, None)
    assert "RuntimeError" in status.content[0].text
    assert "sensitive failure detail" not in status.content[0].text


@pytest.mark.anyio
async def test_stale_reconciliation_generation_publishes_nothing(
    config_tree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(config_tree.home))
    config_tree.write_global_shared("fixture", {"command": "fixture"})
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    class FakeRuntime:
        def __init__(self, servers, environ):
            self.dirty: set[str] = set()
            self.refreshing = False

        async def connect(self, name, signal):
            if self.refreshing:
                refresh_started.set()
                await release_refresh.wait()
                return _FakeConnected(name, [_fake_tool("stale")])
            return _FakeConnected(name, [_fake_tool("read")])

        def is_connected(self, name):
            return True

        def take_dirty_servers(self):
            snapshot = tuple(sorted(self.dirty))
            self.dirty.clear()
            return snapshot

        def take_notification_errors(self):
            return ()

        async def close(self):
            return None

    monkeypatch.setattr(extension_module, "McpRuntime", FakeRuntime)
    runner = ExtensionRunner(cwd=str(config_tree.cwd))
    _bind(runner, active=("mcp",))
    state = extension(runner)
    await runner.async_emit({"type": "session_start"})
    runtime = state.runtime
    runtime.refreshing = True
    runtime.dirty.add("fixture")

    reconciliation = asyncio.create_task(state.on_before_agent_start({}, None))
    await refresh_started.wait()
    state.generation += 1
    release_refresh.set()
    await reconciliation

    assert "mcp__fixture__read" in _registered(runner)
    assert "mcp__fixture__stale" not in _registered(runner)
