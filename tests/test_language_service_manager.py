from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from travis.coding_agent.language_services.config import parse_language_servers
from travis.coding_agent.language_services.jsonrpc import JsonRpcProtocolError
from travis.coding_agent.language_services.manager import (
    LanguageServiceManager,
    LanguageServiceUnavailable,
)
from travis.coding_agent.language_services.types import LanguageServiceLimits


def _config(name: str, suffix: str = ".py", marker: str = "project.marker"):
    return parse_language_servers(
        [
            {
                "name": name,
                "command": "fixture-lsp",
                "languages": [name],
                "extensions": {suffix: name},
                "rootMarkers": [marker],
            }
        ]
    )[0]


class FakeClient:
    _next_pid = 5000

    def __init__(self, *, block_start: asyncio.Event | None = None, fail_requests: int = 0) -> None:
        self.block_start = block_start
        self.fail_requests = fail_requests
        self.started = 0
        self.closed = 0
        self.initialized = False
        self.requests: list[tuple[str, object]] = []
        self.notifications: list[tuple[str, object]] = []
        self.process_id: int | None = None

    async def start(self) -> None:
        self.started += 1
        if self.block_start is not None:
            await self.block_start.wait()
        FakeClient._next_pid += 1
        self.process_id = FakeClient._next_pid

    async def request(self, method: str, params: object, signal=None):
        self.requests.append((method, params))
        if method != "initialize" and self.fail_requests > 0:
            self.fail_requests -= 1
            raise JsonRpcProtocolError("language server closed unexpectedly")
        if method == "initialize":
            return {"capabilities": {"positionEncoding": "utf-8"}}
        return {"method": method, "params": params}

    async def notify(self, method: str, params: object) -> None:
        await asyncio.sleep(0)
        self.notifications.append((method, params))

    async def close(self) -> None:
        self.closed += 1
        self.process_id = None


def _run(coro):
    return asyncio.run(coro)


def test_manager_initializes_once_and_sequences_document_notifications(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("alpha\n", encoding="utf-8")
    clients: list[FakeClient] = []

    def factory(*_args, **_kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [_config("python")], client_factory=factory)
        await manager.request(source, "textDocument/hover", {"position": {"line": 0, "character": 1}})
        await manager.request(source, "textDocument/definition", {"position": {"line": 0, "character": 1}})
        source.write_text("beta\n", encoding="utf-8")
        await manager.request(source, "textDocument/hover", {"position": {"line": 0, "character": 1}})
        await manager.mark_saved(source)
        status = manager.status()
        assert status["configured"] == 1
        assert status["active"] == 1
        assert status["servers"][0]["generation"] == 1
        assert status["servers"][0]["positionEncoding"] == "utf-8"
        await manager.close()

    _run(scenario())
    assert len(clients) == 1
    client = clients[0]
    assert [method for method, _params in client.requests].count("initialize") == 1
    methods = [method for method, _params in client.notifications]
    assert methods == [
        "initialized",
        "textDocument/didOpen",
        "textDocument/didChange",
        "textDocument/didSave",
        "textDocument/didClose",
    ]


def test_concurrent_first_requests_start_one_server(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    clients: list[FakeClient] = []

    def factory(*_args, **_kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [_config("python")], client_factory=factory)
        await asyncio.gather(
            manager.request(source, "one", {}),
            manager.request(source, "two", {}),
        )
        await manager.close()

    _run(scenario())
    assert len(clients) == 1
    assert clients[0].started == 1
    assert [method for method, _params in clients[0].notifications].count("textDocument/didOpen") == 1


def test_manager_evicts_least_recently_used_idle_server_at_cap(tmp_path: Path) -> None:
    configs = [_config(name, f".{name}") for name in ("a", "b", "c", "d")]
    paths = []
    for name in ("a", "b", "c", "d"):
        path = tmp_path / f"main.{name}"
        path.write_text(name, encoding="utf-8")
        paths.append(path)
    clients: list[FakeClient] = []

    def factory(*_args, **_kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    async def scenario() -> None:
        manager = LanguageServiceManager(
            tmp_path,
            configs,
            client_factory=factory,
            limits=LanguageServiceLimits(max_active_servers=3),
        )
        for path in paths:
            await manager.request(path, "fixture", {})
        assert manager.status()["active"] == 3
        await manager.close()

    _run(scenario())
    assert clients[0].closed == 1


def test_manager_restarts_crashed_server_with_bounded_budget(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    clients: list[FakeClient] = []

    def factory(*_args, **_kwargs):
        client = FakeClient(fail_requests=1 if not clients else 0)
        clients.append(client)
        return client

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [_config("python")], client_factory=factory)
        result = await manager.request(source, "fixture", {})
        assert result["method"] == "fixture"
        assert manager.status()["servers"][0]["generation"] == 2
        await manager.close()

    _run(scenario())
    assert len(clients) == 2
    assert clients[0].closed == 1


def test_manager_reports_restart_exhaustion(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")

    def factory(*_args, **_kwargs):
        return FakeClient(fail_requests=1)

    async def scenario() -> None:
        manager = LanguageServiceManager(
            tmp_path,
            [_config("python")],
            client_factory=factory,
            limits=LanguageServiceLimits(max_restarts=2),
        )
        with pytest.raises(LanguageServiceUnavailable, match="restart budget"):
            await manager.request(source, "fixture", {})
        assert manager.status()["servers"][0]["restartExhausted"] is True
        await manager.close()

    _run(scenario())


def test_close_during_startup_closes_late_client(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    release = asyncio.Event()
    client = FakeClient(block_start=release)

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [_config("python")], client_factory=lambda *_a, **_k: client)
        request = asyncio.create_task(manager.request(source, "fixture", {}))
        await asyncio.sleep(0)
        close = asyncio.create_task(manager.close())
        release.set()
        await close
        with pytest.raises(LanguageServiceUnavailable, match="closed"):
            await request

    _run(scenario())
    assert client.closed == 1


def test_startup_timeout_is_propagated_and_partial_client_is_closed(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")

    class TimeoutClient(FakeClient):
        async def start(self) -> None:
            raise TimeoutError("language server startup timed out")

    client = TimeoutClient()

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [_config("python")], client_factory=lambda *_a, **_k: client)
        with pytest.raises(TimeoutError, match="startup"):
            await manager.request(source, "fixture", {})
        assert manager.status()["active"] == 0
        await manager.close()

    _run(scenario())
    assert client.closed == 1


def test_reload_and_trust_revocation_close_project_servers(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    client = FakeClient()

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [_config("python")], client_factory=lambda *_a, **_k: client)
        await manager.request(source, "fixture", {})
        await manager.reload([], project_trusted=False)
        assert manager.status()["configured"] == 0
        assert manager.status()["active"] == 0
        await manager.close()

    _run(scenario())
    assert client.closed == 1


def test_config_reload_replaces_same_name_server_and_advances_generation(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    clients: list[FakeClient] = []

    def factory(*_args, **_kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    original = _config("python")
    [replacement] = parse_language_servers(
        [
            {
                "name": "python",
                "command": "replacement-lsp",
                "languages": ["python"],
                "extensions": {".py": "python"},
                "rootMarkers": ["project.marker"],
            }
        ]
    )

    async def scenario() -> None:
        manager = LanguageServiceManager(tmp_path, [original], client_factory=factory)
        await manager.request(source, "fixture", {})
        await manager.reload([replacement])
        assert manager.status()["configGeneration"] == 2
        assert manager.status()["active"] == 0
        await manager.request(source, "fixture", {})
        await manager.close()

    _run(scenario())
    assert len(clients) == 2
    assert clients[0].closed == 1
