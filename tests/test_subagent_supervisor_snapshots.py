from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from travis.coding_agent.subagents import CallableSubagentBackend, SubagentSupervisor, SubagentTask


def test_snapshot_subscription_reports_exact_lifecycle_without_goal_leak(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def backend(_task):
        entered.set()
        release.wait(2)
        return "secret goal must not appear; " + "x" * 300

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", backend))
    seen = []
    unsubscribe = supervisor.subscribe(seen.append)
    task = SubagentTask(role="reviewer", goal="TOP SECRET GOAL", cwd=str(tmp_path))

    task_id = supervisor.spawn(task)
    assert entered.wait(1)
    release.set()
    result = supervisor.wait(task_id, 2)

    assert result.status == "completed"
    assert [snapshot.revision for snapshot in seen] == [0, 1, 2, 3]
    assert [snapshot.tasks[0].status for snapshot in seen[1:]] == [
        "queued", "running", "completed"
    ]
    final = supervisor.snapshot()
    assert final.active_count == 0
    assert final.capacity == 1
    assert final.tasks[0].ended_at_ms > 0
    assert len(final.tasks[0].summary_preview) <= 160
    assert "TOP SECRET GOAL" not in repr(final)
    assert supervisor.get_result(task_id) is result
    with pytest.raises(FrozenInstanceError):
        final.revision = 9  # type: ignore[misc]
    unsubscribe()


def test_late_subscriber_gets_initial_snapshot_and_unsubscribe_stops_updates(tmp_path) -> None:
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda _task: "done"))
    first_id = supervisor.spawn(SubagentTask(role="one", goal="one", cwd=str(tmp_path)))
    supervisor.wait(first_id, 2)
    seen = []

    unsubscribe = supervisor.subscribe(seen.append)
    assert seen == [supervisor.snapshot()]
    unsubscribe()
    second_id = supervisor.spawn(SubagentTask(role="two", goal="two", cwd=str(tmp_path)))
    supervisor.wait(second_id, 2)

    assert len(seen) == 1


def test_subscriber_failure_isolated_and_callbacks_can_read_supervisor(tmp_path) -> None:
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda _task: "done"))
    reads = []

    def observer(snapshot):
        reads.append((snapshot.revision, supervisor.snapshot().revision))

    supervisor.subscribe(lambda _snapshot: (_ for _ in ()).throw(RuntimeError("boom")))
    supervisor.subscribe(observer)
    task_id = supervisor.spawn(SubagentTask(role="one", goal="one", cwd=str(tmp_path)))
    result = supervisor.wait(task_id, 2)

    assert result.status == "completed"
    assert reads
    assert reads[-1][0] == reads[-1][1]


def test_concurrent_completions_have_monotonic_revisions_and_result_visibility(tmp_path) -> None:
    barrier = threading.Barrier(2)
    supervisor = SubagentSupervisor(max_threads=2)
    supervisor.register_backend(
        CallableSubagentBackend("internal", lambda _task: (barrier.wait(2), "done")[1])
    )
    terminal_visibility = []

    def observer(snapshot):
        for item in snapshot.tasks:
            if item.status == "completed":
                terminal_visibility.append(supervisor.get_result(item.task_id) is not None)

    supervisor.subscribe(observer)
    ids = [
        supervisor.spawn(SubagentTask(role=f"r{index}", goal="work", cwd=str(tmp_path)))
        for index in range(2)
    ]
    supervisor.wait_all(ids, timeout=2)

    assert terminal_visibility and all(terminal_visibility)
    assert supervisor.snapshot().revision == 6
    assert supervisor.snapshot().active_count == 0


def test_shutdown_publishes_terminal_revision(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(
        CallableSubagentBackend(
            "internal", lambda _task: (entered.set(), release.wait(2), "done")[2]
        )
    )
    seen = []
    supervisor.subscribe(seen.append)
    supervisor.spawn(SubagentTask(role="slow", goal="slow", cwd=str(tmp_path)))
    assert entered.wait(1)

    supervisor.shutdown(wait=False)
    release.set()

    assert seen[-1].revision > seen[-2].revision
    assert seen[-1].active_count == 0
    assert seen[-1].tasks[0].controllable is False
