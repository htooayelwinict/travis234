"""Pi-style extension/resource event bus."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any


EventHandler = Callable[[Any], object]


class EventBusController:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def emit(self, channel: str, data: Any) -> None:
        for handler in list(self._handlers.get(channel, [])):
            try:
                result = handler(data)
                if inspect.isawaitable(result):
                    _settle_awaitable(result)
            except Exception as error:  # noqa: BLE001 - Pi isolates event handler failures.
                print(f"Event handler error ({channel}): {error}")

    def on(self, channel: str, handler: EventHandler) -> Callable[[], None]:
        self._handlers[channel].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(channel)
            if not handlers:
                return
            try:
                handlers.remove(handler)
            except ValueError:
                return

        return unsubscribe

    def clear(self) -> None:
        self._handlers.clear()


EventBus = EventBusController


def create_event_bus() -> EventBusController:
    return EventBusController()


createEventBus = create_event_bus


def _settle_awaitable(awaitable: object) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(awaitable)  # type: ignore[arg-type]
        return
    loop.create_task(awaitable)  # type: ignore[arg-type]
