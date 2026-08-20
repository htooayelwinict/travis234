"""Direct branch characterizations for Bedrock request translation."""

from __future__ import annotations

import pytest

from travis.ai.providers.transport_families.bedrock import (
    _bedrock_image,
    _bedrock_messages,
)
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


def _model(*, claude: bool = True) -> Model:
    model_id = "anthropic.claude-3-7-sonnet" if claude else "amazon.nova-pro"
    return Model(
        id=model_id,
        name=model_id,
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
        input=["text", "image"],
    )


def _assistant(
    content: list[TextContent | ThinkingContent | ToolCall],
    model: Model,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )


def test_bedrock_messages_preserve_user_assistant_grouped_results_and_cache() -> None:
    model = _model()
    long_id = "call-" + ("x" * 80)
    assistant = _assistant(
        [
            TextContent(text="answer"),
            ThinkingContent(thinking="unsigned"),
            ThinkingContent(thinking="signed", thinking_signature="opaque"),
            ToolCall(id=long_id, name="read", arguments={"path": "a.py"}),
        ],
        model,
    )
    first_result = ToolResultMessage(
        tool_call_id=long_id,
        tool_name="read",
        content=[TextContent(text="contents")],
        is_error=False,
    )
    second_result = ToolResultMessage(
        tool_call_id="call-empty",
        tool_name="write",
        content=[],
        is_error=True,
    )
    context = Context(
        messages=[
            UserMessage(content=""),
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ]
            ),
            assistant,
            first_result,
            second_result,
        ]
    )

    messages = _bedrock_messages(context, model, "long")

    assert messages == [
        {"role": "user", "content": [{"text": "<empty>"}]},
        {
            "role": "user",
            "content": [
                {"text": "look"},
                {"image": {"format": "png", "source": {"bytes": b"image"}}},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"text": "answer"},
                {"text": "unsigned"},
                {
                    "reasoningContent": {
                        "reasoningText": {"text": "signed", "signature": "opaque"}
                    }
                },
                {
                    "toolUse": {
                        "toolUseId": long_id[:64],
                        "name": "read",
                        "input": {"path": "a.py"},
                    }
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": long_id[:64],
                        "content": [{"text": "contents"}],
                        "status": "success",
                    }
                },
                {
                    "toolResult": {
                        "toolUseId": "call-empty",
                        "content": [{"text": "<empty>"}],
                        "status": "error",
                    }
                },
                {"cachePoint": {"type": "default", "ttl": "1h"}},
            ],
        },
    ]


def test_non_claude_thinking_uses_reasoning_content_without_signature() -> None:
    model = _model(claude=False)
    assistant = _assistant([ThinkingContent(thinking="reason")], model)

    messages = _bedrock_messages(Context(messages=[assistant]), model, "none")

    assert messages == [
        {
            "role": "assistant",
            "content": [
                {"reasoningContent": {"reasoningText": {"text": "reason"}}}
            ],
        }
    ]


def test_cache_is_not_added_for_unsupported_model() -> None:
    model = _model(claude=False)

    messages = _bedrock_messages(
        Context(messages=[UserMessage(content="work")]),
        model,
        "long",
    )

    assert messages == [{"role": "user", "content": [{"text": "work"}]}]


def test_bedrock_image_rejects_unknown_mime_type() -> None:
    with pytest.raises(ValueError, match="Unknown image type: image/tiff"):
        _bedrock_image(ImageContent(data="aW1hZ2U=", mime_type="image/tiff"))
