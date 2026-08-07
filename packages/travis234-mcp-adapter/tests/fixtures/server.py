from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server import MCPServer


server = MCPServer("travis234-mcp-adapter-fixture")


@server.tool()
def echo(text: str) -> str:
    """Return the supplied text."""
    return text


@server.tool()
def configured_secret_name() -> str:
    """Report only whether the fixture received its configured token."""
    return "present" if os.environ.get("FIXTURE_TOKEN") else "missing"


@server.tool()
async def slow(delay_ms: int) -> str:
    """Wait for a controlled duration."""
    await asyncio.sleep(delay_ms / 1_000)
    return "finished"


if __name__ == "__main__":
    pid_file = os.environ.get("FIXTURE_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    asyncio.run(server.run_stdio_async())
