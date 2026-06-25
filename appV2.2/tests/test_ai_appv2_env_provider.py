from __future__ import annotations

import json

import httpx

from appv22.ai.env_config import ModelConfig
import appv22.ai.providers.appv2_env as appv2_env
from appv22.ai.providers.appv2_env import (
    AppV2EnvProvider,
    NullProvider,
    convert_messages,
    parse_sse_chunks,
)
from appv22.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)


def _model() -> Model:
    return Model(id="acme/x", name="X", api="openai-completions", provider="openrouter", base_url="")


def _openrouter_provider() -> AppV2EnvProvider:
    return AppV2EnvProvider(
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


def _run_http_status_failure(monkeypatch, response: httpx.Response) -> AssistantMessage:
    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                f"Client error '{response.status_code} {response.reason_phrase}'",
                request=response.request,
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

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    return _openrouter_provider().stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()


def test_appv2_env_provider_formats_openrouter_403_as_actionable_auth_error(monkeypatch) -> None:
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

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
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


def test_appv2_env_provider_formats_openrouter_prompt_injection_403(monkeypatch) -> None:
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

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
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


def test_appv2_env_provider_formats_unread_streaming_http_error_without_thread_crash(monkeypatch) -> None:
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

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
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


def test_appv2_env_provider_formats_non_json_malformed_and_empty_error_bodies_safely(monkeypatch) -> None:
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
        assert "qwen/qwen3-coder-next" in message.error_message
        assert "Provider message:" in message.error_message
        assert "JSONDecodeError" not in message.error_message


def test_appv2_env_provider_truncates_huge_raw_error_body(monkeypatch) -> None:
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


def test_appv2_env_provider_handles_unavailable_streaming_error_body_without_secondary_error(monkeypatch) -> None:
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


def test_appv2_env_provider_extracts_nested_metadata_raw_error(monkeypatch) -> None:
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


def test_appv2_env_provider_streaming_iteration_failure_terminates_with_one_error(monkeypatch) -> None:
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

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)

    events = list(_openrouter_provider().stream(_model(), Context(messages=[UserMessage(content="hi")])))

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "error"]
    assert events[-1].error.stop_reason == "error"
    assert events[-1].error.error_message == "stream socket reset"


def test_appv2_env_provider_runtime_max_tokens_overrides_env_config(monkeypatch) -> None:
    captured_body: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(["data: [DONE]"])

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

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
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
        )
    )

    provider.stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(max_tokens=4096),
    ).result_sync()

    assert captured_body["max_tokens"] == 4096


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


def test_convert_messages_sanitizes_unpaired_surrogates_for_provider_payload() -> None:
    emoji = chr(0x1F648)
    high_surrogate = chr(0xD83D)
    low_surrogate = chr(0xDE48)
    ctx = Context(
        system_prompt=f"sys {high_surrogate}{emoji}",
        messages=[
            UserMessage(content=f"hello {high_surrogate}{emoji}{low_surrogate}", timestamp=now_ms()),
            UserMessage(content=[TextContent(text=f"part {low_surrogate}{emoji}")], timestamp=now_ms()),
            AssistantMessage(
                content=[
                    ThinkingContent(thinking=f"think {high_surrogate}{emoji}", thinking_signature="reasoning_content"),
                    TextContent(text=f"answer {emoji}{low_surrogate}"),
                ],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[TextContent(text=f"tool {high_surrogate}{emoji}")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ],
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[0]["content"] == f"sys {emoji}"
    assert messages[1]["content"] == f"hello {emoji}"
    assert messages[2]["content"][0]["text"] == f"part {emoji}"
    assert messages[3]["content"] == f"answer {emoji}"
    assert messages[3]["reasoning_content"] == f"think {emoji}"
    assert messages[4]["content"] == f"tool {emoji}"
    json.dumps(messages, ensure_ascii=False).encode("utf-8")


def test_convert_messages_preserves_user_image_content_parts() -> None:
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                ],
                timestamp=now_ms(),
            )
        ]
    )

    messages, _tools = convert_messages(ctx)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}},
            ],
        }
    ]


def test_convert_messages_preserves_assistant_thinking_signature() -> None:
    model = Model(id="gpt-oss", name="GPT OSS", api="openai-completions", provider="opencode-go", base_url="")
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(thinking="first", thinking_signature="reasoning"),
                    ThinkingContent(thinking="second", thinking_signature="reasoning"),
                    TextContent(text="Visible"),
                ],
                api="openai-completions",
                provider="opencode-go",
                model="gpt-oss",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {
            "role": "assistant",
            "content": "Visible",
            "reasoning_content": "first\nsecond",
        }
    ]


