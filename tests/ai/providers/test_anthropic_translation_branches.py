"""Direct branch characterizations for native Anthropic translation."""

from __future__ import annotations

from travis.ai.providers.transport_families.anthropic import _anthropic_native_messages
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
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
        input=["text", "image"],
    )


def _assistant(content: list[TextContent | ThinkingContent | ToolCall]) -> AssistantMessage:
    model = _model()
    return AssistantMessage(
        content=content,
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )


def test_native_messages_preserve_reasoning_tools_grouped_results_and_references() -> None:
    assistant = _assistant(
        [
            TextContent(text="answer"),
            ThinkingContent(
                thinking="redacted",
                thinking_signature="redacted-data",
                redacted=True,
            ),
            ThinkingContent(thinking="signed", thinking_signature="thinking-signature"),
            ThinkingContent(thinking="unsigned"),
            ToolCall(id="call-load", name="load_tools", arguments={"names": ["write"]}),
        ]
    )
    loaded = ToolResultMessage(
        tool_call_id="call-load",
        tool_name="load_tools",
        content=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        added_tool_names=["write"],
        is_error=False,
    )
    repeated = ToolResultMessage(
        tool_call_id="call-repeat",
        tool_name="load_tools",
        content=[TextContent(text="already loaded")],
        added_tool_names=["write"],
        is_error=True,
    )
    context = Context(
        messages=[
            UserMessage(content="   "),
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ]
            ),
            assistant,
            loaded,
            repeated,
        ]
    )

    messages = _anthropic_native_messages(
        context,
        _model(),
        {"type": "ephemeral"},
        deferred_tool_names={"WRITE"},
        normalize_tool_name=str.upper,
    )

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aW1hZ2U=",
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {"type": "redacted_thinking", "data": "redacted-data"},
                {
                    "type": "thinking",
                    "thinking": "signed",
                    "signature": "thinking-signature",
                },
                {"type": "text", "text": "unsigned"},
                {
                    "type": "tool_use",
                    "id": "call-load",
                    "name": "LOAD_TOOLS",
                    "input": {"names": ["write"]},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-load",
                    "content": [{"type": "tool_reference", "tool_name": "WRITE"}],
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call-repeat",
                    "content": "already loaded",
                    "is_error": True,
                },
                {"type": "text", "text": "(see attached image)"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aW1hZ2U=",
                    },
                    "cache_control": {"type": "ephemeral"},
                },
            ],
        },
    ]


def test_allow_empty_signature_preserves_unsigned_thinking_block() -> None:
    assistant = _assistant([ThinkingContent(thinking="unsigned")])

    messages = _anthropic_native_messages(
        Context(messages=[assistant]),
        _model(),
        None,
        allow_empty_signature=True,
    )

    assert messages == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "unsigned", "signature": ""}
            ],
        }
    ]


def test_string_user_cache_control_is_wrapped_on_last_message() -> None:
    messages = _anthropic_native_messages(
        Context(messages=[UserMessage(content="hello")]),
        _model(),
        {"type": "ephemeral", "ttl": "1h"},
    )

    assert messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "hello",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
        }
    ]
