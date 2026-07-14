"""Small provider-construction helpers shared by provider adapters."""

from __future__ import annotations

from collections.abc import Mapping
import inspect

from travis.agent.async_utils import resolve, run_sync
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


def signal_aborted(signal: object) -> bool:
    if isinstance(signal, Mapping):
        return bool(signal.get("aborted"))
    return bool(getattr(signal, "aborted", False))


def settle_callback(result: object) -> object:
    if inspect.isawaitable(result):
        return run_sync(resolve(result))
    return result
