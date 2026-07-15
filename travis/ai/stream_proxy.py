"""Ordered transformation proxy for Travis event streams."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from travis.ai.event_stream import EventStream

T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R")


class ProxyEventStream(EventStream[U, R], Generic[T, U, R]):
    def __init__(self, source: EventStream[T, R]) -> None:
        super().__init__()
        self._source = source
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def close(self) -> None:
        self._cancelled.set()
        self._source.close()
        super().close()


def stream_proxy(
    source: EventStream[T, R],
    *,
    transform: Callable[[T], U | None | Any] | None = None,
    on_event: Callable[[U], object] | None = None,
) -> ProxyEventStream[T, U, R]:
    """Forward one source stream with optional ordered transformation hooks."""

    proxy: ProxyEventStream[T, U, R] = ProxyEventStream(source)

    def forward() -> None:
        try:
            for event in source.iter_until(lambda: proxy.cancelled):
                transformed = _settle(transform(event)) if transform is not None else event
                if transformed is None:
                    continue
                if on_event is not None:
                    _settle(on_event(transformed))
                proxy.push(transformed)
            if not proxy.cancelled:
                proxy.end(source.result_sync())
        except BaseException as error:  # noqa: BLE001 - preserve source/hook failures exactly.
            proxy.fail(error)
        finally:
            source.close()

    threading.Thread(
        target=forward,
        name="travis-stream-proxy",
        daemon=True,
    ).start()
    return proxy


def _settle(value: Any) -> Any:
    return asyncio.run(value) if inspect.isawaitable(value) else value


__all__ = ["ProxyEventStream", "stream_proxy"]
