"""Serialized, bounded delivery for tool progress snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar


T = TypeVar("T")
_SENTINEL = object()


class ToolUpdateRelay(Generic[T]):
    """Delivers updates on the owner loop without creating one task per update."""

    def __init__(
        self,
        emit: Callable[[T], Awaitable[None]],
        *,
        max_pending: int = 64,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._emit = emit
        self._queue: asyncio.Queue[T | object] = asyncio.Queue(maxsize=max(1, max_pending))
        self._accepting = True
        self._latest_overflow: T | None = None
        self._error: BaseException | None = None
        self._consumer = self._loop.create_task(self._consume())

    def publish(self, update: T) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._offer(update)
            return
        asyncio.run_coroutine_threadsafe(self._publish_with_backpressure(update), self._loop).result()

    def _offer(self, update: T) -> None:
        if not self._accepting:
            return
        try:
            self._queue.put_nowait(update)
        except asyncio.QueueFull:
            self._latest_overflow = update

    async def _publish_with_backpressure(self, update: T) -> None:
        if not self._accepting:
            return
        await self._queue.put(update)

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                if self._error is None:
                    await self._emit(item)  # type: ignore[arg-type]
            except BaseException as error:  # noqa: BLE001 - replay after draining accepted updates.
                if self._error is None:
                    self._error = error
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        if not self._accepting:
            await self._consumer
            if self._error is not None:
                raise self._error
            return
        self._accepting = False
        await self._queue.join()
        latest = self._latest_overflow
        self._latest_overflow = None
        if latest is not None and self._error is None:
            try:
                await self._emit(latest)
            except BaseException as error:  # noqa: BLE001 - preserve critical sink failure.
                self._error = error
        self._queue.put_nowait(_SENTINEL)
        await self._consumer
        if self._error is not None:
            raise self._error


__all__ = ["ToolUpdateRelay"]
