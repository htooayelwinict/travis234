from __future__ import annotations

import json

import pytest

from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.responses_stream import decode_responses_stream
from travis.ai.providers.transports import ChatCompletionsTransport
from travis.ai.types import Model


@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("zai", "https://api.z.ai/api/coding/paas/v4"),
        ("zai-coding-cn", "https://open.bigmodel.cn/api/coding/paas/v4"),
    ],
)
def test_direct_zai_routes_send_output_limit_as_max_tokens(
    provider: str,
    base_url: str,
) -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="glm-5.2",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(
            name=provider,
            base_url=base_url,
            default_max_tokens=8_192,
        ),
        stream=True,
        temperature=None,
        max_tokens=4_096,
        base_url=base_url,
        model_reasoning=True,
    )

    assert body["max_tokens"] == 4_096
    assert "max_completion_tokens" not in body


def test_direct_zai_explicit_max_tokens_field_override_still_wins() -> None:
    base_url = "https://api.z.ai/api/coding/paas/v4"
    body = ChatCompletionsTransport().build_kwargs(
        model="custom-zai-model",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(
            name="zai",
            base_url=base_url,
            default_max_tokens=8_192,
        ),
        stream=True,
        temperature=None,
        max_tokens=2_048,
        base_url=base_url,
        model_compat={"maxTokensField": "max_completion_tokens"},
    )

    assert body["max_completion_tokens"] == 2_048
    assert "max_tokens" not in body


def _responses_terminal_message(events):
    terminal = events[-1]
    return terminal.error if hasattr(terminal, "error") else terminal.message


@pytest.mark.parametrize(
    ("incomplete_reason", "expected_stop", "expected_error"),
    [
        ("max_output_tokens", "length", None),
        ("max_tokens", "length", None),
        ("content_filter", "error", "Response incomplete: content_filter"),
        ("future_provider_reason", "error", "Response incomplete: future_provider_reason"),
        (None, "error", "Response incomplete without a provider reason"),
    ],
)
def test_responses_stream_classifies_incomplete_provider_reason(
    incomplete_reason: str | None,
    expected_stop: str,
    expected_error: str | None,
) -> None:
    model = Model(
        id="gpt-5.4",
        name="GPT-5.4",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )
    details = {} if incomplete_reason is None else {"reason": incomplete_reason}
    payload = {
        "type": "response.incomplete",
        "response": {
            "id": "resp_incomplete",
            "status": "incomplete",
            "incomplete_details": details,
            "output": [],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
            },
        },
    }

    message = _responses_terminal_message(
        list(decode_responses_stream([f"data: {json.dumps(payload)}"], model))
    )

    assert message.stop_reason == expected_stop
    assert message.error_message == expected_error
