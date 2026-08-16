from __future__ import annotations

import threading

import pytest

from travis.coding_agent.subagent_supervision import ControlResult
from travis.coding_agent.subagents import CallableSubagentBackend, SubagentSupervisor, SubagentTask


class RecordingHandle:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.reasons: list[str] = []

    def steer(self, message: str) -> ControlResult:
        self.messages.append(message)
        return ControlResult(True, "steering_queued")

    def cancel(self, reason: str) -> ControlResult:
        self.reasons.append(reason)
        return ControlResult(True, "cancellation_requested")


def test_queued_internal_steering_drains_when_handle_attaches(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(
        CallableSubagentBackend(
            "internal", lambda _task: (entered.set(), release.wait(2), "done")[2]
        )
    )
    task = SubagentTask(id="subagent-fixed", role="worker", goal="work", cwd=str(tmp_path))
    task_id = supervisor.spawn(task)
    assert entered.wait(1)

    queued = supervisor.steer(task_id, "focus on tests")
    handle = RecordingHandle()
    supervisor.attach_control_handle(task_id, handle)
    release.set()
    supervisor.wait(task_id, 2)

    assert queued == ControlResult(True, "steering_queued")
    assert handle.messages == ["focus on tests"]


def test_external_and_settled_tasks_return_shaped_control_codes(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(
        CallableSubagentBackend(
            "codex", lambda _task: (entered.set(), release.wait(2), "done")[2]
        )
    )
    task_id = supervisor.spawn(
        SubagentTask(role="worker", goal="work", cwd=str(tmp_path), backend="codex")
    )
    assert entered.wait(1)
    assert supervisor.steer(task_id, "focus") == ControlResult(False, "steering_unsupported")
    release.set()
    supervisor.wait(task_id, 2)
    assert supervisor.steer(task_id, "later") == ControlResult(False, "task_settled")
    assert supervisor.steer("subagent-missing", "later") == ControlResult(False, "unknown_task")


@pytest.mark.parametrize("message", ["", "   ", "x" * 8193])
def test_steering_text_is_bounded(message: str, tmp_path) -> None:
    supervisor = SubagentSupervisor(max_threads=1)
    with pytest.raises(ValueError, match="steering message"):
        supervisor.steer("subagent-missing", message)


def test_cancel_invokes_handle_once_and_steer_cancel_race_settles(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(
        CallableSubagentBackend(
            "internal", lambda _task: (entered.set(), release.wait(2), "done")[2]
        )
    )
    task_id = supervisor.spawn(SubagentTask(role="worker", goal="work", cwd=str(tmp_path)))
    assert entered.wait(1)
    handle = RecordingHandle()
    supervisor.attach_control_handle(task_id, handle)

    result = supervisor.cancel(task_id, "stop now")
    again = supervisor.cancel(task_id, "again")
    after = supervisor.steer(task_id, "too late")
    release.set()

    assert result is again
    assert result.status == "cancelled"
    assert handle.reasons == ["stop now"]
    assert after == ControlResult(False, "task_settled")


def test_handle_exceptions_are_shaped_not_raised(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(
        CallableSubagentBackend(
            "internal", lambda _task: (entered.set(), release.wait(2), "done")[2]
        )
    )
    task_id = supervisor.spawn(SubagentTask(role="worker", goal="work", cwd=str(tmp_path)))
    assert entered.wait(1)

    class Broken:
        def steer(self, _message):
            raise RuntimeError("secret detail")

        def cancel(self, _reason):
            raise RuntimeError("secret detail")

    supervisor.attach_control_handle(task_id, Broken())
    result = supervisor.steer(task_id, "focus")
    release.set()
    supervisor.wait(task_id, 2)

    assert result == ControlResult(False, "control_failed")
