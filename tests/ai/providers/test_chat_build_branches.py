"""Direct branch characterizations for Chat Completions request composition."""

from __future__ import annotations

import copy

import pytest

from travis.ai.providers.base import OMIT_TEMPERATURE, ProviderProfile
from travis.ai.providers.transports import ChatCompletionsTransport


_MISSING = object()


def test_cache_and_base_options_preserve_inputs_and_wire_shape() -> None:
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": [{"type": "text", "text": "work"}]},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object"},
            },
        }
    ]
    original_messages = copy.deepcopy(messages)
    original_tools = copy.deepcopy(tools)
    session_id = "session-" + ("x" * 80)

    body = ChatCompletionsTransport().build_kwargs(
        model="claude-compatible",
        messages=messages,
        tools=tools,
        profile=ProviderProfile(
            name="openai",
            base_url="https://api.openai.com/v1",
            fixed_temperature=0.25,
        ),
        stream=True,
        temperature=0.9,
        max_tokens=4096,
        tool_choice="required",
        session_id=session_id,
        timeout=17.5,
        cache_retention="long",
        model_compat={
            "cacheControlFormat": "anthropic",
            "maxTokensField": "max_tokens",
        },
    )

    assert messages == original_messages
    assert tools == original_tools
    assert body == {
        "model": "claude-compatible",
        "messages": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "policy",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "work",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            },
        ],
        "stream": True,
        "prompt_cache_key": session_id[:64],
        "prompt_cache_retention": "24h",
        "stream_options": {"include_usage": True},
        "store": False,
        "timeout": 17.5,
        "temperature": 0.25,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object"},
                },
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        "tool_choice": "required",
        "max_tokens": 4096,
    }


@pytest.mark.parametrize(
    (
        "fixed_temperature",
        "requested_temperature",
        "omit_max_tokens",
        "expected_temperature",
        "expected_max_tokens",
    ),
    [
        (OMIT_TEMPERATURE, 0.7, False, _MISSING, 8192),
        (0.2, 0.7, False, 0.2, 8192),
        (None, 0.7, False, 0.7, 8192),
        (None, None, True, _MISSING, _MISSING),
    ],
)
def test_temperature_and_max_token_precedence(
    fixed_temperature: object,
    requested_temperature: float | None,
    omit_max_tokens: bool,
    expected_temperature: object,
    expected_max_tokens: object,
) -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="chat-model",
        messages=[],
        tools=None,
        profile=ProviderProfile(
            name="openai",
            base_url="https://api.openai.com/v1",
            fixed_temperature=fixed_temperature,
            default_max_tokens=8192,
        ),
        stream=False,
        temperature=requested_temperature,
        max_tokens=None,
        omit_max_tokens=omit_max_tokens,
        cache_retention="none",
    )

    if expected_temperature is _MISSING:
        assert "temperature" not in body
    else:
        assert body["temperature"] == expected_temperature
    if expected_max_tokens is _MISSING:
        assert "max_completion_tokens" not in body
    else:
        assert body["max_completion_tokens"] == expected_max_tokens


def test_routing_affinity_and_extra_body_merges_preserve_precedence() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="router-model",
        messages=[{"role": "user", "content": "work"}],
        tools=[{"type": "function", "function": {"name": "read"}}],
        profile=ProviderProfile(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
        ),
        stream=False,
        temperature=0.2,
        max_tokens=2048,
        provider_preferences={"only": ["preferred"], "allow_fallbacks": False},
        session_id="session-1",
        cache_retention="none",
        model_compat={
            "supportsStore": False,
            "openRouterRouting": {"order": ["catalog"], "sort": "throughput"},
            "vercelGatewayRouting": {
                "only": ["gateway-a"],
                "order": ["gateway-b"],
                "ignored": "not-forwarded",
            },
            "zaiToolStream": True,
            "sendSessionAffinityHeaders": True,
            "sessionAffinityFormat": "openrouter",
        },
        extra_body_additions={
            "provider": {"ignore": ["added"], "sort": "latency"},
            "custom": "addition",
        },
        request_overrides={
            "temperature": 0.9,
            "extra_body": {
                "provider": {"only": ["override"]},
                "late": "request",
            },
        },
    )

    assert body["temperature"] == 0.9
    assert body["provider"] == {
        "only": ["override"],
        "allow_fallbacks": False,
        "order": ["catalog"],
        "sort": "latency",
        "ignore": ["added"],
    }
    assert body["providerOptions"] == {
        "gateway": {"only": ["gateway-a"], "order": ["gateway-b"]}
    }
    assert body["tool_stream"] is True
    assert body["extra_headers"] == {"x-session-id": "session-1"}
    assert list(body["extra_headers"]) == ["x-session-id"]
    assert body["custom"] == "addition"
    assert body["late"] == "request"


def test_openai_affinity_and_non_mapping_extra_body_override_stay_top_level() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="chat-model",
        messages=[],
        tools=None,
        profile=ProviderProfile(name="custom", base_url="https://provider.invalid/v1"),
        stream=True,
        temperature=None,
        max_tokens=None,
        session_id="session-2",
        cache_retention="none",
        model_compat={
            "supportsStore": False,
            "supportsUsageInStreaming": False,
            "sendSessionAffinityHeaders": True,
            "sessionAffinityFormat": "openai",
        },
        provider_preferences={"only": ["preferred"]},
        request_overrides={"extra_body": "opaque"},
    )

    assert body["extra_headers"] == {
        "session_id": "session-2",
        "x-client-request-id": "session-2",
        "x-session-affinity": "session-2",
    }
    assert list(body["extra_headers"]) == [
        "session_id",
        "x-client-request-id",
        "x-session-affinity",
    ]
    assert body["extra_body"] == "opaque"
    assert body["provider"] == {"only": ["preferred"]}
    assert "stream_options" not in body
    assert "store" not in body
