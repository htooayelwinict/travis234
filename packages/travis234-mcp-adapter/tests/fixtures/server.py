from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context


def echo(text: str) -> str:
    """Return the supplied text."""
    return text


def configured_secret_name() -> str:
    """Report only whether the fixture received its configured token."""
    return "present" if os.environ.get("FIXTURE_TOKEN") else "missing"


async def slow(delay_ms: int) -> str:
    """Wait for a controlled duration."""
    await asyncio.sleep(delay_ms / 1_000)
    return "finished"


def large_output(size: int) -> str:
    """Return controlled oversized text."""
    return "x" * size


def controlled_error() -> str:
    """Return a controlled MCP tool error."""
    raise ValueError("controlled fixture failure")


async def emit_tools_changed(ctx: Context) -> str:
    """Emit one deterministic tools-list change notification."""
    await ctx.notify_tools_changed()
    return "emitted"


def create_server() -> MCPServer:
    created = MCPServer(
        "travis234-mcp-adapter-fixture",
        instructions="Use fixture tools only for deterministic tests.",
    )
    created.tool()(echo)
    created.tool()(configured_secret_name)
    created.tool()(slow)
    created.tool()(large_output)
    created.tool()(controlled_error)
    created.tool()(emit_tools_changed)
    return created


server = create_server()


if __name__ == "__main__":
    pid_file = os.environ.get("FIXTURE_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    asyncio.run(server.run_stdio_async())
