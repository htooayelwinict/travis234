"""Helpers for one canonical awaitable agent runtime."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")
MaybeAwaitable = T | Awaitable[T]


async def resolve(value: MaybeAwaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


def run_sync(coroutine: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise RuntimeError("Use the async travis API from an active event loop")


__all__ = ["MaybeAwaitable", "resolve", "run_sync"]
