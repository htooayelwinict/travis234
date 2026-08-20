"""Direct branch characterizations for Anthropic request composition."""

from __future__ import annotations

from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.transport_families.anthropic import AnthropicMessagesTransport
from travis.ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)


def _model() -> Model:
    return Model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text"],
        max_tokens=128_000,
    )


def test_fallback_request_preserves_system_tools_affinity_and_optional_fields() -> None:
    body = AnthropicMessagesTransport().build_kwargs(
        model="claude-compatible",
        messages=[
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "work"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object"},
                },
            }
        ],
        profile=ProviderProfile(name="custom-anthropic"),
        stream=True,
        temperature=0.2,
        max_tokens=4096,
        cache_retention="none",
        session_id="session-1",
        tool_choice="required",
        metadata={"user_id": "employee-1", "ignored": "value"},
        model_compat={"sendSessionAffinityHeaders": True},
        request_overrides={"top_p": 0.8},
    )

    assert body == {
        "model": "claude-compatible",
        "messages": [{"role": "user", "content": "work"}],
        "max_tokens": 4096,
        "stream": True,
        "system": [{"type": "text", "text": "policy"}],
        "temperature": 0.2,
        "tools": [
            {
                "name": "read",
                "description": "Read a file",
                "input_schema": {"type": "object"},
            }
        ],
        "extra_headers": {"x-session-affinity": "session-1"},
        "metadata": {"user_id": "employee-1"},
        "tool_choice": {"type": "required"},
        "top_p": 0.8,
    }


def test_native_oauth_request_preserves_tool_casing_betas_and_thinking_compatibility() -> None:
    model = _model()
    assistant = AssistantMessage(
        content=[ToolCall(id="call-1", name="read", arguments={"path": "a.py"})],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="contents")],
        is_error=False,
    )
    context = Context(
        system_prompt="policy",
        messages=[UserMessage(content="work"), assistant, result],
        tools=[Tool(name="read", description="Read a file", parameters={"type": "object"})],
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=0.7,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "high"},
        cache_retention="short",
        tool_choice="required",
        context=context,
        target_model=model,
        model_compat={
            "supportsToolReferences": False,
            "supportsEagerToolInputStreaming": False,
        },
        api_key="sk-ant-oat-test-placeholder",
    )

    assert body["messages"][1]["content"][0]["name"] == "Read"
    assert body["tools"] == [
        {
            "name": "Read",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert [block["text"] for block in body["system"]] == [
        "You are Claude Code, Anthropic's official CLI for Claude.",
        "policy",
    ]
    assert body["thinking"] == {
        "type": "enabled",
        "budget_tokens": 3072,
        "display": "summarized",
    }
    assert "temperature" not in body
    assert body["tool_choice"] == {"type": "auto"}
    assert body["extra_headers"] == {
        "accept": "application/json",
        "anthropic-dangerous-direct-browser-access": "true",
        "anthropic-beta": (
            "claude-code-20250219,oauth-2025-04-20,"
            "fine-grained-tool-streaming-2025-05-14,"
            "interleaved-thinking-2025-05-14"
        ),
        "user-agent": "claude-cli/2.1.75",
        "x-app": "cli",
    }


def test_all_deferred_native_tools_fall_back_to_immediate_delivery() -> None:
    model = _model()
    context = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="call-load",
                tool_name="load_tools",
                content=[TextContent(text="loaded")],
                added_tool_names=["write"],
                is_error=False,
            )
        ],
        tools=[Tool(name="write", description="Write", parameters={"type": "object"})],
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        context=context,
        target_model=model,
        model_compat={"supportsToolReferences": True},
    )

    assert body["tools"] == [
        {
            "name": "write",
            "description": "Write",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "eager_input_streaming": True,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert "defer_loading" not in body["tools"][0]
