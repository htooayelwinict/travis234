"""Direct branch characterizations for Mistral request translation."""

from __future__ import annotations

import pytest

from travis.ai.providers.transport_families.mistral import (
    _mistral_messages,
    _mistral_tool_result_text,
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


def _model(*, images: bool = True) -> Model:
    return Model(
        id="magistral",
        name="Magistral",
        api="mistral-conversations",
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        reasoning=True,
        input=["text", "image"] if images else ["text"],
    )


@pytest.mark.parametrize(
    ("content", "is_error", "supports_images", "expected"),
    [
        ([TextContent(text=" output ")], False, False, "output"),
        (
            [TextContent(text="output"), ImageContent(data="aQ==", mime_type="image/png")],
            True,
            False,
            "[tool error] output\n[tool image omitted: model does not support images]",
        ),
        (
            [ImageContent(data="aQ==", mime_type="image/png")],
            False,
            True,
            "(see attached image)",
        ),
        ([], True, False, "[tool error] (no tool output)"),
    ],
)
def test_tool_result_text_preserves_error_image_and_empty_branches(
    content: list[TextContent | ImageContent],
    is_error: bool,
    supports_images: bool,
    expected: str,
) -> None:
    message = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=content,
        is_error=is_error,
    )

    assert _mistral_tool_result_text(message, supports_images) == expected


def test_mistral_messages_preserve_all_roles_and_native_content_shapes() -> None:
    assistant = AssistantMessage(
        content=[
            ThinkingContent(thinking="reason", thinking_signature="opaque"),
            TextContent(text="answer"),
            ToolCall(id="123456789", name="read", arguments={"path": "a.py"}),
        ],
        api="mistral-conversations",
        provider="mistral",
        model="magistral",
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="123456789",
        tool_name="read",
        content=[
            TextContent(text="contents"),
            ImageContent(data="aW1hZ2U=", mime_type="image/png"),
        ],
        is_error=False,
    )
    context = Context(
        messages=[
            UserMessage(content="work"),
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ]
            ),
            assistant,
            result,
        ]
    )

    messages = _mistral_messages(context, _model())

    assert messages == [
        {"role": "user", "content": "work"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image_url",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                },
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": [{"type": "text", "text": "reason"}]},
                {"type": "text", "text": "answer"},
            ],
            "tool_calls": [
                {
                    "id": "123456789",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"a.py"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "123456789",
            "name": "read",
            "content": [
                {"type": "text", "text": "contents"},
                {
                    "type": "image_url",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                },
            ],
        },
    ]


def test_non_image_model_receives_downgraded_user_and_tool_placeholders() -> None:
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="look",
        content=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        is_error=False,
    )
    context = Context(
        messages=[
            UserMessage(content=[ImageContent(data="aW1hZ2U=", mime_type="image/png")]),
            result,
        ]
    )

    messages = _mistral_messages(context, _model(images=False))

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "(image omitted: model does not support images)"}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "look",
            "content": [
                {
                    "type": "text",
                    "text": "(tool image omitted: model does not support images)",
                }
            ],
        },
    ]
