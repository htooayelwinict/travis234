"""Bounded execution for tool bodies owned by one asyncio coordinator."""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Iterable, TypeVar

from appv231.agent.async_utils import resolve

T = TypeVar("T")
R = TypeVar("R")


class ToolCoordinator:
    """Runs tool bodies concurrently while keeping policy and events on the owner loop."""

    def __init__(self, max_parallel_tools: int = 8) -> None:
        self.max_parallel_tools = max(1, int(max_parallel_tools))
        self._semaphore = asyncio.Semaphore(self.max_parallel_tools)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_parallel_tools,
            thread_name_prefix="appv231-tool",
        )

    async def execute(self, function: Callable[..., R], *args: Any) -> R:
        async with self._semaphore:
            if inspect.iscoroutinefunction(function):
                return await function(*args)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, partial(function, *args))
            return await resolve(result)

    async def execute_batch(
        self,
        items: Iterable[T],
        worker: Callable[[T], Any],
    ) -> list[Any]:
        return list(await asyncio.gather(*(worker(item) for item in items)))

    async def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def __aenter__(self) -> "ToolCoordinator":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()


__all__ = ["ToolCoordinator"]