def test_convert_messages_bridges_tool_result_images_for_image_models() -> None:
    model = Model(
        id="vision",
        name="Vision",
        api="openai-completions",
        provider="openrouter",
        base_url="",
        input=["text", "image"],
    )
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    TextContent(text="first text"),
                    ImageContent(data="Zmlyc3Q=", mime_type="image/png"),
                ],
                is_error=False,
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c2",
                tool_name="read",
                content=[ImageContent(data="c2Vjb25k", mime_type="image/jpeg")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "first text"},
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": "(see attached image)"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,Zmlyc3Q="}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,c2Vjb25k"}},
            ],
        },
    ]


def test_convert_messages_normalizes_cross_model_tool_call_ids_and_matching_results() -> None:
    raw_id = "call.bad+id-" + ("x" * 50) + "|openai-response-item"
    expected_id = ("call_bad_id-" + ("x" * 50))[:40]
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    TextContent(text=""),
                    ToolCall(id=raw_id, name="read", arguments={"path": "README.md"}),
                ],
                api="openai-responses",
                provider="openai",
                model="gpt-4.1",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id=raw_id,
                tool_name="read",
                content=[TextContent(text="contents")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[0]["tool_calls"][0]["id"] == expected_id
    assert messages[1]["tool_call_id"] == expected_id


def test_convert_messages_inserts_pi_synthetic_result_for_orphaned_tool_call() -> None:
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[ToolCall(id="call_missing", name="read", arguments={"path": "README.md"})],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            UserMessage(content=[TextContent(text="continue")], timestamp=now_ms()),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call_missing",
        "name": "read",
        "content": "No result provided",
    }
    assert messages[2]["role"] == "user"


def test_convert_messages_skips_error_and_aborted_assistant_replay() -> None:
    ctx = Context(
        messages=[
            UserMessage(content=[TextContent(text="before")], timestamp=now_ms()),
            AssistantMessage(
                content=[TextContent(text="failed partial")],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="error",
                error_message="provider failed",
                timestamp=now_ms(),
            ),
            AssistantMessage(
                content=[TextContent(text="aborted partial")],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="aborted",
                timestamp=now_ms(),
            ),
            UserMessage(content=[TextContent(text="after")], timestamp=now_ms()),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert [message["role"] for message in messages] == ["user", "user"]
    assert messages[0]["content"][0]["text"] == "before"
    assert messages[1]["content"][0]["text"] == "after"


def test_convert_messages_downgrades_images_for_non_vision_model() -> None:
    model = Model(id="text-only", name="Text", api="openai-completions", provider="openrouter", base_url="")
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                    ImageContent(data="aW1nMg==", mime_type="image/png"),
                ],
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    ImageContent(data="dG9vbA==", mime_type="image/png"),
                    TextContent(text="tool text"),
                ],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "text", "text": "(image omitted: model does not support images)"},
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "read",
            "content": "(tool image omitted: model does not support images)tool text",
        },
    ]


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def test_parse_sse_text_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    types = [e.type for e in events]
    assert types[0] == "start"
    assert "text_delta" in types
    assert types[-1] == "done"
    final = events[-1].message
    assert final.content[0].text == "Hello"
    assert final.stop_reason == "stop"


def test_parse_sse_finalizes_on_terminal_finish_reason_without_waiting_for_eof() -> None:
    def lines_after_finish_never_arrive():
        yield _sse({"choices": [{"delta": {"content": "Done"}}]})
        yield _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        raise AssertionError("parser requested another SSE line after terminal finish_reason")

    events = list(parse_sse_chunks(lines_after_finish_never_arrive(), _model()))

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "text_end", "done"]
    final = events[-1].message
    assert final.content[0].text == "Done"
    assert final.stop_reason == "stop"


def test_parse_sse_errors_after_non_data_keepalive_idle_timeout() -> None:
    fake_time = {"now": 100.0}

    def clock() -> float:
        return fake_time["now"]

    def keepalive_after_content():
        yield _sse({"choices": [{"delta": {"content": "Done"}}]})
        fake_time["now"] += 61.0
        yield ": keepalive"
        raise AssertionError("parser kept reading after meaningful SSE data timeout")

    events = list(
        parse_sse_chunks(
            keepalive_after_content(),
            _model(),
            data_idle_timeout_seconds=60.0,
            clock=clock,
        )
    )

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "text_end", "error"]
    final = events[-1].error
    assert final.content[0].text == "Done"
    assert final.stop_reason == "error"
    assert final.error_message == "SSE stream received no data events for 60 seconds"


