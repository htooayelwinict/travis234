"""Codex Responses transport runtime with WebSocket reuse and SSE fallback."""

from __future__ import annotations

import atexit
import copy
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from travis.ai.providers._shared import signal_aborted
from travis.ai.providers.provider_request import PreparedProviderRequest
from travis.ai.providers.responses_translation import convert_responses_messages
from travis.ai.types import AssistantMessage, Context, Model


_BASE_RETRY_DELAY_SECONDS = 1.0
_DEFAULT_MAX_RETRY_DELAY_SECONDS = 60.0
_DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 15.0
_WEBSOCKET_CACHE_TTL_SECONDS = 5 * 60.0
_WEBSOCKET_MAX_AGE_SECONDS = 55 * 60.0
_WEBSOCKET_BETA = "responses_websockets=2026-02-06"
_CONNECTION_LIMIT_CODE = "websocket_connection_limit_reached"
_ALLOWED_TOOL_CALL_PROVIDERS = {"openai", "openai-codex", "opencode"}


class CodexAPIError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None, payload: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload


class CodexProtocolError(RuntimeError):
    def __init__(self, message: str, *, payload: object = None) -> None:
        super().__init__(message)
        self.payload = payload


@dataclass
class _Continuation:
    last_request_body: dict[str, Any]
    last_response_id: str
    last_response_items: list[dict[str, Any]]


@dataclass
class _ConnectionEntry:
    connection: Any
    busy: bool
    created_at: float
    last_used_at: float
    continuation: _Continuation | None = None


_cache_lock = threading.RLock()
_session_connections: dict[str, _ConnectionEntry] = {}
_sse_fallback_sessions: set[str] = set()


def _is_terminal_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "gousagelimiterror",
            "freeusagelimiterror",
            "monthly usage limit reached",
            "available balance",
            "insufficient_quota",
            "out of budget",
            "quota exceeded",
            "billing",
        )
    )


def _is_retryable(status: int, text: str) -> bool:
    if status == 429 and _is_terminal_rate_limit_error(text):
        return False
    if status in {429, 500, 502, 503, 504}:
        return True
    lowered = text.lower().replace("_", " ").replace("-", " ")
    return any(
        marker in lowered
        for marker in (
            "rate limit",
            "ratelimit",
            "overloaded",
            "service unavailable",
            "upstream connect",
            "connection refused",
        )
    )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    retry_after_ms = headers.get("retry-after-ms")
    if retry_after_ms is not None:
        try:
            return max(0.0, float(retry_after_ms) / 1000.0)
        except ValueError:
            pass
    retry_after = headers.get("retry-after")
    if not retry_after:
        return None
    try:
        return max(0.0, float(retry_after))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(retry_after)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay(attempt: int, status: int | None, headers: Mapping[str, str], options: object) -> float:
    requested = _retry_after_seconds(headers)
    delay = requested if requested is not None else _BASE_RETRY_DELAY_SECONDS * (2**attempt)
    if status == 429 and requested is not None:
        max_delay_ms = getattr(options, "max_retry_delay_ms", None)
        max_delay = (
            float(max_delay_ms) / 1000.0
            if isinstance(max_delay_ms, (int, float)) and max_delay_ms > 0
            else _DEFAULT_MAX_RETRY_DELAY_SECONDS
        )
        delay = min(delay, max_delay)
    return max(0.0, delay)


def _friendly_http_error(response: httpx.Response, text: str) -> RuntimeError:
    message = text or response.reason_phrase or "Request failed"
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or error.get("type") or "")
        if response.status_code == 429 or any(
            marker in code.lower()
            for marker in ("usage_limit_reached", "usage_not_included", "rate_limit_exceeded")
        ):
            plan_type = error.get("plan_type")
            plan = f" ({str(plan_type).lower()} plan)" if plan_type else ""
            resets_at = error.get("resets_at")
            when = ""
            if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
                minutes = max(0, round((float(resets_at) - time.time()) / 60.0))
                when = f" Try again in ~{minutes} min."
            return RuntimeError(f"You have hit your ChatGPT usage limit{plan}.{when}".strip())
        if isinstance(error.get("message"), str):
            message = error["message"]
    return RuntimeError(message)


