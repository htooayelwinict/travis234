from __future__ import annotations

import base64
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from travis.agent.types import AbortSignal
from travis.ai.builtin_models import load_builtin_models
from travis.ai.env_config import ModelConfig
from travis.ai.providers import codex_runtime as codex_runtime_module
from travis.ai.providers.travis_env import TravisProvider
from travis.ai.types import AssistantMessage, Context, UserMessage


def _codex_token() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
        ).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def _codex_model_and_provider(token: str):
    model = next(
        item
        for item in load_builtin_models()
        if item.provider == "openai-codex" and item.id == "gpt-5.4"
    )
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key=token,
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )
    return model, provider


def test_codex_retry_delay_aborts_before_the_next_http_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _codex_token()
    first_attempt = threading.Event()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        first_attempt.set()
        return httpx.Response(
            429,
            json={"error": {"message": "temporarily rate limited"}},
            headers={"retry-after": "10"},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        codex_runtime_module.httpx,
        "Client",
        lambda timeout: real_client(
            timeout=timeout,
            transport=httpx.MockTransport(handler),
        ),
    )
    model, provider = _codex_model_and_provider(token)
    retry_wait_started = threading.Event()

    class TrackingAbortSignal(AbortSignal):
        def __init__(self) -> None:
            super().__init__()
            self.registrations = 0
            self.active_callbacks = 0

        def add_callback(self, callback):
            self.registrations += 1
            self.active_callbacks += 1
            unsubscribe = super().add_callback(callback)
            if self.registrations == 2:
                retry_wait_started.set()

            def tracked_unsubscribe() -> None:
                unsubscribe()
                self.active_callbacks -= 1

            return tracked_unsubscribe

    signal = TrackingAbortSignal()
    result_holder: list[AssistantMessage] = []

    def run_request() -> None:
        result_holder.append(
            provider.stream(
                model,
                Context(messages=[UserMessage(content="hello")]),
                SimpleNamespace(
                    api_key=token,
                    max_retries=1,
                    max_retry_delay_ms=10_000,
                    transport="sse",
                    signal=signal,
                ),
            ).result_sync()
        )

    thread = threading.Thread(target=run_request)
    started_at = time.monotonic()
    thread.start()
    assert first_attempt.wait(timeout=2)
    assert retry_wait_started.wait(timeout=2)
    signal.abort()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert time.monotonic() - started_at < 1.5
    assert calls == 1
    assert len(result_holder) == 1
    assert result_holder[0].stop_reason == "error"
    assert result_holder[0].error_message == "Request was aborted"
    assert signal.registrations == 2
    assert signal.active_callbacks == 0


class _OpenWebSocketState:
    name = "OPEN"


class _FakeCodexConnection:
    state = _OpenWebSocketState()

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def recv(self, timeout=None):
        del timeout
        return json.dumps(self.responses.pop(0))

    def close(self, code=1000, reason="done") -> None:
        del code, reason
        self.closed = True


def _completed_codex_response(response_id: str) -> dict[str, object]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "status": "completed",
            "output": [],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 0,
                "total_tokens": 1,
            },
        },
    }


_MISSING_CODEX_CONTINUATION = {
    "type": "error",
    "error": {
        "code": "previous_response_not_found",
        "message": "Previous response was not found",
    },
}


def test_codex_missing_continuation_retries_once_with_full_input(monkeypatch) -> None:
    token = _codex_token()
    first_connection = _FakeCodexConnection(
        [_completed_codex_response("response-1"), _MISSING_CODEX_CONTINUATION]
    )
    recovery_connection = _FakeCodexConnection(
        [_completed_codex_response("response-2")]
    )
    connections = iter([first_connection, recovery_connection])
    handshakes = 0

    def connect(url, headers, timeout):
        nonlocal handshakes
        del url, headers, timeout
        handshakes += 1
        return next(connections)

    codex_runtime_module.close_codex_websocket_sessions()
    monkeypatch.setattr(codex_runtime_module, "_connect_websocket", connect)
    model, provider = _codex_model_and_provider(token)
    options = SimpleNamespace(
        api_key=token,
        transport="auto",
        session_id="missing-continuation-session",
        websocket_connect_timeout_ms=250,
    )

    first = provider.stream(
        model,
        Context(messages=[UserMessage(content="first")]),
        options,
    ).result_sync()
    second = provider.stream(
        model,
        Context(
            messages=[
                UserMessage(content="first"),
                first,
                UserMessage(content="second"),
            ]
        ),
        options,
    ).result_sync()

    assert second.stop_reason == "stop"
    assert second.response_id == "response-2"
    assert handshakes == 2
    assert first_connection.sent[1]["previous_response_id"] == "response-1"
    assert "previous_response_id" not in recovery_connection.sent[0]
    assert len(recovery_connection.sent[0]["input"]) > len(
        first_connection.sent[1]["input"]
    )
    codex_runtime_module.close_codex_websocket_sessions()


def test_codex_repeated_missing_continuation_stops_after_one_retry(monkeypatch) -> None:
    token = _codex_token()
    first_connection = _FakeCodexConnection(
        [_completed_codex_response("response-1"), _MISSING_CODEX_CONTINUATION]
    )
    recovery_connection = _FakeCodexConnection([_MISSING_CODEX_CONTINUATION])
    connections = iter([first_connection, recovery_connection])
    handshakes = 0

    def connect(url, headers, timeout):
        nonlocal handshakes
        del url, headers, timeout
        handshakes += 1
        return next(connections)

    codex_runtime_module.close_codex_websocket_sessions()
    monkeypatch.setattr(codex_runtime_module, "_connect_websocket", connect)
    model, provider = _codex_model_and_provider(token)
    options = SimpleNamespace(
        api_key=token,
        transport="auto",
        session_id="repeated-missing-continuation-session",
        websocket_connect_timeout_ms=250,
    )

    first = provider.stream(
        model,
        Context(messages=[UserMessage(content="first")]),
        options,
    ).result_sync()
    result = provider.stream(
        model,
        Context(
            messages=[
                UserMessage(content="first"),
                first,
                UserMessage(content="second"),
            ]
        ),
        options,
    ).result_sync()

    assert result.stop_reason == "error"
    assert "Previous response was not found" in (result.error_message or "")
    assert handshakes == 2
    codex_runtime_module.close_codex_websocket_sessions()
