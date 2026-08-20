"""Direct branch characterizations for Google SSE chunks."""

from __future__ import annotations

import json

import pytest

from travis.ai.providers.google_stream import _parse_google_sse_chunks
from travis.ai.types import ErrorEvent, Model, TextContent, ThinkingContent, ToolCall


def _model() -> Model:
    return Model(
        id="gemini-test",
        name="Gemini Test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        reasoning=True,
    )


def _sse(chunk: object) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}"


def _candidate(parts: list[object], finish_reason: str = "STOP") -> str:
    return _sse(
        {
            "candidates": [
                {
                    "content": {"parts": parts},
                    "finishReason": finish_reason,
                }
            ]
        }
    )


def test_alternating_text_and_thinking_parts_close_each_previous_block() -> None:
    events = list(
        _parse_google_sse_chunks(
            [
                _candidate(
                    [
                        {"text": "first"},
                        {"text": "why", "thought": True, "thoughtSignature": "sig"},
                        {"text": "last", "thoughtSignature": "text-sig"},
                    ]
                )
            ],
            _model(),
        )
    )

    message = events[-1].message
    assert [type(block) for block in message.content] == [
        TextContent,
        ThinkingContent,
        TextContent,
    ]
    assert message.content[0].text == "first"
    assert message.content[1].thinking == "why"
    assert message.content[1].thinking_signature == "sig"
    assert message.content[2].text == "last"
    assert message.content[2].text_signature == "text-sig"


def test_reasoning_exclusion_keeps_neighboring_text() -> None:
    events = list(
        _parse_google_sse_chunks(
            [_candidate([{"text": "secret", "thought": True}, {"text": "visible"}])],
            _model(),
            include_reasoning=False,
        )
    )

    assert len(events[-1].message.content) == 1
    assert events[-1].message.content[0].text == "visible"


def test_duplicate_provider_tool_ids_receive_distinct_fallback_id() -> None:
    events = list(
        _parse_google_sse_chunks(
            [
                _candidate(
                    [
                        {"functionCall": {"id": "same", "name": "read", "args": {}}},
                        {"functionCall": {"id": "same", "name": "write", "args": {}}},
                    ]
                )
            ],
            _model(),
        )
    )

    first, second = events[-1].message.content
    assert isinstance(first, ToolCall)
    assert isinstance(second, ToolCall)
    assert first.id == "same"
    assert second.id != "same"
    assert second.id.startswith("write_")


@pytest.mark.parametrize(
    ("finish_reason", "expected_reason", "expected_error"),
    (
        ("STOP", "stop", None),
        ("MAX_TOKENS", "length", None),
        ("SAFETY", "error", "Provider finish_reason: SAFETY"),
    ),
)
def test_finish_reason_mapping_is_stable(
    finish_reason: str,
    expected_reason: str,
    expected_error: str | None,
) -> None:
    events = list(
        _parse_google_sse_chunks(
            [_candidate([], finish_reason)],
            _model(),
        )
    )

    terminal = events[-1]
    assert terminal.reason == expected_reason
    if isinstance(terminal, ErrorEvent):
        assert terminal.error.error_message == expected_error


def test_provider_error_payload_preserves_message() -> None:
    events = list(
        _parse_google_sse_chunks(
            [_sse({"error": {"code": 429, "message": "quota exceeded"}})],
            _model(),
        )
    )

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error.error_message == "quota exceeded"