def test_parse_sse_openai_compatible_reasoning_fields() -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "plan", "reasoning": "duplicate"}}]}),
        _sse({"choices": [{"delta": {"reasoning_text": " next"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    assert [event.delta for event in events if event.type == "thinking_delta"] == ["plan", " next"]
    final = events[-1].message
    assert final.content[0].type == "thinking"
    assert final.content[0].thinking == "plan next"
    assert final.content[0].thinking_signature == "reasoning_content"


def test_parse_sse_captures_response_metadata_and_choice_usage() -> None:
    lines = [
        _sse(
            {
                "id": "chatcmpl-abc",
                "model": "provider/resolved-model",
                "choices": [
                    {
                        "delta": {"content": "Hi"},
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                    }
                ],
            }
        ),
        _sse({"id": "chatcmpl-abc", "model": "provider/resolved-model", "choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    final = events[-1].message
    assert final.response_id == "chatcmpl-abc"
    assert final.response_model == "provider/resolved-model"
    assert final.usage.input == 7
    assert final.usage.output == 3
    assert final.usage.total_tokens == 10


def test_parse_sse_zero_usage_does_not_overwrite_nonzero_usage() -> None:
    lines = [
        _sse(
            {
                "choices": [{"delta": {"content": "Hi"}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
            }
        ),
        _sse(
            {
                "choices": [{"delta": {"content": " there"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "stop",
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    final = events[-1].message
    assert final.content[0].text == "Hi there"
    assert final.usage.input == 9
    assert final.usage.output == 2
    assert final.usage.total_tokens == 11


def test_parse_sse_skips_malformed_payload_and_continues_to_finish_reason() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "before "}}]}),
        'data: {"choices": [',
        _sse({"choices": [{"delta": {"content": "after"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "done"
    final = events[-1].message
    assert final.content[0].text == "before after"
    assert final.stop_reason == "stop"
    assert final.error_message is None


def test_parse_sse_missing_finish_reason_returns_error_event() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "partial"}}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "error"
    assert events[-1].reason == "error"
    assert events[-1].error.stop_reason == "error"
    assert "finish_reason" in events[-1].error.error_message


def test_parse_sse_maps_pi_finish_reasons() -> None:
    normal_cases = [
        ("end", "done", "stop"),
        ("function_call", "done", "toolUse"),
        ("network_error", "error", "error"),
        ("content_filter", "error", "error"),
        ("weird_provider_reason", "error", "error"),
    ]

    for finish_reason, event_type, stop_reason in normal_cases:
        events = list(
            parse_sse_chunks(
                [
                    _sse({"choices": [{"delta": {"content": "x"}}]}),
                    _sse({"choices": [{"delta": {}, "finish_reason": finish_reason}]}),
                    "data: [DONE]",
                ],
                _model(),
            )
        )
        assert events[-1].type == event_type
        final = events[-1].message if event_type == "done" else events[-1].error
        assert final.stop_reason == stop_reason
        if event_type == "error":
            assert final.error_message == f"Provider finish_reason: {finish_reason}"


def test_parse_sse_tool_call_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": " \"a.txt\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    assert events[-1].type == "done"
    assert events[-1].reason == "toolUse"
    tool_call = events[-1].message.content[0]
    assert tool_call.type == "toolCall"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "a.txt"}


def test_parse_sse_preserves_multiple_indexed_tool_calls() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_read", "function": {"name": "read", "arguments": ""}},
                                {"index": 1, "id": "call_bash", "function": {"name": "bash", "arguments": ""}},
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": "{\"path\":\"a"}},
                                {"index": 1, "function": {"arguments": "{\"command\":\"echo"}},
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": ".txt\"}"}},
                                {"index": 1, "function": {"arguments": " hi\"}"}},
                            ]
                        }
                    }
                ]
            }
        ),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    assert [e.content_index for e in events if e.type == "toolcall_start"] == [0, 1]
    assert [e.content_index for e in events if e.type == "toolcall_end"] == [0, 1]
    tool_calls = [block for block in events[-1].message.content if block.type == "toolCall"]
    assert [(call.id, call.name, call.arguments) for call in tool_calls] == [
        ("call_read", "read", {"path": "a.txt"}),
        ("call_bash", "bash", {"command": "echo hi"}),
    ]


def test_parse_sse_updates_partial_tool_arguments_during_streaming() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_read", "function": {"name": "read", "arguments": ""}}
                            ]
                        }
                    }
                ]
            }
        ),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":\"src/ma"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "in.py\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]

    saw_partial_arguments = False
    final = None
    for event in parse_sse_chunks(lines, _model()):
        if event.type == "toolcall_delta" and event.delta == "{\"path\":\"src/ma":
            saw_partial_arguments = True
            assert event.partial.content[0].arguments == {"path": "src/ma"}
        if event.type == "done":
            final = event.message

    assert saw_partial_arguments
    assert final is not None
    assert final.content[0].arguments == {"path": "src/main.py"}


def test_null_provider_emits_error_event() -> None:
    s = NullProvider().stream(_model(), Context(messages=[]))
    events = list(s)
    assert events[-1].type == "error"
    msg = s.result_sync()
    assert isinstance(msg, AssistantMessage)
    assert msg.stop_reason == "error"
