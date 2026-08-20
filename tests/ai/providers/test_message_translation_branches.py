"""Direct branch characterizations for chat message translation."""

from __future__ import annotations

import json

from travis.ai.providers.message_translation import _convert_message, _transform_messages
from travis.ai.providers.openai_compat import OpenAICompat
from travis.ai.types import (
    AssistantMessage,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)


def _model(*, provider: str = "openai", model_id: str = "target") -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://provider.example/v1",
        reasoning=True,
    )


def _assistant(
    content: list[TextContent | ThinkingContent | ToolCall],
    *,
    provider: str = "source",
    model_id: str = "source-model",
    stop_reason: str = "toolUse",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider=provider,
        model=model_id,
        usage=empty_usage(),
        stop_reason=stop_reason,
    )


def test_cross_model_transform_normalizes_tool_identity_and_reasoning() -> None:
    assistant = _assistant(
        [
            ThinkingContent(thinking="redacted", thinking_signature="opaque", redacted=True),
            ThinkingContent(thinking="foreign thought", thinking_signature="reasoning"),
            TextContent(text="answer"),
            ToolCall(
                id="call|foreign-item",
                name="read",
                arguments={"path": "README.md"},
                thought_signature="encrypted",
            ),
        ]
    )
    result = ToolResultMessage(
        tool_call_id="call|foreign-item",
        tool_name="read",
        content=[TextContent(text="contents")],
        is_error=False,
    )

    transformed = _transform_messages(
        [assistant, result],
        _model(),
        lambda _tool_call_id, _target, _source: "normalized-call",
    )

    replayed_assistant = transformed[0]
    assert isinstance(replayed_assistant, AssistantMessage)
    assert isinstance(replayed_assistant.content[0], TextContent)
    assert replayed_assistant.content[0].text == "foreign thought"
    assert replayed_assistant.content[1].text == "answer"
    tool_call = replayed_assistant.content[2]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.id == "normalized-call"
    assert tool_call.thought_signature is None
    replayed_result = transformed[1]
    assert isinstance(replayed_result, ToolResultMessage)
    assert replayed_result.tool_call_id == "normalized-call"


def test_same_model_transform_preserves_signed_blocks_and_drops_blank_thinking() -> None:
    redacted = ThinkingContent(
        thinking="redacted",
        thinking_signature="opaque",
        redacted=True,
    )
    signed = ThinkingContent(thinking="reason", thinking_signature="signed")
    tool_call = ToolCall(
        id="original-call",
        name="read",
        arguments={},
        thought_signature="tool-signature",
    )
    assistant = _assistant(
        [redacted, signed, ThinkingContent(thinking="  "), tool_call],
        provider="openai",
        model_id="target",
    )

    transformed = _transform_messages([assistant], _model())

    replayed = transformed[0]
    assert isinstance(replayed, AssistantMessage)
    assert replayed.content == [redacted, signed, tool_call]


def test_transform_repairs_missing_tool_results_and_drops_failed_unanswered_user() -> None:
    tool_call = ToolCall(id="call-1", name="read", arguments={})
    repaired = _transform_messages(
        [_assistant([tool_call]), UserMessage(content="next")],
        _model(),
    )
    failed = _transform_messages(
        [
            UserMessage(content="cancelled request"),
            _assistant([TextContent(text="partial")], stop_reason="aborted"),
        ],
        _model(),
    )

    assert [message.role for message in repaired] == ["assistant", "toolResult", "user"]
    synthetic = repaired[1]
    assert isinstance(synthetic, ToolResultMessage)
    assert synthetic.tool_call_id == "call-1"
    assert synthetic.is_error is True
    assert synthetic.content[0].text == "No result provided"
    assert failed == []


def test_assistant_conversion_preserves_native_and_textual_reasoning_channels() -> None:
    native_reasoning = {"type": "reasoning", "id": "reasoning-1", "summary": []}
    tool_detail = {"type": "reasoning.encrypted", "id": "call-1", "data": "opaque"}
    assistant = _assistant(
        [
            ThinkingContent(thinking="native thought", thinking_signature=json.dumps(native_reasoning)),
            ThinkingContent(thinking="visible thought", thinking_signature="reasoning"),
            TextContent(text="answer"),
            ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "README.md"},
                thought_signature=json.dumps(tool_detail),
            ),
        ],
        provider="opencode-go",
        model_id="target",
    )

    converted = _convert_message(
        assistant,
        _model(provider="opencode-go"),
        OpenAICompat(),
    )

    assert converted is not None
    assert converted["codex_reasoning_items"] == [native_reasoning]
    assert converted["reasoning_content"] == "visible thought"
    assert converted["content"] == "answer"
    assert converted["reasoning_details"] == [tool_detail]
    assert converted["tool_calls"][0]["function"]["arguments"] == '{"path":"README.md"}'


def test_empty_assistant_message_is_omitted() -> None:
    assert _convert_message(_assistant([]), _model(), OpenAICompat()) is None


def test_assistant_conversion_can_embed_thinking_and_require_reasoning_field() -> None:
    embedded = _convert_message(
        _assistant(
            [
                ThinkingContent(thinking="thought", thinking_signature="not-json"),
                TextContent(text="answer"),
            ]
        ),
        _model(),
        OpenAICompat(requires_thinking_as_text=True),
    )
    tool_only = _convert_message(
        _assistant([ToolCall(id="call-1", name="read", arguments={})]),
        _model(),
        OpenAICompat(requires_reasoning_content_on_assistant_messages=True),
    )

    assert embedded is not None
    assert embedded["content"] == [
        {"type": "text", "text": "thought"},
        {"type": "text", "text": "answer"},
    ]
    assert tool_only is not None
    assert tool_only["reasoning_content"] == ""
