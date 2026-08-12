from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys

import psutil
import pytest

from mcp.types import TextContent
from mcp.client.subscriptions import ToolsListChanged
from mcp.types import ToolListChangedNotification
from travis.agent.types import AbortSignal
from travis234_mcp_adapter.config import ServerConfig
from travis234_mcp_adapter.config import resolve_server
from travis234_mcp_adapter.runtime import McpRuntime, _ServerActor


FIXTURE = Path(__file__).parent / "fixtures" / "server.py"


def _runtime(tmp_path: Path, *, timeout_ms: int | None = None) -> tuple[McpRuntime, Path]:
    pid_file = tmp_path / "fixture.pid"
    server = ServerConfig(
        name="fixture",
        source_path=tmp_path / "mcp.json",
        command=sys.executable,
        args=(str(FIXTURE),),
        env={
            "FIXTURE_TOKEN": "${FIXTURE_TOKEN}",
            "FIXTURE_PID_FILE": str(pid_file),
        },
        request_timeout_ms=timeout_ms,
    )
    return McpRuntime({"fixture": server}, {"FIXTURE_TOKEN": "configured"}), pid_file


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def test_stdio_connection_survives_sequential_event_loops(tmp_path: Path) -> None:
    runtime, pid_file = _runtime(tmp_path)

    async def connect_and_list():
        connected = await runtime.connect("fixture", None)
        await connected.list_tools(None)
        return connected

    first = asyncio.run(connect_and_list())
    pid = int(pid_file.read_text(encoding="ascii"))

    async def reconnect_and_call():
        connected = await runtime.connect("fixture", None)
        result = await connected.call_tool("echo", {"text": "next-loop"}, None)
        return connected, result

    second, result = asyncio.run(reconnect_and_call())

    assert second is first
    assert _text(result) == "next-loop"
    assert psutil.pid_exists(pid)
    asyncio.run(runtime.close())
    assert not psutil.pid_exists(pid)


@pytest.mark.anyio
async def test_stdio_is_lazy_connects_once_and_closes_child(tmp_path: Path) -> None:
    runtime, pid_file = _runtime(tmp_path)
    assert not pid_file.exists()

    first, second = await asyncio.gather(
        runtime.connect("fixture", None),
        runtime.connect("fixture", None),
    )

    assert first is second
    assert [tool.name for tool in (await first.list_tools(None)).tools] == [
        "echo",
        "configured_secret_name",
        "slow",
        "large_output",
        "controlled_error",
        "emit_tools_changed",
    ]
    assert _text(await first.call_tool("echo", {"text": "stdio-sentinel"}, None)) == "stdio-sentinel"
    assert _text(await first.call_tool("configured_secret_name", {}, None)) == "present"
    pid = int(pid_file.read_text(encoding="ascii"))
    assert psutil.pid_exists(pid)

    await runtime.close()
    await runtime.close()

    assert not psutil.pid_exists(pid)


@pytest.mark.anyio
async def test_stdio_exposes_metadata_and_coalesces_tools_changed(tmp_path: Path) -> None:
    runtime, _pid_file = _runtime(tmp_path)
    connected = await runtime.connect("fixture", None)

    assert connected.metadata.protocol_version
    assert connected.metadata.instructions == "Use fixture tools only for deterministic tests."
    assert runtime.take_dirty_servers() == ()

    await connected.call_tool("emit_tools_changed", {}, None)

    assert runtime.take_dirty_servers() == ("fixture",)
    assert runtime.take_dirty_servers() == ()

    for _index in range(100):
        runtime._mark_tools_dirty("fixture")  # noqa: SLF001 - deterministic coalescing seam.
    runtime._mark_tools_dirty("zeta")  # noqa: SLF001
    runtime._mark_tools_dirty("alpha")  # noqa: SLF001
    assert runtime.take_dirty_servers() == ("alpha", "fixture", "zeta")
    await runtime.close()


@pytest.mark.anyio
async def test_legacy_tools_changed_message_marks_server_dirty(tmp_path: Path) -> None:
    runtime, _pid_file = _runtime(tmp_path)
    resolved = resolve_server(runtime._servers["fixture"], {"FIXTURE_TOKEN": "configured"})  # noqa: SLF001
    actor = _ServerActor(resolved, runtime._mark_tools_dirty, runtime._record_notification_error)  # noqa: SLF001

    await actor._handle_message(ToolListChangedNotification())  # noqa: SLF001

    assert runtime.take_dirty_servers() == ("fixture",)


