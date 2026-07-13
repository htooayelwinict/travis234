from __future__ import annotations

from tests._support_ai_travis_env_provider import *  # noqa: F403


def test_travis_env_provider_uses_runtime_option_api_key_for_authorization(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStream:
        def __enter__(self):
            raise RuntimeError("stop after capture")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)

    _openrouter_provider().stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(api_key="runtime-login-key"),
    ).result_sync()

    assert captured["headers"]["Authorization"] == "Bearer runtime-login-key"

def test_travis_env_provider_factory_allows_runtime_login_key_without_startup_transport_flag(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "TRAVIS234_WORKER_LLM_MODEL=qwen/qwen3.6-flash",
                "TRAVIS234_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeStream:
        def __enter__(self):
            raise RuntimeError("stop after capture")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)

    provider = create_travis_provider(dotenv_path=str(dotenv))
    provider.stream_simple(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(api_key="runtime-login-key"),
    ).result_sync()

    assert captured["headers"]["Authorization"] == "Bearer runtime-login-key"

def test_travis_env_provider_http_error_reports_runtime_model_after_switch(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Bad Request"}},
    )

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "for model acme/x" in message.error_message
    assert "qwen/qwen3-coder-next" not in message.error_message

def test_travis_env_provider_formats_openrouter_403_as_actionable_auth_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        json={"error": {"message": "Forbidden"}},
    )

    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                "Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
                request=request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

    message = provider.stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert "OPENROUTER_API_KEY" in message.error_message
    assert "model access" in message.error_message
    assert "For more information check" not in message.error_message

def test_travis_env_provider_formats_openrouter_prompt_injection_403(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        json={
            "error": {
                "message": "Request blocked: prompt injection patterns detected",
                "metadata": {"patterns": ["system_prefix_spoofing"]},
            }
        },
    )

    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                "Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
                request=request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

    message = provider.stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter prompt-injection guardrail blocked the request" in message.error_message
    assert "system_prefix_spoofing" in message.error_message
    assert "authorization failed" not in message.error_message

def test_travis_env_provider_formats_unread_streaming_http_error_without_thread_crash(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        stream=httpx.ByteStream(b'{"error":{"message":"Forbidden"}}'),
    )

    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                "Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
                request=request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

    message = provider.stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert "Provider message: Forbidden" in message.error_message

def test_travis_env_provider_formats_non_json_malformed_and_empty_error_bodies_safely(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    cases = [
        b"Forbidden by provider policy",
        b'{"error": {"message": "truncated"',
        b"",
    ]

    for body in cases:
        response = httpx.Response(403, request=request, content=body)

        message = _run_http_status_failure(monkeypatch, response)

        assert message.stop_reason == "error"
        assert message.error_message is not None
        assert "OpenRouter authorization failed" in message.error_message
        assert "HTTP 403" in message.error_message
        assert "acme/x" in message.error_message
        assert "qwen/qwen3-coder-next" not in message.error_message
        assert "Provider message:" in message.error_message
        assert "JSONDecodeError" not in message.error_message

def test_travis_env_provider_truncates_huge_raw_error_body(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    huge_body = ("provider guardrail details " + ("x" * 5000)).encode()
    response = httpx.Response(403, request=request, content=huge_body)

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert len(message.error_message) < 1200
    assert "x" * 500 not in message.error_message

def test_travis_env_provider_handles_unavailable_streaming_error_body_without_secondary_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")

    class FailingBodyStream(httpx.SyncByteStream):
        def __iter__(self):
            raise RuntimeError("body unavailable")

    response = httpx.Response(403, request=request, stream=FailingBodyStream())

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert "Provider message: Forbidden" in message.error_message
    assert "ResponseNotRead" not in message.error_message
    assert "body unavailable" not in message.error_message

def test_travis_env_provider_extracts_nested_metadata_raw_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    nested = {
        "error": {
            "message": "upstream provider rejected the request",
            "metadata": {"patterns": ["upstream_policy"]},
        }
    }
    response = httpx.Response(
        502,
        request=request,
        json={
            "error": {
                "message": "gateway failed",
                "metadata": {"raw": json.dumps(nested)},
            }
        },
    )

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter API error (HTTP 502 Bad Gateway)" in message.error_message
    assert "Provider message: gateway failed" in message.error_message
    assert "upstream provider rejected the request" in message.error_message
    assert "Patterns: upstream_policy" in message.error_message

def test_travis_env_provider_streaming_iteration_failure_terminates_with_one_error(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            yield _sse({"choices": [{"delta": {"content": "partial"}}]})
            raise RuntimeError("stream socket reset")

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)

    events = list(_openrouter_provider().stream(_model(), Context(messages=[UserMessage(content="hi")])))

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "error"]
    assert events[-1].error.stop_reason == "error"
    assert events[-1].error.error_message == "stream socket reset"

def test_travis_env_provider_runtime_max_tokens_overrides_env_config(monkeypatch) -> None:
    captured_body: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": "ok"},
                        }
                    ),
                    "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 0},
                        }
                    ),
                    "data: " + json.dumps({"type": "message_stop"}),
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured_body.update(kwargs["json"])
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            max_tokens=8192,
            generation_params=GenerationParams(max_tokens=8192),
        )
    )

    provider.stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(max_tokens=4096),
    ).result_sync()

    assert captured_body["max_tokens"] == 4096

