"""Direct branch characterizations for the Bedrock event decoder."""

from __future__ import annotations

import pytest

from travis.ai.providers.bedrock_stream import _parse_bedrock_events
from travis.ai.types import ErrorEvent, Model, TextContent, ThinkingContent


def _model() -> Model:
    return Model(
        id="anthropic.claude-test",
        name="Claude Test",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
    )


def test_text_and_reasoning_deltas_create_implicit_blocks() -> None:
    events = list(
        _parse_bedrock_events(
            [
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": "hello"},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 1,
                        "delta": {
                            "reasoningContent": {
                                "text": "because",
                                "signature": "opaque",
                            }
                        },
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 1}},
                {"messageStop": {"stopReason": "end_turn"}},
            ],
            _model(),
        )
    )

    message = events[-1].message
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "hello"
    assert isinstance(message.content[1], ThinkingContent)
    assert message.content[1].thinking == "because"
    assert message.content[1].thinking_signature == "opaque"


@pytest.mark.parametrize(
    ("provider_reason", "expected_reason", "expected_error"),
    (
        ("end_turn", "stop", None),
        ("stop_sequence", "stop", None),
        ("max_tokens", "length", None),
        ("model_context_window_exceeded", "length", None),
        ("tool_use", "toolUse", None),
        ("guardrail_intervened", "error", "guardrail_intervened"),
        (None, "error", "Unknown Bedrock stop reason"),
    ),
)
def test_stop_reason_mapping_is_stable(
    provider_reason: str | None,
    expected_reason: str,
    expected_error: str | None,
) -> None:
    events = list(
        _parse_bedrock_events(
            [{"messageStop": {"stopReason": provider_reason}}],
            _model(),
        )
    )

    terminal = events[-1]
    assert terminal.reason == expected_reason
    if isinstance(terminal, ErrorEvent):
        assert terminal.error.error_message == expected_error
    else:
        assert terminal.message.error_message == expected_error


def test_provider_exception_event_becomes_error_event() -> None:
    events = list(
        _parse_bedrock_events(
            [{"throttlingException": {"message": "slow down"}}],
            _model(),
        )
    )

    assert [type(event).__name__ for event in events] == ["StartEvent", "ErrorEvent"]
    assert events[-1].error.error_message == "throttlingException: slow down"