def _compress_body(body_json: bytes) -> tuple[bytes, bool]:
    try:
        import zstandard

        return zstandard.ZstdCompressor(level=3).compress(body_json), True
    except Exception:
        return body_json, False


def _invoke_on_response(options: object, response: httpx.Response, model: Model) -> None:
    callback = getattr(options, "on_response", None)
    if callable(callback):
        from travis.ai.providers._shared import settle_callback

        settle_callback(callback({"status": response.status_code, "headers": dict(response.headers)}, model))


def _push_decoded(stream: object, request: PreparedProviderRequest, lines: Iterable[str]) -> AssistantMessage | None:
    final_message: AssistantMessage | None = None
    for event in request.decoder(lines):
        stream.push(event)
        if event.type == "done":
            final_message = event.message
        elif event.type == "error":
            final_message = event.error
    return final_message


def _run_sse(
    stream: object,
    model: Model,
    options: object,
    request: PreparedProviderRequest,
    *,
    client_factory: Any = None,
) -> AssistantMessage | None:
    body_json = json.dumps(request.body, separators=(",", ":"), ensure_ascii=False).encode()
    payload, compressed = _compress_body(body_json)
    headers = dict(request.headers)
    if compressed:
        headers["content-encoding"] = "zstd"
    max_retries_raw = getattr(options, "max_retries", None)
    max_retries = int(max_retries_raw) if isinstance(max_retries_raw, int) else 0
    max_retries = max(0, max_retries)
    signal = getattr(options, "signal", None)

    factory = client_factory or httpx.Client
    with factory(timeout=request.timeout_seconds) as client:
        for attempt in range(max_retries + 1):
            if signal_aborted(signal):
                raise RuntimeError("Request was aborted")
            accepted_response = False
            try:
                with client.stream("POST", request.url, content=payload, headers=headers) as response:
                    unsubscribe = (
                        signal.add_callback(response.close)
                        if signal is not None and hasattr(signal, "add_callback")
                        else lambda: None
                    )
                    try:
                        _invoke_on_response(options, response, model)
                        if response.is_success:
                            accepted_response = True
                            return _push_decoded(stream, request, response.iter_lines())
                        response.read()
                        text = response.text
                        if attempt < max_retries and _is_retryable(response.status_code, text):
                            delay = _retry_delay(attempt, response.status_code, response.headers, options)
                        else:
                            raise CodexAPIError(str(_friendly_http_error(response, text)))
                    finally:
                        unsubscribe()
            except (CodexAPIError, CodexProtocolError):
                raise
            except RuntimeError as error:
                if accepted_response:
                    raise
                if str(error).startswith("You have hit your ChatGPT usage limit"):
                    raise
                if attempt >= max_retries:
                    raise
                delay = _BASE_RETRY_DELAY_SECONDS * (2**attempt)
            except Exception:
                if accepted_response:
                    raise
                if attempt >= max_retries:
                    raise
                delay = _BASE_RETRY_DELAY_SECONDS * (2**attempt)
            if signal_aborted(signal):
                raise RuntimeError("Request was aborted")
            time.sleep(delay)
    raise RuntimeError("Failed after retries")


def _websocket_url(http_url: str) -> str:
    parsed = urlsplit(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws" if parsed.scheme == "http" else parsed.scheme
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _websocket_headers(request: PreparedProviderRequest, request_id: str) -> dict[str, str]:
    headers = {
        str(key): str(value)
        for key, value in request.headers.items()
        if key.lower() not in {"accept", "content-type", "openai-beta"}
    }
    headers["OpenAI-Beta"] = _WEBSOCKET_BETA
    headers["x-client-request-id"] = request_id
    headers["session-id"] = request_id
    return headers


def _connection_open(connection: object) -> bool:
    state = getattr(connection, "state", None)
    if state is None:
        return True
    name = getattr(state, "name", str(state)).upper()
    return name == "OPEN" or name.endswith(".OPEN") or str(state) == "1"


def _close_silently(connection: object, code: int = 1000, reason: str = "done") -> None:
    try:
        connection.close(code=code, reason=reason)
    except TypeError:
        try:
            connection.close(code, reason)
        except Exception:
            pass
    except Exception:
        pass


def _purge_expired_locked(now: float) -> None:
    for session_id, entry in list(_session_connections.items()):
        idle = now - entry.last_used_at >= _WEBSOCKET_CACHE_TTL_SECONDS
        old = now - entry.created_at >= _WEBSOCKET_MAX_AGE_SECONDS
        if entry.busy or (not idle and not old and _connection_open(entry.connection)):
            continue
        _close_silently(entry.connection, reason="idle_timeout" if idle else "connection_age_limit")
        _session_connections.pop(session_id, None)


def _connect_websocket(url: str, headers: Mapping[str, str], timeout: float) -> object:
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers=dict(headers),
        user_agent_header=None,
        open_timeout=timeout,
        max_size=None,
    )