@pytest.mark.anyio
async def test_modern_listener_marks_dirty_and_actor_owned_task_is_cancellable(tmp_path: Path) -> None:
    runtime, _pid_file = _runtime(tmp_path)
    resolved = resolve_server(runtime._servers["fixture"], {"FIXTURE_TOKEN": "configured"})  # noqa: SLF001
    actor = _ServerActor(resolved, runtime._mark_tools_dirty, runtime._record_notification_error)  # noqa: SLF001
    delivered = asyncio.Event()

    class Subscription:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not delivered.is_set():
                delivered.set()
                return ToolsListChanged()
            await asyncio.Event().wait()

    class ListenContext:
        async def __aenter__(self):
            return Subscription()

        async def __aexit__(self, *_args):
            return None

    class ModernClient:
        def listen(self, *, tools_list_changed: bool = False):
            assert tools_list_changed is True
            return ListenContext()

    task = asyncio.create_task(actor._listen_for_tool_changes(ModernClient()))  # noqa: SLF001
    await delivered.wait()
    await asyncio.sleep(0)

    assert runtime.take_dirty_servers() == ("fixture",)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_modern_listener_loss_is_bounded_diagnostic_and_not_raised(tmp_path: Path) -> None:
    runtime, _pid_file = _runtime(tmp_path)
    resolved = resolve_server(runtime._servers["fixture"], {"FIXTURE_TOKEN": "configured"})  # noqa: SLF001
    actor = _ServerActor(resolved, runtime._mark_tools_dirty, runtime._record_notification_error)  # noqa: SLF001

    class LostContext:
        async def __aenter__(self):
            raise RuntimeError("sensitive transport detail")

        async def __aexit__(self, *_args):
            return None

    class ModernClient:
        def listen(self, *, tools_list_changed: bool = False):
            assert tools_list_changed is True
            return LostContext()

    await actor._listen_for_tool_changes(ModernClient())  # noqa: SLF001

    assert runtime.take_notification_errors() == (("fixture", "RuntimeError"),)
    assert runtime.take_notification_errors() == ()


@pytest.mark.anyio
async def test_stdio_timeout_does_not_poison_later_calls(tmp_path: Path) -> None:
    runtime, _pid_file = _runtime(tmp_path)
    connected = await runtime.connect("fixture", None)
    connected._actor.resolved = replace(  # noqa: SLF001 - isolate request-timeout recovery from process startup.
        connected._actor.resolved,  # noqa: SLF001
        request_timeout_ms=250,
    )

    with pytest.raises(TimeoutError, match="fixture.*call_tool.*250"):
        await connected.call_tool("slow", {"delay_ms": 5_000}, None)

    reconnected = await runtime.connect("fixture", None)
    assert reconnected is not connected
    assert _text(await reconnected.call_tool("echo", {"text": "after-timeout"}, None)) == "after-timeout"
    await runtime.close()


@pytest.mark.anyio
async def test_stdio_abort_cancels_request_and_keeps_server_usable(tmp_path: Path) -> None:
    runtime, _pid_file = _runtime(tmp_path)
    connected = await runtime.connect("fixture", None)
    signal = AbortSignal()
    call = asyncio.create_task(connected.call_tool("slow", {"delay_ms": 5_000}, signal))
    await asyncio.sleep(0.05)

    signal.abort()

    with pytest.raises(asyncio.CancelledError):
        await call
    reconnected = await runtime.connect("fixture", None)
    assert reconnected is not connected
    assert _text(await reconnected.call_tool("echo", {"text": "after-abort"}, None)) == "after-abort"
    await runtime.close()


@pytest.mark.anyio
async def test_missing_stdio_command_does_not_leave_cached_connection(tmp_path: Path) -> None:
    server = ServerConfig(
        name="missing",
        source_path=tmp_path / "mcp.json",
        command=f"missing-travis234-mcp-{os.getpid()}",
    )
    runtime = McpRuntime({"missing": server}, {})

    with pytest.raises(OSError):
        await runtime.connect("missing", None)
    with pytest.raises(OSError):
        await runtime.connect("missing", None)

    await runtime.close()
