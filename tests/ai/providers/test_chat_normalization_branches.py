"""Direct branch characterizations for Chat Completions response normalization."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from travis.ai.providers.transports import ChatCompletionsTransport


class _DumpableExtra:
    def model_dump(self) -> object:
        return {"signature": "dumped"}


class _BrokenDumpExtra:
    def model_dump(self) -> object:
        raise RuntimeError("cannot dump provider extension")


class _NoneDumpExtra:
    def model_dump(self) -> object:
        return None


def _response(
    message: object,
    *,
    finish_reason: str | None = "stop",
    usage: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage,
    )


def test_tool_calls_preserve_direct_fallback_and_failed_dump_extensions() -> None:
    direct_extra = _DumpableExtra()
    failed_dump_extra = _BrokenDumpExtra()
    message = SimpleNamespace(
        content=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
        refusal=None,
        tool_calls=[
            SimpleNamespace(
                id="call-direct",
                function=SimpleNamespace(name="read", arguments='{"path":"a.py"}'),
                extra_content=direct_extra,
                model_extra={"extra_content": {"signature": "ignored"}},
            ),
            SimpleNamespace(
                model_extra={"extra_content": failed_dump_extra},
            ),
            SimpleNamespace(
                id="call-empty",
                function=None,
                extra_content=None,
                model_extra=["not", "a", "mapping"],
            ),
            SimpleNamespace(
                id="call-none-dump",
                function=None,
                extra_content=_NoneDumpExtra(),
            ),
        ],
    )

    normalized = ChatCompletionsTransport().normalize_response(_response(message))

    assert normalized.tool_calls is not None
    assert len(normalized.tool_calls) == 4
    assert normalized.tool_calls[0].id == "call-direct"
    assert normalized.tool_calls[0].name == "read"
    assert normalized.tool_calls[0].arguments == '{"path":"a.py"}'
    assert normalized.tool_calls[0].provider_data == {
        "extra_content": {"signature": "dumped"}
    }
    assert normalized.tool_calls[1].id is None
    assert normalized.tool_calls[1].name == ""
    assert normalized.tool_calls[1].arguments == ""
    assert normalized.tool_calls[1].provider_data == {
        "extra_content": failed_dump_extra
    }
    assert normalized.tool_calls[2].provider_data is None
    assert normalized.tool_calls[3].provider_data == {"extra_content": None}


def test_usage_and_reasoning_preserve_numeric_coercion_and_fallback_metadata() -> None:
    message = SimpleNamespace(
        content="answer",
        reasoning="primary reasoning",
        reasoning_content=None,
        reasoning_details=[{"type": "reasoning.encrypted", "data": "opaque"}],
        refusal=None,
        tool_calls=None,
        model_extra={"reasoning_content": "fallback reasoning"},
    )
    usage = SimpleNamespace(
        prompt_tokens="12",
        completion_tokens=None,
        cached_tokens="2",
    )

    normalized = ChatCompletionsTransport().normalize_response(
        _response(message, usage=usage)
    )

    assert normalized.reasoning == "primary reasoning"
    assert normalized.provider_data == {
        "reasoning_content": "fallback reasoning",
        "reasoning_details": [
            {"type": "reasoning.encrypted", "data": "opaque"}
        ],
    }
    assert normalized.usage is not None
    assert normalized.usage.prompt_tokens == 12
    assert normalized.usage.completion_tokens == 0
    assert normalized.usage.total_tokens == 0
    assert normalized.usage.cached_tokens == 2


def test_model_extra_refusal_replaces_empty_stop_response() -> None:
    message = SimpleNamespace(
        content=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
        tool_calls=None,
        model_extra={"refusal": "Request blocked"},
    )

    normalized = ChatCompletionsTransport().normalize_response(
        _response(message, finish_reason=None)
    )

    assert normalized.content == "Request blocked"
    assert normalized.finish_reason == "content_filter"
    assert normalized.provider_data == {"refusal": "Request blocked"}
    assert normalized.reasoning is None
    assert normalized.usage is None


@pytest.mark.parametrize(
    ("content", "tool_calls", "finish_reason", "expected_content", "expected_finish"),
    [
        ("existing", None, "stop", "existing", "stop"),
        (None, [SimpleNamespace(id="call-1", function=None)], None, None, "stop"),
        (None, None, "length", "Request blocked", "length"),
    ],
)
def test_refusal_preserves_existing_content_tools_and_non_stop_finish_reasons(
    content: str | None,
    tool_calls: list[object] | None,
    finish_reason: str | None,
    expected_content: str | None,
    expected_finish: str,
) -> None:
    message = SimpleNamespace(
        content=content,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
        refusal="Request blocked",
        tool_calls=tool_calls,
    )

    normalized = ChatCompletionsTransport().normalize_response(
        _response(message, finish_reason=finish_reason)
    )

    assert normalized.content == expected_content
    assert normalized.finish_reason == expected_finish
    assert normalized.provider_data == {"refusal": "Request blocked"}


def test_blank_refusal_does_not_create_provider_metadata() -> None:
    message = SimpleNamespace(
        content=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
        refusal="   ",
        tool_calls=None,
    )

    normalized = ChatCompletionsTransport().normalize_response(_response(message))

    assert normalized.content is None
    assert normalized.finish_reason == "stop"
    assert normalized.provider_data is None


def test_response_shape_and_usage_conversion_errors_still_escape() -> None:
    transport = ChatCompletionsTransport()

    with pytest.raises(AttributeError):
        transport.normalize_response(SimpleNamespace())
    with pytest.raises(IndexError):
        transport.normalize_response(SimpleNamespace(choices=[]))

    message = SimpleNamespace(
        content="answer",
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
        refusal=None,
        tool_calls=None,
    )
    invalid_usage = SimpleNamespace(prompt_tokens="not-an-integer")
    with pytest.raises(ValueError, match="invalid literal"):
        transport.normalize_response(_response(message, usage=invalid_usage))
