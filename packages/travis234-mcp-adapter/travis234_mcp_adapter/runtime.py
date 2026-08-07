from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Literal, TextIO

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from travis.agent.types import AbortSignal
from travis234_mcp_adapter.config import ResolvedServer, ServerConfig, resolve_server


Operation = Literal["list_tools", "call_tool"]


@dataclass
class _ActorRequest:
    operation: Operation
    arguments: tuple[object, ...]
    future: asyncio.Future[object]
    task: asyncio.Task[object] | None = None
    cancel_requested: bool = False

    def cancel(self) -> None:
        self.cancel_requested = True
        if self.task is not None:
            self.task.cancel()
        if not self.future.done():
            self.future.cancel()


class ConnectedServer:
    def __init__(self, actor: _ServerActor) -> None:
        self._actor = actor

    @property
    def name(self) -> str:
        return self._actor.resolved.name

    async def list_tools(self, signal: AbortSignal | None, cursor: str | None = None):
        return await self._actor.request("list_tools", (cursor,), signal)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        signal: AbortSignal | None,
    ):
        return await self._actor.request("call_tool", (name, arguments), signal)


class _ServerActor:
    def __init__(self, resolved: ResolvedServer) -> None:
        self.resolved = resolved
        self._queue: asyncio.Queue[_ActorRequest | None] = asyncio.Queue()
        self._ready: asyncio.Future[None] | None = None
        self._owner: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[object]] = set()
        self._closing = False

    async def start(self, signal: AbortSignal | None) -> None:
        if self._owner is None:
            loop = asyncio.get_running_loop()
            self._ready = loop.create_future()
            self._owner = asyncio.create_task(
                self._run(),
                name=f"travis234-mcp-{self.resolved.name}",
            )
        assert self._ready is not None
        try:
            await _await_controlled(
                asyncio.shield(self._ready),
                signal,
                self.resolved.request_timeout_ms,
                self.resolved.name,
                "connect",
            )
        except BaseException:
            await self.close(force=True)
            raise

    async def request(
        self,
        operation: Operation,
        arguments: tuple[object, ...],
        signal: AbortSignal | None,
    ) -> object:
        if self._closing or self._owner is None or self._owner.done():
            raise RuntimeError(f'MCP server "{self.resolved.name}" is not connected')
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        request = _ActorRequest(operation=operation, arguments=arguments, future=future)
        self._queue.put_nowait(request)
        try:
            return await _await_controlled(
                asyncio.shield(future),
                signal,
                self.resolved.request_timeout_ms,
                self.resolved.name,
                operation,
            )
        except BaseException:
            request.cancel()
            raise

    async def close(self, *, force: bool = False) -> None:
        owner = self._owner
        if owner is None:
            return
        self._closing = True
        if force:
            owner.cancel()
        elif not owner.done():
            self._queue.put_nowait(None)
        try:
            async with asyncio.timeout(5):
                await owner
        except asyncio.CancelledError:
            if not force:
                raise
        except TimeoutError:
            owner.cancel()
            try:
                await owner
            except asyncio.CancelledError:
                pass
        finally:
            self._owner = None

    async def _run(self) -> None:
        ready = self._ready
        assert ready is not None
        try:
            if self.resolved.command is None:
                raise RuntimeError("Streamable HTTP transport is not implemented")
            params = StdioServerParameters(
                command=self.resolved.command,
                args=list(self.resolved.args),
                env=dict(self.resolved.env),
                cwd=self.resolved.cwd,
            )
            with Path(os.devnull).open("w", encoding="utf-8") as errlog:
                async with Client(stdio_client(params, errlog=_as_text_io(errlog))) as client:
                    if not ready.done():
                        ready.set_result(None)
                    await self._serve(client)
        except BaseException as error:
            if not ready.done():
                if isinstance(error, asyncio.CancelledError):
                    ready.cancel()
                else:
                    ready.set_exception(error)
            self._fail_queued(error)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            for task in tuple(self._active):
                task.cancel()
            if self._active:
                await asyncio.gather(*self._active, return_exceptions=True)

    async def _serve(self, client: Client) -> None:
        while True:
            request = await self._queue.get()
            if request is None:
                return
            task = asyncio.create_task(self._invoke(client, request))
            request.task = task
            self._active.add(task)
            task.add_done_callback(lambda done, item=request: self._finish(item, done))
            if request.cancel_requested:
                task.cancel()

    async def _invoke(self, client: Client, request: _ActorRequest) -> object:
        if request.operation == "list_tools":
            cursor = request.arguments[0]
            return await client.list_tools(cursor=cursor)
        name, arguments = request.arguments
        return await client.call_tool(str(name), dict(arguments))

    def _finish(self, request: _ActorRequest, task: asyncio.Task[object]) -> None:
        self._active.discard(task)
        try:
            result = task.result()
        except asyncio.CancelledError:
            if not request.future.done():
                request.future.cancel()
        except BaseException as error:
            if not request.future.done():
                request.future.set_exception(error)
        else:
            if not request.future.done():
                request.future.set_result(result)

    def _fail_queued(self, error: BaseException) -> None:
        while True:
            try:
                request = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if request is not None and not request.future.done():
                if isinstance(error, asyncio.CancelledError):
                    request.future.cancel()
                else:
                    request.future.set_exception(error)


class McpRuntime:
    def __init__(
        self,
        servers: Mapping[str, ServerConfig],
        environ: Mapping[str, str] | Callable[[], Mapping[str, str]],
    ) -> None:
        self._servers = dict(servers)
        self._get_environ = environ if callable(environ) else lambda: environ
        self._actors: dict[str, _ServerActor] = {}
        self._connected: dict[str, ConnectedServer] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    async def connect(self, name: str, signal: AbortSignal | None) -> ConnectedServer:
        if self._closed:
            raise RuntimeError("MCP runtime is closed")
        server = self._servers.get(name)
        if server is None:
            raise KeyError(f'Unknown MCP server "{name}"')
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            existing = self._connected.get(name)
            if existing is not None:
                return existing
            resolved = resolve_server(server, self._get_environ())
            actor = _ServerActor(resolved)
            self._actors[name] = actor
            try:
                await actor.start(signal)
            except BaseException:
                self._actors.pop(name, None)
                raise
            connected = ConnectedServer(actor)
            self._connected[name] = connected
            return connected

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        actors = list(self._actors.values())
        self._actors.clear()
        self._connected.clear()
        if actors:
            await asyncio.gather(*(actor.close() for actor in actors), return_exceptions=True)


async def _await_controlled(
    awaitable: Awaitable[object],
    signal: AbortSignal | None,
    timeout_ms: int | None,
    server_name: str,
    operation: str,
) -> object:
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    if current is None:
        raise RuntimeError("MCP requests require an asyncio task")
    unsubscribe = None
    if signal is not None:
        unsubscribe = signal.add_callback(
            lambda: loop.call_soon_threadsafe(current.cancel)
        )
    try:
        if timeout_ms is not None and timeout_ms > 0:
            try:
                async with asyncio.timeout(timeout_ms / 1_000):
                    return await awaitable
            except TimeoutError as error:
                raise TimeoutError(
                    f'MCP server "{server_name}" {operation} timed out after {timeout_ms} ms'
                ) from error
        return await awaitable
    finally:
        if unsubscribe is not None:
            unsubscribe()


def _as_text_io(stream: TextIO) -> TextIO:
    return stream