def test_travis_env_provider_applies_generation_params_to_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

    class FakeStream:
        def __init__(self):
            self.events = []

        def push(self, event):
            self.events.append(event)

        def close(self):
            self.closed = True

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    config = travis_env.ModelConfig(
        enabled=True,
        api_key="test-key",
        model="acme/x",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=55,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        stop=[],
        provider_sort="latency",
        max_tokens=None,
        generation_params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=4096,
            stop=("END",),
            provider_sort="throughput",
        ),
    )

    provider = travis_env.TravisProvider(config)
    stream = FakeStream()
    provider._run(stream, _model(), Context(messages=[UserMessage(content="hi")]), None)

    body = captured["body"]
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 4096
    assert body["stop"] == ["END"]
    assert body["provider"] == {"sort": "throughput", "allow_fallbacks": True}

def test_travis_env_provider_surfaces_generation_param_warnings(monkeypatch) -> None:
    warnings = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            return FakeResponse()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="test-key",
            model="acme/x",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=55,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            generation_params=GenerationParams(parallel_tool_calls=True),
        )
    )

    provider.stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleNamespace(on_generation_warning=warnings.append),
    ).result_sync()

    assert [(warning.param, warning.action) for warning in warnings] == [
        ("parallel_tool_calls", "dropped")
    ]

def test_travis_env_provider_runtime_model_overrides_env_config_model(monkeypatch) -> None:
    captured_body: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": "ok"},
                        }
                    ),
                    "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 0},
                        }
                    ),
                    "data: " + json.dumps({"type": "message_stop"}),
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured_body.update(kwargs["json"])
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3.6-flash",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            max_tokens=8192,
        )
    )
    switched_model = Model(
        id="openai/gpt-5.5",
        name="OpenAI GPT 5.5",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    provider.stream(
        switched_model,
        Context(messages=[UserMessage(content="hi")]),
    ).result_sync()

    assert captured_body["model"] == "openai/gpt-5.5"

def test_travis_env_provider_trusts_travis_runtime_resolution_over_local_profile(monkeypatch) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from travis.ai.providers.base import ProviderProfile
    from travis.ai.providers.catalog import ResolvedProviderRuntime

    captured: dict[str, object] = {}
    runtime_profile = ProviderProfile(
        name="runtime-anthropic",
        api_mode="anthropic_messages",
        base_url="https://runtime.example/anthropic",
    )

    def fake_runtime(*args, **kwargs):
        return ResolvedProviderRuntime(
            provider="runtime-anthropic",
            requested_provider="openrouter",
            profile=runtime_profile,
            api_mode="anthropic_messages",
            transport="anthropic_messages",
            endpoint_path="/v1/messages",
            base_url="https://runtime.example/anthropic",
            api_key_env_vars=("RUNTIME_API_KEY",),
            auth_type="api_key",
            source="test-runtime",
        )

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": "ok"},
                        }
                    ),
                    "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 0},
                        }
                    ),
                    "data: " + json.dumps({"type": "message_stop"}),
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeStream()

    monkeypatch.setattr(travis_env, "resolve_provider_runtime", fake_runtime)
    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)

    stream = create_assistant_message_event_stream()
    _openrouter_provider()._run(
        stream,
        _model(),
        Context(system_prompt="sys", messages=[UserMessage(content="hi")]),
        None,
    )

    assert captured["url"] == "https://runtime.example/anthropic/v1/messages"
    assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["json"]["system"] == [{"type": "text", "text": "sys"}]
    assert "tools" not in captured["json"]
    assert stream.result_sync().stop_reason == "stop"

def test_convert_messages_maps_roles_and_tools() -> None:
    ctx = Context(
        system_prompt="sys",
        messages=[
            UserMessage(content="hello", timestamp=now_ms()),
            ToolResultMessage(
                tool_call_id="c1", tool_name="read",
                content=[TextContent(text="file body")], is_error=False, timestamp=now_ms(),
            ),
        ],
        tools=[Tool(name="read", description="read", parameters={"type": "object"})],
    )
    messages, tools = convert_messages(ctx)
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "hello"}
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "c1"
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read"

def test_travis_env_provider_invokes_runtime_payload_hook(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStream:
        status_code = 200
        headers = {"x-test": "yes"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeStream()

    class Options:
        api_key = "runtime-key"
        on_response = None
        reasoning = None

        def __init__(self):
            self.seen_payloads: list[dict] = []

        def on_payload(self, payload):
            self.seen_payloads.append(payload)
            mutated = dict(payload)
            mutated["metadata"] = {"hooked": True}
            return mutated

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = _openrouter_provider()
    options = Options()

    events = list(provider.stream(_model(), Context(messages=[UserMessage("hello", timestamp=now_ms())]), options))

    assert events[-1].type == "done"
    assert options.seen_payloads
    assert captured["json"]["metadata"] == {"hooked": True}
