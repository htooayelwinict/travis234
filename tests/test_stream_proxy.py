from __future__ import annotations

import asyncio

import pytest

from travis.ai.event_stream import EventStream
from travis.ai.stream_proxy import stream_proxy


def test_stream_proxy_preserves_order_and_supports_replace_and_suppress() -> None:
    source: EventStream[int, str] = EventStream()
    seen: list[int] = []
    proxy = stream_proxy(
        source,
        transform=lambda value: None if value == 2 else value * 10,
        on_event=seen.append,
    )

    source.push(1)
    source.push(2)
    source.push(3)
    source.end("complete")

    assert list(proxy) == [10, 30]
    assert proxy.result_sync() == "complete"
    assert seen == [10, 30]


def test_stream_proxy_awaits_async_transform_and_callback() -> None:
    source: EventStream[int, int] = EventStream()
    seen: list[int] = []

    async def transform(value: int) -> int:
        await asyncio.sleep(0)
        return value + 1

    async def on_event(value: int) -> None:
        await asyncio.sleep(0)
        seen.append(value)

    proxy = stream_proxy(source, transform=transform, on_event=on_event)
    source.push(4)
    source.end(5)

    assert list(proxy) == [5]
    assert proxy.result_sync() == 5
    assert seen == [5]


def test_stream_proxy_propagates_source_error_unchanged() -> None:
    source: EventStream[int, None] = EventStream()
    proxy = stream_proxy(source)
    error = RuntimeError("source failed")

    source.fail(error)
    assert list(proxy) == []
    with pytest.raises(RuntimeError) as raised:
        proxy.result_sync()
    assert raised.value is error


def test_stream_proxy_cancellation_and_callback_failure_close_source() -> None:
    class TrackingStream(EventStream[int, None]):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    cancelled_source = TrackingStream()
    cancelled = stream_proxy(cancelled_source)
    cancelled.close()
    assert cancelled_source.closed is True
    assert cancelled.result_sync() is None

    failed_source = TrackingStream()

    def fail_callback(_event: int) -> None:
        raise ValueError("callback failed")

    failed = stream_proxy(failed_source, on_event=fail_callback)
    failed_source.push(1)
    failed_source.end()
    assert list(failed) == []
    with pytest.raises(ValueError, match="callback failed"):
        failed.result_sync()
    assert failed_source.closed is True
