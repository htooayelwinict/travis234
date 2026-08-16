from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import threading
from types import SimpleNamespace

from mcp.types import CallToolResult, ListToolsResult, TextContent
import pytest

from travis.agent.types import AbortSignal
from travis234_mcp_adapter.config import LoadedConfig, ReconnectConfig, ServerConfig
from travis234_mcp_adapter import runtime as runtime_module
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.proxy_tool import StaleMcpGenerationError, dispatch_proxy
from travis234_mcp_adapter.runtime import McpRuntime


class FakeClient:
    def __init__(self, *, fail_calls: bool = False) -> None:
        self.fail_calls = fail_calls
        self.call_count = 0

    async def list_tools(self, *, cursor=None):
        return ListToolsResult(tools=[])

    async def call_tool(self, name: str, arguments: dict[str, object]):
        self.call_count += 1
        if self.fail_calls:
            raise ConnectionError("transport token=must-not-leak")
        return CallToolResult(content=[TextContent(type="text", text=name)])


def _server(
    tmp_path: Path,
    *,
    automatic: bool = False,
    max_attempts: int = 1,
    base_delay_ms: int = 100,
) -> ServerConfig:
    return ServerConfig(
        name="fixture",
        source_path=tmp_path / "mcp.json",
        command="fixture-server",
        reconnect=ReconnectConfig(
            automatic=automatic,
            max_attempts=max_attempts,
            base_delay_ms=base_delay_ms,
        ),
    )


def test_status_snapshot_is_connection_free_and_does_not_resolve_credentials(
    tmp_path: Path,
) -> None:
    def fail_environ():
        raise AssertionError("status resolved credentials")

    runtime = McpRuntime({"fixture": _server(tmp_path)}, fail_environ)

    snapshot = runtime.status("fixture")

    assert snapshot.state == "disconnected"
    assert snapshot.last_error_type is None
    assert snapshot.connected_at_ms is None


@pytest.mark.anyio
async def test_explicit_reconnect_is_coalesced_and_replaces_the_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[FakeClient] = []
    closed: list[FakeClient] = []

    @asynccontextmanager
    async def open_client(_resolved):
        client = FakeClient()
        clients.append(client)
        try:
            yield client
        finally:
            closed.append(client)

    monkeypatch.setattr(runtime_module, "_open_client", open_client)
    runtime = McpRuntime({"fixture": _server(tmp_path)}, {})
    first = await runtime.connect("fixture", None)

    replacement_one, replacement_two = await asyncio.gather(
        runtime.reconnect("fixture", None),
        runtime.reconnect("fixture", None),
    )

    assert replacement_one is replacement_two
    assert replacement_one is not first
    assert len(clients) == 2
    assert closed == [clients[0]]
    assert runtime.status("fixture").state == "connected"
    await runtime.close()


@pytest.mark.anyio
async def test_automatic_recovery_never_replays_the_failed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = [FakeClient(fail_calls=True), FakeClient()]
    opened: list[FakeClient] = []

    @asynccontextmanager
    async def open_client(_resolved):
        client = clients[len(opened)]
        opened.append(client)
        yield client

    monkeypatch.setattr(runtime_module, "_open_client", open_client)
    runtime = McpRuntime(
        {"fixture": _server(tmp_path, automatic=True)},
        {},
    )
    connected = await runtime.connect("fixture", None)

    with pytest.raises(ConnectionError, match="transport token"):
        await connected.call_tool("side_effect", {}, None)

    assert clients[0].call_count == 1
    assert clients[1].call_count == 0
    assert len(opened) == 2
    recovered = await runtime.connect("fixture", None)
    assert recovered is not connected
    assert runtime.status("fixture").state == "connected"
    await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("max_attempts", "expected_delays"),
    [
        (1, []),
        (2, [0.5]),
        (3, [0.5, 1.0]),
    ],
)
async def test_reconnect_attempts_are_bounded_and_backoff_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    max_attempts: int,
    expected_delays: list[float],
) -> None:
    attempts = 0
    delays: list[float] = []

    @asynccontextmanager
    async def fail_open(_resolved):
        nonlocal attempts
        attempts += 1
        raise OSError("credential=must-not-leak")
        yield FakeClient()  # pragma: no cover

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(runtime_module, "_open_client", fail_open)
    runtime = McpRuntime(
        {
            "fixture": _server(
                tmp_path,
                max_attempts=max_attempts,
                base_delay_ms=500,
            )
        },
        {},
        sleep=fake_sleep,
    )

    with pytest.raises(OSError, match="credential"):
        await runtime.reconnect("fixture", None)

    assert attempts == max_attempts
    assert delays == expected_delays
    snapshot = runtime.status("fixture")
    assert snapshot.state == "failed"
    assert snapshot.last_error_type == "OSError"
    assert snapshot.last_error_at_ms is not None
    await runtime.close()


