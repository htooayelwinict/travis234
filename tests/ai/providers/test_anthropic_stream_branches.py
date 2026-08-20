"""Direct branch characterizations for the Anthropic SSE decoder."""

from __future__ import annotations

import json

from travis.ai.providers.anthropic_stream import decode_anthropic_stream
from travis.ai.types import ErrorEvent, Model, TextContent, ThinkingContent, ToolCall


def _model() -> Model:
    return Model(
        id="claude-test",
        name="Claude Test",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        reasoning=True,
    )


def _sse(event: object) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}"


def _max_tokens_lines(content_block: dict[str, object]) -> list[str]:
    return [
        _sse(
            {
                "type": "message_start",
                "message": {"id": "message-1", "usage": {"input_tokens": 1}},
            }
        ),
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": content_block,
            }
        ),
        _sse({"type": "content_block_stop", "index": 0}),
        _sse(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": 12_000},
            }
        ),
        _sse({"type": "message_stop"}),
    ]


def test_reasoning_only_output_limit_is_an_error() -> None:
    events = list(
        decode_anthropic_stream(
            _max_tokens_lines({"type": "thinking", "thinking": "reasoning only"}),
            _model(),
        )
    )

    terminal = events[-1]
    assert isinstance(terminal, ErrorEvent)
    assert terminal.error.stop_reason == "error"
    assert terminal.error.error_message == (
        "Provider output token limit reached before producing user-visible text or a tool call"
    )


def test_visible_output_limit_remains_a_partial_response() -> None:
    events = list(
        decode_anthropic_stream(
            _max_tokens_lines({"type": "text", "text": "partial answer"}),
            _model(),
        )
    )

    terminal = events[-1]
    assert not isinstance(terminal, ErrorEvent)
    assert terminal.message.stop_reason == "length"
    assert terminal.message.content == [TextContent(text="partial answer")]


def test_tool_output_limit_remains_available_for_truncated_tool_recovery() -> None:
    events = list(
        decode_anthropic_stream(
            _max_tokens_lines(
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read",
                    "input": {"path": "README.md"},
                }
            ),
            _model(),
        )
    )

    terminal = events[-1]
    assert not isinstance(terminal, ErrorEvent)
    assert terminal.message.stop_reason == "length"
    assert isinstance(terminal.message.content[0], ToolCall)


def test_content_block_lifecycles_preserve_text_thinking_tool_and_usage() -> None:
    lines = [
        _sse(
            {
                "type": "message_start",
                "message": {
                    "id": "message-1",
                    "usage": {"input_tokens": 10, "cache_read_input_tokens": 3},
                },
            }
        ),
        _sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "a"}}),
        _sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "b"}}),
        _sse({"type": "content_block_stop", "index": 0}),
        _sse(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "thinking", "thinking": "why", "signature": "sig-"},
            }
        ),
        _sse(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "thinking_delta", "thinking": " now"},
            }
        ),
        _sse(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "signature_delta", "signature": "tail"},
            }
        ),
        _sse({"type": "content_block_stop", "index": 1}),
        _sse(
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "call-1", "name": "read", "input": {}},
            }
        ),
        _sse(
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":"README.md"}'},
            }
        ),
        _sse({"type": "content_block_stop", "index": 2}),
        _sse(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 7},
            }
        ),
        _sse({"type": "message_stop"}),
    ]

    events = list(decode_anthropic_stream(lines, _model()))

    message = events[-1].message
    assert message.response_id == "message-1"
    assert message.stop_reason == "toolUse"
    assert message.usage.input == 10
    assert message.usage.cache_read == 3
    assert message.usage.output == 7
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "ab"
    assert isinstance(message.content[1], ThinkingContent)
    assert message.content[1].thinking == "why now"
    assert message.content[1].thinking_signature == "sig-tail"
    assert isinstance(message.content[2], ToolCall)
    assert message.content[2].arguments == {"path": "README.md"}


def test_reasoning_exclusion_drops_thinking_and_redacted_blocks() -> None:
    lines = [
        _sse({"type": "message_start", "message": {"id": "message-1"}}),
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "secret"},
            }
        ),
        _sse(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "redacted_thinking", "data": "opaque"},
            }
        ),
        _sse({"type": "message_stop"}),
    ]

    events = list(decode_anthropic_stream(lines, _model(), include_reasoning=False))

    assert [type(event).__name__ for event in events] == ["StartEvent", "DoneEvent"]
    assert events[-1].message.content == []


def test_redacted_reasoning_retains_marker_and_signature() -> None:
    lines = [
        _sse({"type": "message_start", "message": {"id": "message-1"}}),
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "redacted_thinking", "data": "opaque"},
            }
        ),
        _sse({"type": "content_block_stop", "index": 0}),
        _sse({"type": "message_stop"}),
    ]

    events = list(decode_anthropic_stream(lines, _model()))

    block = events[-1].message.content[0]
    assert isinstance(block, ThinkingContent)
    assert block.thinking == "[Reasoning redacted]"
    assert block.thinking_signature == "opaque"
    assert block.redacted is True


def test_error_and_truncated_stream_messages_are_stable() -> None:
    provider_error = list(
        decode_anthropic_stream(
            [_sse({"type": "error", "error": {"type": "overloaded", "message": "try later"}})],
            _model(),
        )
    )
    truncated = list(
        decode_anthropic_stream(
            [_sse({"type": "message_start", "message": {"id": "message-1"}})],
            _model(),
        )
    )

    assert isinstance(provider_error[-1], ErrorEvent)
    assert provider_error[-1].error.error_message == "try later"
    assert isinstance(truncated[-1], ErrorEvent)
    assert truncated[-1].error.error_message == "Anthropic stream ended before message_stop"
