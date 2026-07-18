"""extension/resource event bus."""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any


EventHandler = Callable[[Any], object]


class EventBusController:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[EventHandler, object | None]]] = defaultdict(list)
        self._active_owners: list[object] = []

    def emit(self, channel: str, data: Any) -> None:
        for handler, _owner in list(self._handlers.get(channel, [])):
            try:
                result = handler(data)
                if inspect.isawaitable(result):
                    _settle_awaitable(result, channel)
            except Exception as error:  # noqa: BLE001 - Travis isolates event handler failures.
                print(f"Event handler error ({channel}): {error}", file=sys.stderr)

    def on(self, channel: str, handler: EventHandler) -> Callable[[], None]:
        owner = self._active_owners[-1] if self._active_owners else None
        registered = (handler, owner)
        self._handlers[channel].append(registered)

        def unsubscribe() -> None:
            handlers = self._handlers.get(channel)
            if not handlers:
                return
            try:
                handlers.remove(registered)
            except ValueError:
                return

        return unsubscribe

    @contextmanager
    def owner(self, owner: object):
        self._active_owners.append(owner)
        try:
            yield self
        finally:
            self._active_owners.pop()

    def clear_owner(self, owner: object) -> None:
        for channel in tuple(self._handlers):
            remaining = [item for item in self._handlers[channel] if item[1] is not owner]
            if remaining:
                self._handlers[channel] = remaining
            else:
                self._handlers.pop(channel, None)

    def clear(self) -> None:
        self._handlers.clear()


EventBus = EventBusController


def create_event_bus() -> EventBusController:
    return EventBusController()




def _settle_awaitable(awaitable: object, channel: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(awaitable)  # type: ignore[arg-type]
        return
    task = loop.create_task(awaitable)  # type: ignore[arg-type]

    def observe(completed: asyncio.Task[object]) -> None:
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception as error:  # noqa: BLE001 - async bus failures stay isolated and observable.
            print(f"Event handler error ({channel}): {error}", file=sys.stderr)

    task.add_done_callback(observe)