@pytest.mark.anyio
async def test_cancellation_interrupts_reconnect_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    sleeping = threading.Event()

    @asynccontextmanager
    async def fail_open(_resolved):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("offline")
        yield FakeClient()  # pragma: no cover

    async def blocking_sleep(_delay: float) -> None:
        sleeping.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime_module, "_open_client", fail_open)
    runtime = McpRuntime(
        {"fixture": _server(tmp_path, max_attempts=3)},
        {},
        sleep=blocking_sleep,
    )
    signal = AbortSignal()
    reconnecting = asyncio.create_task(runtime.reconnect("fixture", signal))
    assert await asyncio.to_thread(sleeping.wait, 1)
    assert runtime.status("fixture").state == "reconnecting"

    signal.abort()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(reconnecting, 1)
    assert attempts == 1
    assert runtime.status("fixture").state == "disconnected"
    await runtime.close()


@pytest.mark.anyio
async def test_cancellation_interrupts_connection_establishment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening = threading.Event()

    @asynccontextmanager
    async def blocked_open(_resolved):
        opening.set()
        await asyncio.Event().wait()
        yield FakeClient()  # pragma: no cover

    monkeypatch.setattr(runtime_module, "_open_client", blocked_open)
    runtime = McpRuntime({"fixture": _server(tmp_path)}, {})
    signal = AbortSignal()
    connecting = asyncio.create_task(runtime.connect("fixture", signal))
    assert await asyncio.to_thread(opening.wait, 1)
    assert runtime.status("fixture").state == "connecting"

    signal.abort()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(connecting, 1)
    assert runtime.status("fixture").state == "disconnected"
    await runtime.close()


@pytest.mark.anyio
async def test_reconnect_resolves_credentials_again_without_retaining_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_values: list[str] = []
    values = iter(("first-value", "second-value"))
    server = ServerConfig(
        name="fixture",
        source_path=tmp_path / "mcp.json",
        command="fixture-server",
        env={"SERVICE_TOKEN": "${SERVICE_TOKEN}"},
    )

    @asynccontextmanager
    async def open_client(resolved):
        resolved_values.append(resolved.env["SERVICE_TOKEN"])
        yield FakeClient()

    monkeypatch.setattr(runtime_module, "_open_client", open_client)
    runtime = McpRuntime(
        {"fixture": server},
        lambda: {"SERVICE_TOKEN": next(values)},
    )

    await runtime.connect("fixture", None)
    await runtime.reconnect("fixture", None)

    assert resolved_values == ["first-value", "second-value"]
    snapshot = runtime.status("fixture")
    assert "first-value" not in repr(snapshot)
    assert "second-value" not in repr(snapshot)
    await runtime.close()


@pytest.mark.anyio
async def test_close_cancels_pending_reconnect_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeping = threading.Event()

    @asynccontextmanager
    async def fail_open(_resolved):
        raise ConnectionError("offline")
        yield FakeClient()  # pragma: no cover

    async def blocking_sleep(_delay: float) -> None:
        sleeping.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime_module, "_open_client", fail_open)
    runtime = McpRuntime(
        {"fixture": _server(tmp_path, max_attempts=3)},
        {},
        sleep=blocking_sleep,
    )
    reconnecting = asyncio.create_task(runtime.reconnect("fixture", None))
    assert await asyncio.to_thread(sleeping.wait, 1)

    await runtime.close()

    with pytest.raises(asyncio.CancelledError):
        await reconnecting
    assert runtime.status("fixture").state == "closing"


@pytest.mark.anyio
async def test_close_cancels_connection_establishment_without_waiting_for_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening = threading.Event()

    @asynccontextmanager
    async def blocked_open(_resolved):
        opening.set()
        await asyncio.Event().wait()
        yield FakeClient()  # pragma: no cover

    monkeypatch.setattr(runtime_module, "_open_client", blocked_open)
    runtime = McpRuntime({"fixture": _server(tmp_path)}, {})
    connecting = asyncio.create_task(runtime.connect("fixture", None))
    assert await asyncio.to_thread(opening.wait, 1)

    await asyncio.wait_for(runtime.close(), 1)

    with pytest.raises(asyncio.CancelledError):
        await connecting
    assert runtime.status("fixture").state == "closing"


