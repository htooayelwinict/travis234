"""Bounded language-server roots, documents, restarts, and shutdown ownership."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from travis.agent.types import AbortSignal
from travis.coding_agent.language_services.config import select_server_config
from travis.coding_agent.language_services.documents import (
    DocumentSnapshot,
    DocumentTracker,
    PositionEncoding,
    position_to_server,
)
from travis.coding_agent.language_services.jsonrpc import JsonRpcProtocolError, JsonRpcStdioClient
from travis.coding_agent.language_services.types import (
    DocumentPosition,
    LanguageServerConfig,
    LanguageServiceLimits,
)


class LanguageServiceUnavailable(RuntimeError):
    pass


class _Client(Protocol):
    initialized: bool
    process_id: int | None

    async def start(self) -> None: ...
    async def request(self, method: str, params: object, signal: AbortSignal | None = None) -> object: ...
    async def notify(self, method: str, params: object) -> None: ...
    async def close(self) -> None: ...


@dataclass
class _ServerState:
    config: LanguageServerConfig
    root: Path
    client: _Client
    generation: int = 0
    position_encoding: PositionEncoding = "utf-16"
    last_used: float = field(default_factory=time.monotonic)
    active_requests: int = 0
    document_versions: dict[Path, int] = field(default_factory=dict)
    restarts: deque[float] = field(default_factory=deque)
    restart_exhausted: bool = False
    document_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    restart_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


ClientFactory = Callable[..., _Client]


class LanguageServiceManager:
    def __init__(
        self,
        workspace: str | Path,
        configs: list[LanguageServerConfig],
        *,
        limits: LanguageServiceLimits | None = None,
        client_factory: ClientFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.configs = list(configs)
        self.limits = limits or LanguageServiceLimits()
        self.documents = DocumentTracker(self.workspace)
        self._client_factory = client_factory or self._default_client_factory
        self._clock = clock
        self._servers: dict[tuple[str, Path], _ServerState] = {}
        self._lock = asyncio.Lock()
        self.config_generation = 1
        self._close_requested = False
        self._closed = False

    def _default_client_factory(self, config: LanguageServerConfig, root: Path, **kwargs) -> _Client:
        return JsonRpcStdioClient(
            config.command,
            config.args,
            cwd=root,
            limits=self.limits,
            on_notification=kwargs.get("on_notification"),
        )

    async def for_path(self, path: str | Path) -> _ServerState:
        if self._close_requested:
            raise LanguageServiceUnavailable("language service manager is closed")
        try:
            config, root = select_server_config(self.configs, path, self.workspace)
        except (LookupError, ValueError) as error:
            raise LanguageServiceUnavailable(str(error)) from error
        return await self._state_for(config, root)

    async def _state_for(self, config: LanguageServerConfig, root: Path) -> _ServerState:
        key = (config.name, root)
        async with self._lock:
            if self._close_requested:
                raise LanguageServiceUnavailable("language service manager is closed")
            state = self._servers.get(key)
            if state is not None:
                state.last_used = self._clock()
                return state
            await self._evict_if_needed()
            client = self._client_factory(config, root, on_notification=lambda method, params: None)
            state = _ServerState(config=config, root=root, client=client)
            self._servers[key] = state
            try:
                await self._start_state(state)
            except BaseException:
                self._servers.pop(key, None)
                await client.close()
                raise
            if self._close_requested:
                self._servers.pop(key, None)
                await self._close_state(state)
                raise LanguageServiceUnavailable("language service manager is closed")
            return state

    async def workspace_request(
        self,
        method: str,
        params: object,
        signal: AbortSignal | None = None,
    ) -> list[object]:
        results: list[object] = []
        for config in self.configs:
            state = await self._state_for(config, self.workspace)
            state.active_requests += 1
            generation = state.generation
            try:
                result = await state.client.request(method, params, signal=signal)
                if generation != state.generation:
                    raise JsonRpcProtocolError("stale language server generation")
                results.append(result)
            except JsonRpcProtocolError as error:
                if signal is not None and signal.aborted:
                    raise asyncio.CancelledError from error
                await self._restart(state, failed_generation=generation)
                results.append(await state.client.request(method, params, signal=signal))
            finally:
                state.active_requests = max(0, state.active_requests - 1)
                state.last_used = self._clock()
        return results

    async def prepare_position(
        self,
        path: str | Path,
        line: int,
        character: int,
    ) -> dict[str, int]:
        state = await self.for_path(path)
        snapshot = self.documents.open_or_update(path)
        converted = position_to_server(
            snapshot.text,
            DocumentPosition(line, character),
            state.position_encoding,
        )
        return {"line": converted.line, "character": converted.character}

    def response_context(self, path: str | Path) -> dict[str, object]:
        config, root = select_server_config(self.configs, path, self.workspace)
        state = self._servers.get((config.name, root))
        if state is None:
            raise LanguageServiceUnavailable("language server is not active")
        snapshot = self.documents.snapshot(path)
        return {
            "generation": state.generation,
            "configGeneration": self.config_generation,
            "positionEncoding": state.position_encoding,
            "documentHash": snapshot.sha256,
        }

    async def _start_state(self, state: _ServerState) -> None:
        await state.client.start()
        initialize = await state.client.request(
            "initialize",
            {
                "processId": None,
                "rootUri": state.root.as_uri(),
                "workspaceFolders": [{"uri": state.root.as_uri(), "name": state.root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16", "utf-8", "utf-32"]},
                    "textDocument": {"synchronization": {"dynamicRegistration": False}},
                    "workspace": {"workspaceFolders": True},
                },
                "initializationOptions": state.config.initialization_options,
            },
        )
        capabilities = initialize.get("capabilities", {}) if isinstance(initialize, dict) else {}
        encoding = capabilities.get("positionEncoding") if isinstance(capabilities, dict) else None
        state.position_encoding = encoding if encoding in {"utf-8", "utf-16", "utf-32"} else "utf-16"
        state.client.initialized = True
        await state.client.notify("initialized", {})
        state.generation += 1
        state.document_versions.clear()
        state.last_used = self._clock()

    async def _evict_if_needed(self) -> None:
        if len(self._servers) < self.limits.max_active_servers:
            return
        idle = [(state.last_used, key, state) for key, state in self._servers.items() if state.active_requests == 0]
        if not idle:
            raise LanguageServiceUnavailable("all configured language servers are busy")
        _used, key, state = min(idle, key=lambda item: (item[0], item[1][0], str(item[1][1])))
        self._servers.pop(key, None)
        await self._close_state(state)

    async def request(
        self,
        path: str | Path,
        method: str,
        params: object,
        signal: AbortSignal | None = None,
    ) -> object:
        while True:
            state = await self.for_path(path)
            state.active_requests += 1
            state.last_used = self._clock()
            generation = state.generation
            try:
                async with state.document_lock:
                    await self._sync_document(state, path)
                result = await state.client.request(method, params, signal=signal)
                if generation != state.generation:
                    raise JsonRpcProtocolError("stale language server generation")
                return result
            except JsonRpcProtocolError as error:
                if signal is not None and signal.aborted:
                    raise asyncio.CancelledError from error
                await self._restart(state, failed_generation=generation)
            finally:
                state.active_requests = max(0, state.active_requests - 1)
                state.last_used = self._clock()

    async def _sync_document(self, state: _ServerState, path: str | Path) -> DocumentSnapshot:
        snapshot = self.documents.open_or_update(path)
        known_version = state.document_versions.get(snapshot.path)
        language_id = state.config.extensions.get(snapshot.path.suffix.lower(), state.config.languages[0])
        if known_version is None:
            await state.client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": snapshot.uri,
                        "languageId": language_id,
                        "version": snapshot.version,
                        "text": snapshot.text,
                    }
                },
            )
        elif known_version != snapshot.version:
            await state.client.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": snapshot.uri, "version": snapshot.version},
                    "contentChanges": [{"text": snapshot.text}],
                },
            )
        state.document_versions[snapshot.path] = snapshot.version
        return snapshot

    async def mark_saved(self, path: str | Path) -> DocumentSnapshot:
        state = await self.for_path(path)
        snapshot = self.documents.mark_saved(path)
        await self._sync_document(state, path)
        await state.client.notify(
            "textDocument/didSave",
            {"textDocument": {"uri": snapshot.uri}, "text": snapshot.text},
        )
        return snapshot

    async def _restart(self, state: _ServerState, *, failed_generation: int) -> None:
        async with state.restart_lock:
            if state.generation != failed_generation:
                return
            now = self._clock()
            cutoff = now - self.limits.restart_window_seconds
            while state.restarts and state.restarts[0] < cutoff:
                state.restarts.popleft()
            if len(state.restarts) >= self.limits.max_restarts:
                state.restart_exhausted = True
                raise LanguageServiceUnavailable(
                    f"language server {state.config.name!r} exhausted its restart budget"
                )
            state.restarts.append(now)
            old_client = state.client
            await old_client.close()
            state.client = self._client_factory(
                state.config,
                state.root,
                on_notification=lambda method, params: None,
            )
            try:
                await self._start_state(state)
            except BaseException as error:
                await state.client.close()
                if isinstance(error, LanguageServiceUnavailable):
                    raise
                raise JsonRpcProtocolError("language server restart failed") from error

    async def reload(
        self,
        configs: list[LanguageServerConfig],
        *,
        project_trusted: bool = True,
    ) -> None:
        resolved = list(configs) if project_trusted else []
        async with self._lock:
            if resolved != self.configs:
                self.config_generation += 1
            self.configs = resolved
            configured = {config.name: config for config in resolved}
            stale = [
                key
                for key, state in self._servers.items()
                if configured.get(key[0]) != state.config
            ]
            for key in stale:
                state = self._servers.pop(key)
                await self._close_state(state)

    def status(self) -> dict[str, object]:
        servers = [
            {
                "name": state.config.name,
                "running": state.client.process_id is not None,
                "generation": state.generation,
                "positionEncoding": state.position_encoding,
                "restartExhausted": state.restart_exhausted,
            }
            for state in sorted(self._servers.values(), key=lambda item: (item.config.name, str(item.root)))
        ]
        return {
            "configured": len(self.configs),
            "active": len(self._servers),
            "configGeneration": self.config_generation,
            "servers": servers,
            "limits": {
                "maxActiveServers": self.limits.max_active_servers,
                "startupSeconds": self.limits.startup_timeout_seconds,
                "requestSeconds": self.limits.request_timeout_seconds,
                "maxRestarts": self.limits.max_restarts,
                "restartWindowSeconds": self.limits.restart_window_seconds,
            },
        }

    async def _close_state(self, state: _ServerState) -> None:
        for path in sorted(state.document_versions, key=str):
            try:
                snapshot = self.documents.snapshot(path)
                await state.client.notify("textDocument/didClose", {"textDocument": {"uri": snapshot.uri}})
            except (KeyError, JsonRpcProtocolError):
                pass
        state.document_versions.clear()
        await state.client.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._close_requested = True
        async with self._lock:
            states = list(self._servers.values())
            self._servers.clear()
            for state in states:
                await self._close_state(state)
            self._closed = True


__all__ = ["LanguageServiceManager", "LanguageServiceUnavailable"]
