from __future__ import annotations

import threading
from types import SimpleNamespace

from travis.coding_agent.subagent_result_types import SubagentResult
from travis.coding_agent.subagent_supervision import (
    ControlResult,
    SubagentSnapshot,
    SupervisorSnapshot,
)
from travis.tui.components.subagent_roster import SubagentRoster
from travis.tui.interactive_command_dispatcher import _parse_agents_command
from travis.tui.interactive_subagents import InteractiveSubagents


def _snapshot(revision: int, *tasks: SubagentSnapshot) -> SupervisorSnapshot:
    return SupervisorSnapshot(
        revision=revision,
        active_count=sum(item.status in {"queued", "running"} for item in tasks),
        capacity=3,
        tasks=tuple(tasks),
    )


def _task(task_id="subagent-one", status="running", summary="") -> SubagentSnapshot:
    return SubagentSnapshot(
        task_id=task_id,
        role="reviewer",
        backend="internal",
        status=status,
        started_at_ms=1000,
        ended_at_ms=2000 if status == "completed" else 0,
        summary_preview=summary,
        controllable=status == "running",
    )


def test_roster_renders_zero_one_three_tasks_without_private_data() -> None:
    assert SubagentRoster(_snapshot(0)).render(80) == [
        "Agents: 0/3 active",
        "  No subagents have been spawned in this session.",
    ]
    roster = SubagentRoster(
        _snapshot(
            3,
            _task(summary="safe preview"),
            _task("subagent-two", "completed", "/private/path must remain only input"),
            _task("subagent-three", "failed", "failed"),
        ),
        clock_ms=lambda: 4000,
    )
    rendered = "\n".join(roster.render(52))

    assert "reviewer" in rendered
    assert "running" in rendered
    assert "steer,cancel" in rendered
    assert all(len(line) <= 52 for line in rendered.splitlines())
    assert "goal" not in rendered.lower()
    assert "context" not in rendered.lower()
    assert "toolTrace" not in rendered


def test_agents_command_parser_is_separate_from_subagents_skill() -> None:
    assert _parse_agents_command("/agents") == ("status", ())
    assert _parse_agents_command("/agents status") == ("status", ())
    assert _parse_agents_command("/agents inspect abc") == ("inspect", ("abc",))
    assert _parse_agents_command("/agents steer abc focus now") == (
        "steer", ("abc", "focus now")
    )
    assert _parse_agents_command("/agents cancel abc") == ("cancel", ("abc",))
    assert _parse_agents_command("/subagents inspect") is None


class _Supervisor:
    def __init__(self) -> None:
        self.current = _snapshot(0)
        self.callback = None
        self.unsubscribed = False
        self.results = {}

    def snapshot(self):
        return self.current

    def subscribe(self, callback):
        self.callback = callback
        callback(self.current)
        return lambda: setattr(self, "unsubscribed", True)

    def steer(self, task_id, message):
        return ControlResult(task_id == "subagent-one", "steering_queued" if task_id == "subagent-one" else "unknown_task")

    def cancel(self, task_id, reason):
        del reason
        if task_id != "subagent-one":
            raise KeyError(task_id)
        return SubagentResult(task_id, "internal", "reviewer", "cancelled", "cancelled")

    def get_result(self, task_id):
        return self.results.get(task_id)


class _Interactive(InteractiveSubagents):
    def __init__(self, supervisor) -> None:
        self.app = SimpleNamespace(session=SimpleNamespace(subagents=supervisor))
        self.history = SimpleNamespace(items=[], add=lambda item: self.history.items.append(item))
        self.status = SimpleNamespace(set_message=lambda value: setattr(self.status, "message", value))
        self.posted = []
        self.tui = SimpleNamespace(
            dispatcher=SimpleNamespace(post=self.posted.append),
            request_render=lambda **_kwargs: None,
        )
        self._unsubscribe_subagents = None
        self._subagent_snapshot = _snapshot(0)
        self.confirm = True

    def _refresh_footer(self):
        pass

    def prompt_extension_confirm(self, *_args):
        return self.confirm


def test_worker_snapshot_is_marshaled_coalesced_and_unsubscribed() -> None:
    supervisor = _Supervisor()
    interactive = _Interactive(supervisor)
    interactive._bind_subagent_supervisor()
    supervisor.current = _snapshot(2, _task())

    thread = threading.Thread(target=lambda: supervisor.callback(supervisor.current))
    thread.start()
    thread.join()

    assert interactive._subagent_snapshot.revision == 0
    interactive.posted.pop(0)()
    interactive.posted.pop(0)()
    assert interactive._subagent_snapshot.revision == 2
    interactive._apply_subagent_snapshot(_snapshot(1))
    assert interactive._subagent_snapshot.revision == 2
    interactive._shutdown_subagent_ui()
    assert supervisor.unsubscribed is True


def test_agents_commands_render_control_results_and_confirmation() -> None:
    supervisor = _Supervisor()
    supervisor.current = _snapshot(1, _task())
    interactive = _Interactive(supervisor)
    interactive._subagent_snapshot = supervisor.current

    interactive._run_agents_command(("status", ()))
    interactive._run_agents_command(("steer", ("subagent-one", "focus")))
    interactive.confirm = False
    interactive._run_agents_command(("cancel", ("subagent-one",)))

    rendered = "\n".join(
        "\n".join(item.render(100)) for item in interactive.history.items
    )
    assert "Agents: 1/3 active" in rendered
    assert "steering_queued" in rendered
    assert "cancel skipped" in rendered.lower()