@pytest.mark.anyio
async def test_proxy_reconnect_does_not_issue_a_protocol_request_and_clears_catalogs(
    tmp_path: Path,
) -> None:
    configured = _server(tmp_path)

    class ProxyRuntime:
        def __init__(self) -> None:
            self.reconnects = 0
            self.connects = 0

        async def reconnect(self, name, _signal):
            assert name == "fixture"
            self.reconnects += 1
            return object()

        async def connect(self, _name, _signal):
            self.connects += 1
            raise AssertionError("explicit reconnect issued a protocol request")

    runtime = ProxyRuntime()
    state = SimpleNamespace(
        config=LoadedConfig(
            servers={"fixture": configured},
            sources=(),
            ignored_project_sources=(),
        ),
        config_error=None,
        runtime=runtime,
        catalogs={"fixture": (object(),)},
        resource_catalogs={"fixture": object()},
        prompt_catalogs={"fixture": object()},
        spills=SpillRegistry(tmp_path),
        generation=1,
        shadowed_configured_names=(),
    )

    result = await dispatch_proxy(
        state,
        {"server": "fixture", "operation": "reconnect"},
        None,
    )

    assert runtime.reconnects == 1
    assert runtime.connects == 0
    assert state.catalogs == {}
    assert state.resource_catalogs == {}
    assert state.prompt_catalogs == {}
    assert result.details["travis234Mcp"] == {
        "operation": "reconnect",
        "server": "fixture",
        "isError": False,
    }


@pytest.mark.anyio
async def test_proxy_reconnect_rejects_a_stale_session_generation(
    tmp_path: Path,
) -> None:
    configured = _server(tmp_path)
    state = None

    class ReloadingRuntime:
        async def reconnect(self, _name, _signal):
            state.generation += 1
            return object()

    state = SimpleNamespace(
        config=LoadedConfig(
            servers={"fixture": configured},
            sources=(),
            ignored_project_sources=(),
        ),
        config_error=None,
        runtime=ReloadingRuntime(),
        catalogs={},
        resource_catalogs={},
        prompt_catalogs={},
        spills=SpillRegistry(tmp_path),
        generation=4,
        shadowed_configured_names=(),
    )

    with pytest.raises(StaleMcpGenerationError):
        await dispatch_proxy(
            state,
            {"server": "fixture", "operation": "reconnect"},
            None,
        )


def test_runtime_status_exposes_only_bounded_diagnostics(tmp_path: Path) -> None:
    runtime = McpRuntime({"fixture": _server(tmp_path)}, {})

    snapshot = runtime.status("fixture")

    assert snapshot.state == "disconnected"
    assert snapshot.updated_at_ms > 0
    assert not hasattr(snapshot, "error_message")


@pytest.mark.anyio
async def test_proxy_status_reports_reconnect_policy_without_connecting(
    tmp_path: Path,
) -> None:
    configured = _server(tmp_path, automatic=True, max_attempts=2)
    runtime = McpRuntime({"fixture": configured}, {})
    state = SimpleNamespace(
        config=LoadedConfig(
            servers={"fixture": configured},
            sources=(),
            ignored_project_sources=(),
        ),
        config_error=None,
        runtime=runtime,
        catalogs={},
        resource_catalogs={},
        prompt_catalogs={},
        spills=SpillRegistry(tmp_path),
        generation=1,
        shadowed_configured_names=(),
    )

    result = await dispatch_proxy(state, {}, None)

    assert result.content[0].text == (
        "MCP adapter status\n"
        "- fixture: disconnected; automaticReconnect=on"
    )
    server = result.details["travis234Mcp"]["servers"][0]
    assert server["name"] == "fixture"
    assert server["status"] == "disconnected"
    assert server["automaticReconnect"] is True
    assert server["maxReconnectAttempts"] == 2
    assert server["updatedAtMs"] > 0
    assert "lastError" not in server
    assert runtime.is_connected("fixture") is False
    await runtime.close()


@pytest.mark.anyio
async def test_proxy_clears_server_catalogs_after_transport_failure(
    tmp_path: Path,
) -> None:
    configured = _server(tmp_path, automatic=True)

    class FailingRuntime:
        async def connect(self, _name, _signal):
            raise ConnectionError("transport secret=must-not-leak")

    state = SimpleNamespace(
        config=LoadedConfig(
            servers={"fixture": configured},
            sources=(),
            ignored_project_sources=(),
        ),
        config_error=None,
        runtime=FailingRuntime(),
        catalogs={"fixture": (object(),)},
        resource_catalogs={"fixture": object()},
        prompt_catalogs={"fixture": object()},
        spills=SpillRegistry(tmp_path),
        generation=1,
        shadowed_configured_names=(),
    )

    result = await dispatch_proxy(
        state,
        {"server": "fixture", "operation": "tools.list"},
        None,
    )

    assert result.details["travis234Mcp"]["isError"] is True
    assert "must-not-leak" not in result.content[0].text
    assert state.catalogs == {}
    assert state.resource_catalogs == {}
    assert state.prompt_catalogs == {}
