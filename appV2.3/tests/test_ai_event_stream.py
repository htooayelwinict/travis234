from __future__ import annotations

import asyncio

from appv23.ai.event_stream import (
    AssistantMessageEventStream,
    create_assistant_message_event_stream,
)
from appv23.ai.types import (
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    empty_usage,
    now_ms,
)


def _msg(stop_reason: str = "stop", error_message: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason=stop_reason,
        error_message=error_message,
        timestamp=now_ms(),
    )


def test_iterates_events_until_done_and_result_sync() -> None:
    stream = create_assistant_message_event_stream()
    final = _msg()
    stream.push(StartEvent(partial=final))
    stream.push(TextDeltaEvent(content_index=0, delta="hi", partial=final))
    stream.push(DoneEvent(reason="stop", message=final))

    events = list(stream)
    assert [e.type for e in events] == ["start", "text_delta", "done"]
    assert stream.result_sync() is final


def test_error_event_resolves_result_without_raising() -> None:
    stream = create_assistant_message_event_stream()
    err = _msg(stop_reason="error", error_message="boom")
    stream.push(ErrorEvent(reason="error", error=err))

    events = list(stream)
    assert [e.type for e in events] == ["error"]
    result = stream.result_sync()
    assert result.error_message == "boom"


def test_async_iteration_and_await_result() -> None:
    stream = create_assistant_message_event_stream()
    final = _msg()
    stream.push(StartEvent(partial=final))
    stream.push(DoneEvent(reason="stop", message=final))

    async def drive() -> list[str]:
        types = [e.type async for e in stream]
        assert (await stream.result()) is final
        return types

    assert asyncio.run(drive()) == ["start", "done"]


def test_is_assistant_message_event_stream() -> None:
    assert isinstance(create_assistant_message_event_stream(), AssistantMessageEventStream)
