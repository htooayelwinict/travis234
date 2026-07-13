"""Generation-cancelled background model catalog loader."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class ModelCatalogLoader:
    def __init__(
        self,
        *,
        discover: Callable[[str | None], list[T]],
        post: Callable[[Callable[[], None]], None],
    ) -> None:
        self._discover = discover
        self._post = post
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="travis-model-catalog")
        self._lock = threading.RLock()
        self._generation = 0
        self._current: Future[list[T]] | None = None
        self._closed = False

    def load(
        self,
        query: str | None,
        on_complete: Callable[[list[T], BaseException | None], None] | None = None,
    ) -> Future[list[T]]:
        with self._lock:
            if self._closed:
                future: Future[list[T]] = Future()
                future.set_exception(RuntimeError("model catalog loader is closed"))
                return future
            self._generation += 1
            generation = self._generation
            previous = self._current
            if previous is not None:
                previous.cancel()
            future = self._executor.submit(self._discover, query)
            self._current = future

        if on_complete is not None:
            future.add_done_callback(
                lambda completed: self._deliver(generation, completed, on_complete)
            )
        return future

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            current = self._current
            self._current = None
        if current is not None:
            current.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _deliver(
        self,
        generation: int,
        future: Future[list[T]],
        on_complete: Callable[[list[T], BaseException | None], None],
    ) -> None:
        if future.cancelled():
            return
        try:
            models = future.result()
            error: BaseException | None = None
        except BaseException as caught:  # noqa: BLE001 - delivered to UI as a bounded status.
            models = []
            error = caught

        def deliver() -> None:
            with self._lock:
                if self._closed or generation != self._generation:
                    return
            on_complete(models, error)

        self._post(deliver)


__all__ = ["ModelCatalogLoader"]
