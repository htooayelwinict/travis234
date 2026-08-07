from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


@dataclass
class ConfigTree:
    home: Path
    cwd: Path

    def _write(self, path: Path, servers: dict[str, object]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
        return path

    def write_global_shared(self, name: str, value: object) -> Path:
        return self._write(self.home / ".config" / "mcp" / "mcp.json", {name: value})

    def write_global_travis(self, name: str, value: object) -> Path:
        return self._write(self.home / ".travis234" / "agent" / "mcp.json", {name: value})

    def write_project_shared(self, name: str, value: object) -> Path:
        return self._write(self.cwd / ".mcp.json", {name: value})

    def write_project_travis(self, name: str, value: object) -> Path:
        return self._write(self.cwd / ".travis234" / "mcp.json", {name: value})


@pytest.fixture
def config_tree(tmp_path: Path) -> ConfigTree:
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    return ConfigTree(home=home, cwd=cwd)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def mcp_http_server():
    import uvicorn

    from fixtures.server import create_server

    probe = {"requests": 0, "authorized": False}
    app = create_server().streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
    )

    async def observed(scope, receive, send):
        if scope["type"] == "http":
            probe["requests"] += 1
            headers = dict(scope.get("headers", []))
            probe["authorized"] = probe["authorized"] or b"authorization" in headers
        await app(scope, receive, send)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(observed, log_level="critical", lifespan="on")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    for _attempt in range(200):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(0.01)
    else:
        server.should_exit = True
        await task
        raise RuntimeError("HTTP fixture did not start")

    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", probe=probe)
    finally:
        server.should_exit = True
        await task
