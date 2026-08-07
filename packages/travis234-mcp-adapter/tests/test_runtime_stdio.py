from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys

import psutil
import pytest

from mcp.types import TextContent
from travis.agent.types import AbortSignal
from travis234_mcp_adapter.config import ServerConfig
from travis234_mcp_adapter.runtime import McpRuntime


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
    ]
    assert _text(await first.call_tool("echo", {"text": "stdio-sentinel"}, None)) == "stdio-sentinel"
    assert _text(await first.call_tool("configured_secret_name", {}, None)) == "present"
    pid = int(pid_file.read_text(encoding="ascii"))
    assert psutil.pid_exists(pid)

    await runtime.close()
    await runtime.close()

    assert not psutil.pid_exists(pid)


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
