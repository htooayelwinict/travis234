from __future__ import annotations

import asyncio
import threading
import time

import pytest

from travis.agent import ToolCoordinator, run_sync


def test_tool_coordinator_bounds_sync_tool_bodies() -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()

    def tool_body(value: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return value

    async def exercise() -> list[int]:
        async with ToolCoordinator(max_parallel_tools=2) as coordinator:
            return await asyncio.gather(
                *(coordinator.execute(tool_body, value) for value in range(8))
            )

    assert asyncio.run(exercise()) == list(range(8))
    assert maximum == 2


def test_run_sync_rejects_an_active_event_loop() -> None:
    async def value() -> int:
        return 1

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="Use the async travis API"):
            run_sync(value())

    asyncio.run(exercise())
