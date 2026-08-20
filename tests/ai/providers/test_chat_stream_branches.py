"""Direct branch characterizations for Chat Completions SSE payloads."""

from __future__ import annotations

import json

import pytest

from travis.ai.providers.chat_stream import parse_sse_chunks
from travis.ai.types import ErrorEvent, Model, TextContent, ThinkingContent, ToolCall


def _model(*, provider: str = "openai") -> Model:
    return Model(
        id="configured-model",
        name="Configured Model",
        api="openai-completions",
        provider=provider,
        base_url="https://provider.example/v1",
        reasoning=True,
    )


def _sse(chunk: object) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}"


@pytest.mark.parametrize("reasoning_field", ("reasoning_content", "reasoning", "reasoning_text"))
def test_alternate_reasoning_fields_preserve_metadata_and_text(
    reasoning_field: str,
) -> None:
    lines = [
        _sse(
            {
                "id": "response-1",
                "model": "served-model",
                "choices": [
                    {
                        "delta": {reasoning_field: "think", "content": "answer"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
    ]

    events = list(parse_sse_chunks(lines, _model()))

    message = events[-1].message
    assert message.response_id == "response-1"
    assert message.response_model == "served-model"
    assert isinstance(message.content[0], ThinkingContent)
    assert message.content[0].thinking == "think"
    assert message.content[0].thinking_signature == reasoning_field
    assert message.content[1].text == "answer"


def test_reasoning_is_suppressed_without_dropping_text() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "secret", "content": "visible"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
    ]

    events = list(parse_sse_chunks(lines, _model(), include_reasoning=False))

    assert len(events[-1].message.content) == 1
    assert events[-1].message.content[0].text == "visible"


def test_reasoning_only_output_limit_is_an_error() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "reasoning only"},
                        "finish_reason": "length",
                    }
                ]
            }
        )
    ]

    events = list(parse_sse_chunks(lines, _model(provider="opencode-go")))

    terminal = events[-1]
    assert isinstance(terminal, ErrorEvent)
    assert terminal.error.stop_reason == "error"
    assert terminal.error.error_message == (
        "Provider output token limit reached before producing user-visible text or a tool call"
    )


def test_visible_output_limit_remains_a_partial_response() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "reasoning",
                            "content": "partial answer",
                        },
                        "finish_reason": "length",
                    }
                ]
            }
        )
    ]

    events = list(parse_sse_chunks(lines, _model(provider="opencode-go")))

    terminal = events[-1]
    assert not isinstance(terminal, ErrorEvent)
    assert terminal.message.stop_reason == "length"
    assert isinstance(terminal.message.content[1], TextContent)
    assert terminal.message.content[1].text == "partial answer"


def test_split_tool_call_receives_deferred_encrypted_reasoning_detail() -> None:
    detail = {"type": "reasoning.encrypted", "id": "call-1", "data": "opaque"}
    lines = [
        _sse({"choices": [{"delta": {"reasoning_details": [detail]}}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {"name": "read", "arguments": '{"path":'},
                                }
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
                                {"index": 0, "function": {"arguments": '"README.md"}'}}
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
    ]

    events = list(parse_sse_chunks(lines, _model()))

    tool_call = events[-1].message.content[0]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.id == "call-1"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "README.md"}
    assert tool_call.thought_signature == json.dumps(detail, separators=(",", ":"))
    assert events[-1].reason == "toolUse"


def test_stream_without_finish_reason_fails_closed() -> None:
    events = list(
        parse_sse_chunks(
            ["data: not-json", _sse({"choices": [{"delta": {"content": "partial"}}]})],
            _model(),
        )
    )

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error.error_message == "Stream ended without finish_reason"
