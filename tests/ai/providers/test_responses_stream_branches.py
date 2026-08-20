"""Direct branch characterizations for the Responses SSE decoder."""

from __future__ import annotations

import json

from travis.ai.providers.responses_stream import decode_responses_stream
from travis.ai.types import ErrorEvent, Model, ThinkingContent, ToolCall


def _model() -> Model:
    return Model(
        id="gpt-test",
        name="GPT Test",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=True,
    )


def _sse(event: object) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}"


def test_done_only_tool_item_creates_slot_before_finishing_call() -> None:
    lines = [
        _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "item-1",
                    "call_id": "call-1",
                    "name": "read",
                    "arguments": '{"path":"README.md"}',
                },
            }
        ),
        _sse(
            {
                "type": "response.completed",
                "response": {"id": "response-1", "status": "completed", "output": []},
            }
        ),
    ]

    events = list(decode_responses_stream(lines, _model()))

    assert [type(event).__name__ for event in events] == [
        "StartEvent",
        "ToolcallStartEvent",
        "ToolcallEndEvent",
        "DoneEvent",
    ]
    tool_call = events[-1].message.content[0]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.id == "call-1|item-1"
    assert tool_call.arguments == {"path": "README.md"}
    assert events[-1].reason == "toolUse"


def test_reasoning_item_is_backfilled_from_terminal_response_output() -> None:
    reasoning_item = {
        "type": "reasoning",
        "id": "reasoning-1",
        "summary": [{"text": "final thought"}],
    }
    lines = [
        _sse(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "reasoning-1"},
            }
        ),
        _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": reasoning_item,
            }
        ),
        _sse(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [{**reasoning_item, "encrypted_content": "opaque"}],
                },
            }
        ),
    ]

    events = list(decode_responses_stream(lines, _model()))

    block = events[-1].message.content[0]
    assert isinstance(block, ThinkingContent)
    assert block.thinking == "final thought"
    assert block.thinking_signature is not None
    assert json.loads(block.thinking_signature)["encrypted_content"] == "opaque"


def test_reasoning_events_are_ignored_when_reasoning_is_excluded() -> None:
    lines = [
        _sse(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "reasoning-1"},
            }
        ),
        _sse(
            {
                "type": "response.reasoning_text.delta",
                "output_index": 0,
                "delta": "secret",
            }
        ),
        _sse(
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": []},
            }
        ),
    ]

    events = list(decode_responses_stream(lines, _model(), include_reasoning=False))

    assert [type(event).__name__ for event in events] == ["StartEvent", "DoneEvent"]
    assert events[-1].message.content == []


def test_failed_response_uses_provider_message() -> None:
    lines = [
        "data: not-json",
        _sse(
            {
                "type": "response.failed",
                "response": {"error": {"code": "bad_request", "message": "request rejected"}},
            }
        ),
    ]

    events = list(decode_responses_stream(lines, _model()))

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error.error_message == "request rejected"


def test_stream_without_terminal_event_fails_closed() -> None:
    events = list(
        decode_responses_stream(
            [_sse({"type": "response.created", "response": {"id": "response-1"}})],
            _model(),
        )
    )

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error.error_message == (
        "Responses stream ended before a terminal response event"
    )
