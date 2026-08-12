from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server import MCPServer


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


def create_server() -> MCPServer:
    created = MCPServer("travis234-mcp-adapter-fixture")
    created.tool()(echo)
    created.tool()(configured_secret_name)
    created.tool()(slow)
    created.tool()(large_output)
    created.tool()(controlled_error)
    return created


server = create_server()


if __name__ == "__main__":
    pid_file = os.environ.get("FIXTURE_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    asyncio.run(server.run_stdio_async())
