from __future__ import annotations

from appv22.ai.overflow import is_context_overflow
from appv22.ai.overflow import parse_available_output_tokens_from_error


def test_detects_overflow_messages() -> None:
    assert is_context_overflow("This prompt is too long for the model")
    assert is_context_overflow("context_length_exceeded")
    assert is_context_overflow("input token count of 200000 exceeds the maximum")


def test_ignores_rate_limit_and_throttling() -> None:
    assert not is_context_overflow("Throttling error: slow down")
    assert not is_context_overflow("rate limit reached, too many requests")
    assert not is_context_overflow("")


def test_ignores_hermes_output_cap_errors() -> None:
    error = (
        "max_tokens: 32768 > context_window: 200000 - input_tokens: 190000 "
        "= available_tokens: 10000"
    )

    assert not is_context_overflow(error)
    assert parse_available_output_tokens_from_error(error) == 10000