def _acquire_connection(
    request: PreparedProviderRequest,
    session_id: str | None,
    options: object,
) -> tuple[object, _ConnectionEntry | None, bool]:
    now = time.monotonic()
    with _cache_lock:
        _purge_expired_locked(now)
        entry = _session_connections.get(session_id) if session_id else None
        if entry is not None and not entry.busy and _connection_open(entry.connection):
            entry.busy = True
            return entry.connection, entry, True
    timeout_ms = getattr(options, "websocket_connect_timeout_ms", None)
    timeout = (
        float(timeout_ms) / 1000.0
        if isinstance(timeout_ms, (int, float)) and timeout_ms >= 0
        else _DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_SECONDS
    )
    request_id = session_id or str(uuid.uuid4())
    connection = _connect_websocket(
        _websocket_url(request.url),
        _websocket_headers(request, request_id),
        timeout,
    )
    if not session_id:
        return connection, None, False
    with _cache_lock:
        existing = _session_connections.get(session_id)
        if existing is None or (not existing.busy and not _connection_open(existing.connection)):
            entry = _ConnectionEntry(connection, True, now, now)
            _session_connections[session_id] = entry
            return connection, entry, False
    return connection, None, False


def _release_connection(
    session_id: str | None,
    connection: object,
    entry: _ConnectionEntry | None,
    *,
    keep: bool,
) -> None:
    if entry is None:
        _close_silently(connection)
        return
    with _cache_lock:
        if not keep or not _connection_open(connection):
            _close_silently(connection)
            if session_id and _session_connections.get(session_id) is entry:
                _session_connections.pop(session_id, None)
            return
        entry.busy = False
        entry.last_used_at = time.monotonic()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_without_input(body: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in body.items() if key not in {"input", "previous_response_id"}}


def _cached_request_body(entry: _ConnectionEntry, body: dict[str, Any]) -> dict[str, Any]:
    continuation = entry.continuation
    if continuation is None:
        return body
    if _canonical(_request_without_input(body)) != _canonical(
        _request_without_input(continuation.last_request_body)
    ):
        entry.continuation = None
        return body
    current_input = body.get("input")
    previous_input = continuation.last_request_body.get("input")
    if not isinstance(current_input, list) or not isinstance(previous_input, list):
        entry.continuation = None
        return body
    baseline = [*previous_input, *continuation.last_response_items]
    if len(current_input) < len(baseline) or _canonical(current_input[: len(baseline)]) != _canonical(baseline):
        entry.continuation = None
        return body
    return {
        **body,
        "previous_response_id": continuation.last_response_id,
        "input": current_input[len(baseline) :],
    }


def _event_error(event: Mapping[str, object]) -> tuple[str | None, str | None]:
    nested = event.get("error") if isinstance(event.get("error"), Mapping) else {}
    code = event.get("code") if isinstance(event.get("code"), str) else nested.get("code")
    message = event.get("message") if isinstance(event.get("message"), str) else nested.get("message")
    return (
        str(code) if isinstance(code, str) else None,
        str(message) if isinstance(message, str) else None,
    )


