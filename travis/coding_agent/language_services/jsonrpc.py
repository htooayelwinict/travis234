"""Bounded framed stdio JSON-RPC transport for language servers."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from travis.agent.types import AbortSignal
from travis.coding_agent.language_services.types import LanguageServiceLimits

_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
    }
)
_SENSITIVE_NAME = re.compile(
    r"(?:provider|token|key|secret|password|passwd|auth|cookie|credential)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)^([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|AUTH|COOKIE|CREDENTIAL)[A-Za-z0-9_]*)=.*$"
)
_MAX_HEADER_BYTES = 64 * 1024
_MAX_STDERR_BYTES = 16 * 1024


class JsonRpcProtocolError(RuntimeError):
    """The server transport closed or violated the bounded framing contract."""


class JsonRpcRequestError(RuntimeError):
    """A JSON-RPC error response from the server."""

    def __init__(self, code: int | None, message: str) -> None:
        self.code = code
        super().__init__(f"language server request failed ({code}): {message}")


def _minimal_environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {
        name: value
        for name in _ENV_ALLOWLIST
        if (value := os.environ.get(name)) is not None and not _SENSITIVE_NAME.search(name)
    }
    for name, value in (overrides or {}).items():
        if name in _ENV_ALLOWLIST and not _SENSITIVE_NAME.search(name) and isinstance(value, str):
            environment[name] = value
    return environment


def _redact_stderr(value: str) -> str:
    return _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


class JsonRpcStdioClient:
    def __init__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path,
        limits: LanguageServiceLimits | None = None,
        environment: Mapping[str, str] | None = None,
        on_notification: Callable[[str, object], None] | None = None,
    ) -> None:
        self.command = str(command)
        self.args = tuple(str(arg) for arg in args)
        self.cwd = Path(cwd).expanduser().resolve()
        self.limits = limits or LanguageServiceLimits()
        self.environment = _minimal_environment(environment)
        self.on_notification = on_notification
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[object]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_size = 0
        self._closing = False
        self.initialized = False

    @property
    def process_id(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.returncode is None else None

    @property
    def pending_request_count(self) -> int:
        return len(self._pending)

    async def start(self) -> None:
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return
            if self._closing:
                raise JsonRpcProtocolError("language server client is closed")
            try:
                process = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        self.command,
                        *self.args,
                        cwd=str(self.cwd),
                        env=self.environment,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=self.limits.startup_timeout_seconds,
                )
            except TimeoutError as error:
                raise TimeoutError("language server startup timed out") from error
            except OSError as error:
                raise JsonRpcProtocolError(f"language server failed to start: {type(error).__name__}") from error
            self._process = process
            self._reader_task = asyncio.create_task(self._reader_loop(), name="travis-lsp-jsonrpc-reader")
            self._stderr_task = asyncio.create_task(self._stderr_loop(), name="travis-lsp-stderr-reader")

    async def request(
        self,
        method: str,
        params: object,
        signal: AbortSignal | None = None,
    ) -> object:
        await self.start()
        if signal is not None and signal.aborted:
            raise asyncio.CancelledError
        loop = asyncio.get_running_loop()
        request_id = self._next_id
        self._next_id += 1
        response: asyncio.Future[object] = loop.create_future()
        aborted: asyncio.Future[None] = loop.create_future()
        self._pending[request_id] = response

        def mark_aborted() -> None:
            def resolve() -> None:
                if not aborted.done():
                    aborted.set_result(None)

            loop.call_soon_threadsafe(resolve)

        unsubscribe = signal.add_callback(mark_aborted) if signal is not None else lambda: None
        try:
            await self._write_message(
                {"jsonrpc": "2.0", "id": request_id, "method": str(method), "params": params}
            )
            done, _pending = await asyncio.wait(
                {response, aborted},
                timeout=self.limits.request_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if aborted in done:
                await self._cancel_request(request_id)
                raise asyncio.CancelledError
            if response not in done:
                await self._cancel_request(request_id)
                raise TimeoutError(f"language server request {method!r} timed out")
            return response.result()
        finally:
            unsubscribe()
            self._pending.pop(request_id, None)
            if not response.done():
                response.cancel()
            if not aborted.done():
                aborted.cancel()

    async def notify(self, method: str, params: object) -> None:
        await self.start()
        await self._write_message({"jsonrpc": "2.0", "method": str(method), "params": params})

    async def _cancel_request(self, request_id: int) -> None:
        try:
            await self._write_message(
                {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": request_id}}
            )
        except JsonRpcProtocolError:
            pass

    async def _write_message(self, payload: dict[str, object]) -> None:
        process = self._process
        writer = process.stdin if process is not None else None
        if self._closing or writer is None or process.returncode is not None:
            raise JsonRpcProtocolError("language server is closed")
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise JsonRpcProtocolError("language server request contains non-JSON data") from error
        if len(body) > self.limits.max_frame_bytes:
            raise JsonRpcProtocolError("language server outbound frame exceeds limit")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        async with self._write_lock:
            try:
                writer.write(frame)
                await writer.drain()
            except (BrokenPipeError, ConnectionError, RuntimeError) as error:
                raise JsonRpcProtocolError(self._closed_message()) from error

    async def _reader_loop(self) -> None:
        process = self._process
        reader = process.stdout if process is not None else None
        if reader is None:
            return
        try:
            while True:
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > _MAX_HEADER_BYTES:
                    raise JsonRpcProtocolError("language server protocol header exceeds limit")
                headers: dict[str, str] = {}
                for raw_line in header[:-4].split(b"\r\n"):
                    try:
                        name, value = raw_line.decode("ascii").split(":", 1)
                    except (UnicodeDecodeError, ValueError) as error:
                        raise JsonRpcProtocolError("language server protocol error: malformed header") from error
                    headers[name.strip().lower()] = value.strip()
                raw_length = headers.get("content-length")
                try:
                    content_length = int(raw_length or "")
                except ValueError as error:
                    raise JsonRpcProtocolError("language server protocol error: invalid Content-Length") from error
                if content_length < 0 or content_length > self.limits.max_frame_bytes:
                    raise JsonRpcProtocolError("language server frame exceeds limit")
                body = await reader.readexactly(content_length)
                try:
                    message = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise JsonRpcProtocolError("language server protocol error: invalid JSON") from error
                if not isinstance(message, dict):
                    raise JsonRpcProtocolError("language server protocol error: message must be an object")
                self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            self._fail_pending(JsonRpcProtocolError(self._closed_message()))
        except JsonRpcProtocolError as error:
            self._fail_pending(error)
        except Exception as error:  # noqa: BLE001 - shape all reader failures.
            self._fail_pending(JsonRpcProtocolError(f"language server protocol reader failed: {type(error).__name__}"))

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = message.get("id")
            pending = self._pending.get(request_id) if isinstance(request_id, int) else None
            if pending is None or pending.done():
                return
            raw_error = message.get("error")
            if isinstance(raw_error, dict):
                code = raw_error.get("code") if isinstance(raw_error.get("code"), int) else None
                text = raw_error.get("message") if isinstance(raw_error.get("message"), str) else "unknown error"
                pending.set_exception(JsonRpcRequestError(code, text[:1000]))
            else:
                pending.set_result(message.get("result"))
            return
        method = message.get("method")
        if isinstance(method, str) and self.on_notification is not None:
            try:
                self.on_notification(method, message.get("params"))
            except Exception:
                pass

    async def _stderr_loop(self) -> None:
        process = self._process
        reader = process.stderr if process is not None else None
        if reader is None:
            return
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                return
            self._stderr_chunks.append(chunk)
            self._stderr_size += len(chunk)
            while self._stderr_size > _MAX_STDERR_BYTES and self._stderr_chunks:
                removed = self._stderr_chunks.popleft()
                self._stderr_size -= len(removed)

    def _stderr_tail(self) -> str:
        raw = b"".join(self._stderr_chunks)[-_MAX_STDERR_BYTES:]
        return _redact_stderr(raw.decode("utf-8", errors="replace")).strip()

    def _closed_message(self) -> str:
        tail = self._stderr_tail()
        return f"language server closed unexpectedly; stderr: {tail}" if tail else "language server closed unexpectedly"

    def _fail_pending(self, error: BaseException) -> None:
        for pending in tuple(self._pending.values()):
            if not pending.done():
                pending.set_exception(error)

    async def close(self) -> None:
        if self._closing:
            return
        process = self._process
        if process is None:
            self._closing = True
            return
        if self.initialized and process.returncode is None:
            try:
                await self.request("shutdown", None)
            except BaseException:
                pass
            try:
                await self._write_message({"jsonrpc": "2.0", "method": "exit", "params": None})
            except JsonRpcProtocolError:
                pass
        self._closing = True
        self.initialized = False
        self._fail_pending(JsonRpcProtocolError("language server client closed"))
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            except TimeoutError:
                process.kill()
                await process.wait()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._reader_task = None
        self._stderr_task = None
        self._process = None


__all__ = [
    "JsonRpcProtocolError",
    "JsonRpcRequestError",
    "JsonRpcStdioClient",
]
