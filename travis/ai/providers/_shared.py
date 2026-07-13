"""Small provider-construction helpers shared by provider adapters."""

from __future__ import annotations

from travis.ai.types import AssistantMessage, Model, empty_usage, now_ms


def blank_assistant_message(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
