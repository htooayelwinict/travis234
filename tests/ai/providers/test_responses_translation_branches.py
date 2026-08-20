"""Direct branch characterizations for OpenAI Responses translation."""

from __future__ import annotations

import json

import pytest

from travis.ai.providers.responses_translation import convert_responses_messages
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)


def _model(
    *,
    model_id: str = "target",
    images: bool = True,
    supports_developer: bool = True,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=True,
        input=["text", "image"] if images else ["text"],
        compat={"supportsDeveloperRole": supports_developer},
    )


def _assistant(
    content: list[TextContent | ThinkingContent | ToolCall],
    *,
    model_id: str = "target",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api="openai-responses",
        provider="openai",
        model=model_id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )


def _convert(model: Model, context: Context) -> list[dict[str, object]]:
    return convert_responses_messages(model, context, {"openai"})


def test_system_role_and_user_content_shapes_are_preserved() -> None:
    context = Context(
        system_prompt="policy",
        messages=[
            UserMessage(content="hello"),
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ]
            ),
            UserMessage(content=[]),
        ],
    )

    developer_output = _convert(_model(), context)
    system_output = _convert(_model(supports_developer=False), context)
    promptless_output = convert_responses_messages(
        _model(),
        context,
        {"openai"},
        include_system_prompt=False,
    )

    assert developer_output[0] == {"role": "developer", "content": "policy"}
    assert system_output[0] == {"role": "system", "content": "policy"}
    assert developer_output[1:] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                },
            ],
        },
    ]
    assert promptless_output == developer_output[1:]


def test_assistant_items_preserve_reasoning_text_metadata_and_native_tool_id() -> None:
    reasoning_item = {
        "type": "reasoning",
        "id": "reasoning-1",
        "summary": [],
        "encrypted_content": "opaque",
    }
    message_signature = json.dumps(
        {"v": 1, "id": "message-1", "phase": "commentary"},
        separators=(",", ":"),
    )
    assistant = _assistant(
        [
            ThinkingContent(thinking="", thinking_signature=json.dumps(reasoning_item)),
            TextContent(text="answer", text_signature=message_signature),
            ToolCall(id="call-1|fc-item-1", name="read", arguments={"path": "a.py"}),
        ]
    )
    result = ToolResultMessage(
        tool_call_id="call-1|fc-item-1",
        tool_name="read",
        content=[TextContent(text="contents")],
        is_error=False,
    )

    output = _convert(_model(), Context(messages=[assistant, result]))

    assert output[0] == reasoning_item
    assert output[1] == {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": "answer",
                "annotations": [],
            }
        ],
        "status": "completed",
        "id": "message-1",
        "phase": "commentary",
    }
    assert output[2] == {
        "type": "function_call",
        "call_id": "call-1",
        "name": "read",
        "arguments": '{"path":"a.py"}',
        "id": "fc-item-1",
    }
    assert output[3] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "contents",
    }


def test_cross_model_function_call_drops_stale_item_id() -> None:
    assistant = _assistant(
        [ToolCall(id="call-1|fc-stale", name="read", arguments={})],
        model_id="older-model",
    )
    result = ToolResultMessage(
        tool_call_id="call-1|fc-stale",
        tool_name="read",
        content=[TextContent(text="done")],
        is_error=False,
    )

    output = _convert(_model(), Context(messages=[assistant, result]))

    assert output[0] == {
        "type": "function_call",
        "call_id": "call-1",
        "name": "read",
        "arguments": "{}",
    }


def test_tool_outputs_preserve_supported_images_and_empty_placeholders() -> None:
    image_result = ToolResultMessage(
        tool_call_id="call-image",
        tool_name="look",
        content=[
            TextContent(text="caption"),
            ImageContent(data="aW1hZ2U=", mime_type="image/png"),
        ],
        is_error=False,
    )
    empty_result = ToolResultMessage(
        tool_call_id="call-empty",
        tool_name="read",
        content=[],
        is_error=False,
    )

    image_output = _convert(_model(), Context(messages=[image_result]))
    text_only_output = _convert(_model(images=False), Context(messages=[image_result]))
    empty_output = _convert(_model(), Context(messages=[empty_result]))

    assert image_output[0]["output"] == [
        {"type": "input_text", "text": "caption"},
        {
            "type": "input_image",
            "detail": "auto",
            "image_url": "data:image/png;base64,aW1hZ2U=",
        },
    ]
    assert text_only_output[0]["output"] == (
        "caption\n(tool image omitted: model does not support images)"
    )
    assert empty_output[0]["output"] == "(no tool output)"


def test_deferred_tool_search_is_emitted_once_for_repeated_load_names() -> None:
    write_tool = Tool(
        name="write",
        description="Write a file",
        parameters={"type": "object"},
    )
    result = ToolResultMessage(
        tool_call_id="call-load",
        tool_name="load_tools",
        content=[TextContent(text="loaded")],
        added_tool_names=["write", "write", "missing"],
        is_error=False,
    )
    repeated = ToolResultMessage(
        tool_call_id="call-load-again",
        tool_name="load_tools",
        content=[TextContent(text="already loaded")],
        added_tool_names=["write"],
        is_error=False,
    )

    output = convert_responses_messages(
        _model(),
        Context(messages=[result, repeated]),
        {"openai"},
        deferred_tools={"write": write_tool},
    )

    searches = [item for item in output if item.get("type") == "tool_search_call"]
    search_outputs = [item for item in output if item.get("type") == "tool_search_output"]
    assert len(searches) == 1
    assert searches[0]["arguments"] == {"query": "write", "limit": 1}
    assert len(search_outputs) == 1
    assert search_outputs[0]["tools"] == [
        {
            "type": "function",
            "name": "write",
            "description": "Write a file",
            "parameters": {"type": "object"},
            "strict": False,
            "defer_loading": True,
        }
    ]


def test_malformed_native_reasoning_signature_keeps_json_error_behavior() -> None:
    assistant = _assistant(
        [ThinkingContent(thinking="reason", thinking_signature="not-json")]
    )

    with pytest.raises(json.JSONDecodeError):
        _convert(_model(), Context(messages=[assistant]))
