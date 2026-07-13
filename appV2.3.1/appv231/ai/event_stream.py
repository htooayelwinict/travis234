"""Push/async-iterable event stream. Port of pi/packages/ai/src/utils/event-stream.ts."""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import AsyncIterator, Callable, Generic, Iterator, TypeVar

from appv231.ai.types import AssistantMessage, AssistantMessageEvent

T = TypeVar("T")
R = TypeVar("R")

_SENTINEL = object()


class EventStream(Generic[T, R]):
    """A queue-backed stream of events that resolves to a single result."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._done = threading.Event()
        self._result: R | None = None
        self._error: BaseException | None = None

    def push(self, event: T) -> None:
        self._queue.put(event)

    def end(self, result: R | None = None) -> None:
        if self._done.is_set():
            return
        self._result = result
        self._done.set()
        self._queue.put(_SENTINEL)

    def fail(self, error: BaseException) -> None:
        if self._done.is_set():
            return
        self._error = error
        self._done.set()
        self._queue.put(_SENTINEL)

    def __iter__(self) -> Iterator[T]:
        yield from self.iter_until()

    def iter_until(
        self,
        should_stop: Callable[[], bool] | None = None,
        *,
        poll_interval_seconds: float = 0.05,
    ) -> Iterator[T]:
        while True:
            if should_stop is not None and should_stop():
                return
            if should_stop is None:
                item = self._queue.get()
            else:
                try:
                    item = self._queue.get(timeout=poll_interval_seconds)
                except queue.Empty:
                    continue
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    def close(self) -> None:
        self.end(self._result)

    async def __aiter__(self) -> AsyncIterator[T]:
        while True:
            item = await asyncio.to_thread(self._queue.get)
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    def result_sync(self) -> R:
        self._done.wait()
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]

    async def result(self) -> R:
        await asyncio.to_thread(self._done.wait)
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]


class AssistantMessageEventStream(EventStream[AssistantMessageEvent, AssistantMessage]):
    """Completes on a `done` or `error` event with the final AssistantMessage."""

    def push(self, event: AssistantMessageEvent) -> None:
        super().push(event)
        if event.type == "done":
            self.end(event.message)
        elif event.type == "error":
            self.end(event.error)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    return AssistantMessageEventStream()
