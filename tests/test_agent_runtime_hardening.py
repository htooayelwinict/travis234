from __future__ import annotations

import asyncio
import threading
import time

import pytest

from travis.agent import ToolCoordinator, run_sync
from travis.agent.agent import PendingMessageQueue
from travis.ai.types import UserMessage, now_ms


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


def test_drain_all_is_atomic_with_concurrent_enqueue() -> None:
    snapshot_started = threading.Event()
    release_snapshot = threading.Event()
    enqueue_finished = threading.Event()
    existing = UserMessage(content="existing", timestamp=now_ms())
    concurrent = UserMessage(content="concurrent", timestamp=now_ms())

    class BlockingSnapshotList(list):
        def __iter__(self):
            snapshot_started.set()
            assert release_snapshot.wait(timeout=2)
            return super().__iter__()

    queue = PendingMessageQueue(mode="all")
    queue.messages = BlockingSnapshotList([existing])
    drained: list[object] = []

    drain_thread = threading.Thread(target=lambda: drained.extend(queue.drain()))

    def enqueue() -> None:
        queue.enqueue(concurrent)
        enqueue_finished.set()

    enqueue_thread = threading.Thread(target=enqueue)
    drain_thread.start()
    assert snapshot_started.wait(timeout=2)
    enqueue_thread.start()
    enqueue_finished.wait(timeout=0.05)
    release_snapshot.set()
    drain_thread.join(timeout=2)
    enqueue_thread.join(timeout=2)

    assert drain_thread.is_alive() is False
    assert enqueue_thread.is_alive() is False
    assert drained == [existing]
    assert queue.drain() == [concurrent]
