from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
from typing import Any, AsyncIterator, Literal, TextIO

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from travis.agent.types import AbortSignal
from travis234_mcp_adapter.config import ResolvedServer, ServerConfig, resolve_server


Operation = Literal[
    "list_tools",
    "call_tool",
    "list_resources",
    "list_resource_templates",
    "read_resource",
    "list_prompts",
    "get_prompt",
]
RuntimeState = Literal[
    "disconnected",
    "connecting",
    "connected",
    "reconnecting",
    "failed",
    "closing",
]


@dataclass(frozen=True)
class ConnectionStatus:
    state: RuntimeState
    updated_at_ms: int
    connected_at_ms: int | None = None
    last_error_type: str | None = None
    last_error_at_ms: int | None = None


class _LoopOwner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def started(self) -> bool:
        return self._thread is not None

    async def run(self, awaitable: Awaitable[object]) -> object:
        loop = self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(awaitable, loop)
        return await asyncio.wrap_future(future)

    async def stop(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join, 5)
        if thread.is_alive():
            raise RuntimeError("MCP runtime loop did not stop")
        with self._lock:
            self._loop = None
            self._thread = None
            self._ready.clear()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._thread is None:
                self._ready.clear()
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="travis234-mcp-runtime",
                    daemon=True,
                )
                self._thread.start()
            thread = self._thread
        if not self._ready.wait(5):
            raise RuntimeError("MCP runtime loop did not start")
        with self._lock:
            loop = self._loop
        if loop is None or thread is None or not thread.is_alive():
            raise RuntimeError("MCP runtime loop is unavailable")
        return loop

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()


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
    def __init__(self, actor: _ServerActor, runtime: McpRuntime) -> None:
        self._actor = actor
        self._runtime = runtime

    @property
    def name(self) -> str:
        return self._actor.resolved.name

    async def list_tools(self, signal: AbortSignal | None, cursor: str | None = None):
        return await self._runtime._request(
            self._actor,
            "list_tools",
            (cursor,),
            signal,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        signal: AbortSignal | None,
    ):
        return await self._runtime._request(
            self._actor,
            "call_tool",
            (name, arguments),
            signal,
        )

    async def list_resources(self, signal: AbortSignal | None, cursor: str | None = None):
        return await self._runtime._request(
            self._actor,
            "list_resources",
            (cursor,),
            signal,
        )

    async def list_resource_templates(
        self,
        signal: AbortSignal | None,
        cursor: str | None = None,
    ):
        return await self._runtime._request(
            self._actor,
            "list_resource_templates",
            (cursor,),
            signal,
        )

    async def read_resource(self, uri: str, signal: AbortSignal | None):
        return await self._runtime._request(
            self._actor,
            "read_resource",
            (uri,),
            signal,
        )

    async def list_prompts(self, signal: AbortSignal | None, cursor: str | None = None):
        return await self._runtime._request(
            self._actor,
            "list_prompts",
            (cursor,),
            signal,
        )

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str],
        signal: AbortSignal | None,
    ):
        return await self._runtime._request(
            self._actor,
            "get_prompt",
            (name, arguments),
            signal,
        )


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
            async with _open_client(self.resolved) as client:
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
        if request.operation == "call_tool":
            name, arguments = request.arguments
            return await client.call_tool(str(name), dict(arguments))
        if request.operation == "list_resources":
            return await client.list_resources(cursor=request.arguments[0])
        if request.operation == "list_resource_templates":
            return await client.list_resource_templates(cursor=request.arguments[0])
        if request.operation == "read_resource":
            return await client.read_resource(str(request.arguments[0]))
        if request.operation == "list_prompts":
            return await client.list_prompts(cursor=request.arguments[0])
        name, arguments = request.arguments
        return await client.get_prompt(str(name), dict(arguments))

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
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._servers = dict(servers)
        self._get_environ = environ if callable(environ) else lambda: environ
        self._sleep = sleep or asyncio.sleep
        self._actors: dict[str, _ServerActor] = {}
        self._connected: dict[str, ConnectedServer] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reconnect_tasks: dict[str, asyncio.Task[ConnectedServer]] = {}
        self._loop_owner = _LoopOwner()
        self._status_lock = threading.Lock()
        self._connected_names: set[str] = set()
        now = _timestamp_ms()
        self._statuses = {
            name: ConnectionStatus(state="disconnected", updated_at_ms=now)
            for name in self._servers
        }
        self._close_lock = threading.Lock()
        self._closed = False

    async def connect(self, name: str, signal: AbortSignal | None) -> ConnectedServer:
        if self._closed:
            raise RuntimeError("MCP runtime is closed")
        return await self._loop_owner.run(self._connect_on_owner(name, signal))

    async def reconnect(self, name: str, signal: AbortSignal | None) -> ConnectedServer:
        if self._closed:
            raise RuntimeError("MCP runtime is closed")
        return await self._loop_owner.run(self._reconnect_on_owner(name, signal))

    async def _reconnect_on_owner(
        self,
        name: str,
        signal: AbortSignal | None,
    ) -> ConnectedServer:
        if name not in self._servers:
            raise KeyError(f'Unknown MCP server "{name}"')
        existing = self._reconnect_tasks.get(name)
        if existing is None:
            existing = asyncio.create_task(
                self._perform_reconnect(name, signal),
                name=f"travis234-mcp-reconnect-{name}",
            )
            self._reconnect_tasks[name] = existing
        try:
            return await asyncio.shield(existing)
        except asyncio.CancelledError:
            if existing.cancelled() and not self._closed:
                self._set_status(name, "disconnected")
            raise
        finally:
            if existing.done() and self._reconnect_tasks.get(name) is existing:
                self._reconnect_tasks.pop(name, None)

    async def _perform_reconnect(
        self,
        name: str,
        signal: AbortSignal | None,
    ) -> ConnectedServer:
        current = self._actors.get(name)
        if current is not None:
            await self._discard_on_owner(current)
        reconnect = self._servers[name].reconnect
        last_error: BaseException | None = None
        for attempt in range(reconnect.max_attempts):
            self._set_status(name, "reconnecting")
            try:
                return await self._connect_on_owner(
                    name,
                    signal,
                    connecting_state="reconnecting",
                )
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                last_error = error
                if attempt + 1 >= reconnect.max_attempts:
                    raise
                delay = reconnect.base_delay_ms * (2**attempt) / 1_000
                self._set_status(name, "reconnecting")
                await _await_controlled(
                    self._sleep(delay),
                    signal,
                    None,
                    name,
                    "reconnect",
                )
        assert last_error is not None
        raise last_error

    async def _connect_on_owner(
        self,
        name: str,
        signal: AbortSignal | None,
        *,
        connecting_state: RuntimeState = "connecting",
    ) -> ConnectedServer:
        server = self._servers.get(name)
        if server is None:
            raise KeyError(f'Unknown MCP server "{name}"')
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if self._closed:
                raise RuntimeError("MCP runtime is closed")
            existing = self._connected.get(name)
            if existing is not None:
                return existing
            self._set_status(name, connecting_state)
            try:
                resolved = resolve_server(server, self._get_environ())
                actor = _ServerActor(resolved)
                self._actors[name] = actor
                await actor.start(signal)
            except asyncio.CancelledError:
                self._actors.pop(name, None)
                if not self._closed:
                    self._set_status(name, "disconnected")
                raise
            except BaseException as error:
                self._actors.pop(name, None)
                self._set_status(name, "failed", error=error)
                raise
            connected = ConnectedServer(actor, self)
            self._connected[name] = connected
            self._set_status(name, "connected")
            return connected

    async def _request(
        self,
        actor: _ServerActor,
        operation: Operation,
        arguments: tuple[object, ...],
        signal: AbortSignal | None,
    ) -> object:
        try:
            return await self._loop_owner.run(actor.request(operation, arguments, signal))
        except asyncio.CancelledError:
            try:
                await self._loop_owner.run(self._discard_on_owner(actor))
            except (asyncio.CancelledError, RuntimeError):
                pass
            raise
        except BaseException as error:
            name = actor.resolved.name
            try:
                await self._loop_owner.run(self._discard_on_owner(actor, error=error))
            except (asyncio.CancelledError, RuntimeError):
                pass
            server = self._servers.get(name)
            if server is not None and server.reconnect.automatic and not self._closed:
                try:
                    await self._loop_owner.run(self._reconnect_on_owner(name, signal))
                except BaseException:
                    pass
            raise

    async def _discard_on_owner(
        self,
        actor: _ServerActor,
        *,
        error: BaseException | None = None,
    ) -> None:
        name = actor.resolved.name
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if self._actors.get(name) is not actor:
                return
            self._actors.pop(name, None)
            self._connected.pop(name, None)
            self._set_status(name, "failed" if error is not None else "disconnected", error=error)
            await actor.close()

    async def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        for name in self._servers:
            self._set_status(name, "closing")
        if not self._loop_owner.started:
            return
        await self._loop_owner.run(self._close_on_owner())
        await self._loop_owner.stop()

    async def _close_on_owner(self) -> None:
        actors = list(self._actors.values())
        self._actors.clear()
        self._connected.clear()
        reconnect_tasks = tuple(self._reconnect_tasks.values())
        self._reconnect_tasks.clear()
        for task in reconnect_tasks:
            task.cancel()
        with self._status_lock:
            self._connected_names.clear()
        pending = [actor.close(force=True) for actor in actors]
        if reconnect_tasks:
            pending.extend(reconnect_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def is_connected(self, name: str) -> bool:
        with self._status_lock:
            return name in self._connected_names

    def status(self, name: str) -> ConnectionStatus:
        with self._status_lock:
            try:
                return self._statuses[name]
            except KeyError as error:
                raise KeyError(f'Unknown MCP server "{name}"') from error

    def _set_status(
        self,
        name: str,
        state: RuntimeState,
        *,
        error: BaseException | None = None,
    ) -> None:
        now = _timestamp_ms()
        with self._status_lock:
            previous = self._statuses[name]
            connected_at = now if state == "connected" else previous.connected_at_ms
            last_error_type = previous.last_error_type
            last_error_at = previous.last_error_at_ms
            if error is not None:
                last_error_type = type(error).__name__[:80]
                last_error_at = now
            self._statuses[name] = ConnectionStatus(
                state=state,
                updated_at_ms=now,
                connected_at_ms=connected_at,
                last_error_type=last_error_type,
                last_error_at_ms=last_error_at,
            )
            if state == "connected":
                self._connected_names.add(name)
            else:
                self._connected_names.discard(name)


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


def _timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


@asynccontextmanager
async def _open_client(resolved: ResolvedServer) -> AsyncIterator[Client]:
    if resolved.command is not None:
        params = StdioServerParameters(
            command=resolved.command,
            args=list(resolved.args),
            env=dict(resolved.env),
            cwd=resolved.cwd,
        )
        with Path(os.devnull).open("w", encoding="utf-8") as errlog:
            async with Client(stdio_client(params, errlog=_as_text_io(errlog))) as client:
                yield client
        return

    if resolved.url is None:
        raise RuntimeError(f'MCP server "{resolved.name}" has no transport')
    timeout_seconds = (
        resolved.request_timeout_ms / 1_000
        if resolved.request_timeout_ms is not None and resolved.request_timeout_ms > 0
        else None
    )
    timeout = (
        httpx2.Timeout(timeout_seconds)
        if timeout_seconds is not None
        else httpx2.Timeout(30.0, read=300.0)
    )
    async with httpx2.AsyncClient(
        headers=dict(resolved.headers),
        timeout=timeout,
        follow_redirects=True,
    ) as http_client:
        transport = streamable_http_client(resolved.url, http_client=http_client)
        async with Client(transport) as client:
            yield client
