"""Direct branch characterizations for Mistral Conversations chunks."""

from __future__ import annotations

import json

import pytest

from travis.ai.providers.mistral_stream import _decode_mistral_stream
from travis.ai.types import ErrorEvent, Model, TextContent, ThinkingContent, ToolCall


def _model() -> Model:
    return Model(
        id="devstral-test",
        name="Devstral Test",
        api="mistral-conversations",
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        reasoning=True,
    )


def _sse(chunk: object) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}"


def test_typed_content_list_switches_between_text_and_thinking() -> None:
    lines = [
        _sse(
            {
                "id": "response-1",
                "choices": [
                    {
                        "delta": {
                            "content": [
                                {"type": "text", "text": "first"},
                                {"type": "thinking", "thinking": [{"type": "text", "text": "why"}]},
                                "last",
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        )
    ]

    events = list(_decode_mistral_stream(lines, _model()))

    message = events[-1].message
    assert message.response_id == "response-1"
    assert [type(block) for block in message.content] == [
        TextContent,
        ThinkingContent,
        TextContent,
    ]
    assert message.content[0].text == "first"
    assert message.content[1].thinking == "why"
    assert message.content[2].text == "last"


def test_reasoning_exclusion_keeps_text_items() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "content": [
                                {"type": "thinking", "thinking": [{"type": "text", "text": "secret"}]},
                                {"type": "text", "text": "visible"},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )
    ]

    events = list(_decode_mistral_stream(lines, _model(), include_reasoning=False))

    assert len(events[-1].message.content) == 1
    assert events[-1].message.content[0].text == "visible"


def test_camel_case_usage_and_split_tool_call_are_merged() -> None:
    lines = [
        _sse(
            {
                "usage": {
                    "promptTokens": 10,
                    "completionTokens": 4,
                    "promptTokensDetails": {"cachedTokens": 3},
                    "totalTokens": 14,
                },
                "choices": [
                    {
                        "delta": {
                            "toolCalls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {"name": "read", "arguments": '{"path":'},
                                }
                            ]
                        }
                    }
                ],
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "toolCalls": [
                                {"index": 0, "function": {"arguments": '"README.md"}'}}
                            ]
                        },
                        "finishReason": "tool_calls",
                    }
                ]
            }
        ),
    ]

    events = list(_decode_mistral_stream(lines, _model()))

    message = events[-1].message
    block = message.content[0]
    assert isinstance(block, ToolCall)
    assert block.id == "call-1"
    assert block.name == "read"
    assert block.arguments == {"path": "README.md"}
    assert message.usage.input == 7
    assert message.usage.cache_read == 3
    assert message.usage.output == 4
    assert message.stop_reason == "toolUse"


@pytest.mark.parametrize(
    ("provider_reason", "expected_reason", "is_error"),
    (
        ("stop", "stop", False),
        ("length", "length", False),
        ("model_length", "length", False),
        ("tool_calls", "toolUse", False),
        ("error", "error", True),
        ("future_reason", "stop", False),
    ),
)
def test_stop_reason_mapping_is_stable(
    provider_reason: str,
    expected_reason: str,
    is_error: bool,
) -> None:
    events = list(
        _decode_mistral_stream(
            [_sse({"choices": [{"delta": {}, "finish_reason": provider_reason}]})],
            _model(),
        )
    )

    terminal = events[-1]
    assert terminal.reason == expected_reason
    assert isinstance(terminal, ErrorEvent) is is_error
    if isinstance(terminal, ErrorEvent):
        assert terminal.error.error_message == "Mistral provider stream failed"