def _websocket_lines(connection: object, timeout: float | None, on_start: Any) -> Iterable[str]:
    saw_completion = False
    while True:
        try:
            raw = connection.recv(timeout=timeout)
        except TimeoutError as error:
            raise RuntimeError(
                f"WebSocket idle timeout after {round((timeout or 0) * 1000)}ms"
            ) from error
        except Exception as error:
            if saw_completion:
                return
            raise RuntimeError(str(error) or "WebSocket closed") from error
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            event = json.loads(str(raw))
        except (json.JSONDecodeError, UnicodeError) as error:
            raise CodexProtocolError(f"Invalid Codex WebSocket JSON: {error}", payload=raw) from error
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "error":
            code, message = _event_error(event)
            raise CodexAPIError(
                f"Codex error: {message or code or _canonical(event)}",
                code=code,
                payload=event,
            )
        if event_type == "response.failed":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            error_value = response.get("error") if isinstance(response.get("error"), dict) else {}
            raise CodexAPIError(
                str(error_value.get("message") or "Codex response failed"),
                code=str(error_value.get("code")) if error_value.get("code") else None,
                payload=event,
            )
        if event_type in {"response.done", "response.incomplete"}:
            event = {**event, "type": "response.completed"}
            event_type = "response.completed"
        on_start()
        yield f"data: {_canonical(event)}"
        if event_type == "response.completed":
            saw_completion = True
            return


def _run_websocket(
    stream: object,
    model: Model,
    options: object,
    request: PreparedProviderRequest,
    state: dict[str, bool],
) -> tuple[AssistantMessage | None, bool]:
    session_id = getattr(options, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    connection, entry, _reused = _acquire_connection(request, session_id, options)
    keep = True
    started = False
    full_body = copy.deepcopy(dict(request.body))
    use_cached = getattr(options, "transport", None) in {None, "auto", "websocket-cached"}
    body = _cached_request_body(entry, full_body) if use_cached and entry is not None else full_body
    timeout = request.timeout_seconds
    final_message: AssistantMessage | None = None

    def mark_started() -> None:
        nonlocal started
        started = True
        state["started"] = True

    try:
        connection.send(_canonical({"type": "response.create", **body}))
        final_message = _push_decoded(
            stream,
            request,
            _websocket_lines(connection, timeout, mark_started),
        )
        if signal_aborted(getattr(options, "signal", None)):
            keep = False
            raise RuntimeError("Request was aborted")
        if use_cached and entry is not None and final_message is not None and final_message.response_id:
            response_items = [
                item
                for item in convert_responses_messages(
                    model,
                    Context(messages=[final_message]),
                    _ALLOWED_TOOL_CALL_PROVIDERS,
                    include_system_prompt=False,
                )
                if item.get("type") != "function_call_output"
            ]
            entry.continuation = _Continuation(
                last_request_body=full_body,
                last_response_id=final_message.response_id,
                last_response_items=response_items,
            )
        return final_message, started
    except Exception:
        keep = False
        if entry is not None:
            entry.continuation = None
        raise
    finally:
        _release_connection(session_id, connection, entry, keep=keep)


def run_codex_request(
    stream: object,
    model: Model,
    options: object,
    request: PreparedProviderRequest,
) -> AssistantMessage | None:
    transport = getattr(options, "transport", None) or "auto"
    session_id = getattr(options, "session_id", None)
    session_id = session_id if isinstance(session_id, str) and session_id else None
    websocket_disabled = transport != "sse" and session_id in _sse_fallback_sessions
    if transport != "sse" and not websocket_disabled:
        retried_connection_limit = False
        while True:
            state = {"started": False}
            try:
                return _run_websocket(stream, model, options, request, state)[0]
            except CodexAPIError as error:
                if not state["started"] and error.code == _CONNECTION_LIMIT_CODE and not retried_connection_limit:
                    retried_connection_limit = True
                    continue
                raise
            except CodexProtocolError:
                raise
            except Exception:
                if state["started"]:
                    raise
                if session_id:
                    _sse_fallback_sessions.add(session_id)
                break
    return _run_sse(stream, model, options, request)


def close_codex_websocket_sessions(session_id: str | None = None) -> None:
    with _cache_lock:
        if session_id is not None:
            entry = _session_connections.pop(session_id, None)
            _sse_fallback_sessions.discard(session_id)
            if entry is not None:
                _close_silently(entry.connection, reason="session_close")
            return
        entries = list(_session_connections.values())
        _session_connections.clear()
        _sse_fallback_sessions.clear()
    for entry in entries:
        _close_silently(entry.connection, reason="shutdown")


atexit.register(close_codex_websocket_sessions)


__all__ = [
    "CodexAPIError",
    "CodexProtocolError",
    "close_codex_websocket_sessions",
    "run_codex_request",
]
