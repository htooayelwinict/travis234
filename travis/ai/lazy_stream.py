"""Synchronous stream facade for provider setup performed in a worker."""

from __future__ import annotations

import threading
from collections.abc import Callable

from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.types import AssistantMessage, ErrorEvent, Model, empty_usage, now_ms


def lazy_stream(
    model: Model,
    setup: Callable[[], AssistantMessageEventStream],
) -> AssistantMessageEventStream:
    outer = create_assistant_message_event_stream()

    def forward() -> None:
        try:
            inner = setup()
            for event in inner:
                outer.push(event)
        except Exception as error:  # noqa: BLE001 - request setup failures are protocol events.
            _push_error(outer, model, error)

    threading.Thread(target=forward, daemon=True).start()
    return outer


def _push_error(
    stream: AssistantMessageEventStream,
    model: Model,
    error: BaseException,
) -> None:
    message = AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="error",
        error_message=str(error),
        timestamp=now_ms(),
    )
    stream.push(ErrorEvent(reason="error", error=message))


__all__ = ["lazy_stream"]
