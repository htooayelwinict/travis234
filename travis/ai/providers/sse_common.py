"""Shared server-sent-event decoding helpers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator

from travis.ai.types import AssistantMessage, StartEvent

class _StartEventState:
    def __init__(self, message: AssistantMessage) -> None:
        self.message = message
        self.started = False

    def ensure(self) -> StartEvent | None:
        if self.started:
            return None
        self.started = True
        return StartEvent(partial=self.message)


def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    if reason is None:
        return "stop", None
    if reason in ("stop", "end"):
        return "stop", None
    if reason == "length":
        return "length", None
    if reason in ("function_call", "tool_calls"):
        return "toolUse", None
    if reason in ("content_filter", "network_error"):
        return "error", f"Provider finish_reason: {reason}"
    return "error", f"Provider finish_reason: {reason}"

def _iter_sse_data(
    lines: Iterable[str],
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[str]:
    last_data_at = clock()
    for raw in lines:
        if (
            data_idle_timeout_seconds is not None
            and data_idle_timeout_seconds > 0
            and clock() - last_data_at > data_idle_timeout_seconds
        ):
            seconds = int(data_idle_timeout_seconds)
            raise TimeoutError(f"SSE stream received no data events for {seconds} seconds")
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        last_data_at = clock()
        yield payload
