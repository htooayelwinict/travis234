from __future__ import annotations

import json
from pathlib import Path
import shlex
import sqlite3

from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from travis.ai.types import TextContent, ToolResultMessage
from travis.app import CodingApp
from travis.tui.terminal import FakeTerminal
from tests._provider_runtime import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def last_tool_text(context) -> str:
    for message in reversed(context.messages):
        if isinstance(message, ToolResultMessage):
            return "\n".join(
                block.text for block in message.content if isinstance(block, TextContent)
            )
    raise AssertionError("expected a tool result")


def test_tui_unrelated_turn_preserves_orchestration_lazy_discovery(tmp_path: Path) -> None:
    captured_prompts: list[str] = []

    def provider(model, context):
        captured_prompts.append(context.system_prompt or "")
        return text_response_events(model, "Ordinary TUI reply")

    register_api_provider(create_faux_provider(provider))
    agent_dir = tmp_path / "agent"
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        app.run_turn("Reply normally without loading any skill")

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "<name>orchestration</name>" in prompt
        assert "Coordinate another durable Travis234 session" not in prompt
        assert "# Local Tmux Orchestration" not in prompt
        assert "Run the private relay" not in prompt
        assert not (agent_dir / "orchestration").exists()
        assert not (tmp_path / ".worktrees").exists()
        assert "Ordinary TUI reply" in "\n".join(app.tui.render(100))
    finally:
        app.close()


def test_tui_durable_run_task_receipts_survive_coordinator_restart(tmp_path: Path) -> None:
    helper = (
        Path(__file__).parents[1]
        / "travis/resources/skills/orchestration/scripts/orchestrate.py"
    )
    skill = helper.parents[1] / "SKILL.md"
    agent_dir = tmp_path / "agent"
    run_request = tmp_path / "run.json"
    run_request.write_text(
        json.dumps(
            {
                "objective": "Research parser ownership",
                "coordinatorSessionId": "tui-coordinator-a",
            }
        ),
        encoding="utf-8",
    )
    run_request.chmod(0o600)
    task_request = tmp_path / "task.json"
    task_request.write_text(
        json.dumps(
            {
                "objective": "Identify the parser owner without editing",
                "ownership": {
                    "ownedPaths": [],
                    "forbiddenPaths": ["*"],
                    "responsibility": "read-only research",
                },
                "acceptanceCriteria": ["Name the owning module", "Cite one file"],
                "mode": "supervised",
                "maxRounds": 4,
                "commitPolicy": "no_commit",
            }
        ),
        encoding="utf-8",
    )
    task_request.chmod(0o600)
    state: dict[str, str] = {}
    initial_prompts: list[str] = []
    provider_calls = {"count": 0}

    def first_provider(model, context):
        provider_calls["count"] += 1
        initial_prompts.append(context.system_prompt or "")
        if provider_calls["count"] == 1:
            return tool_call_response_events(
                model,
                "read",
                {"path": str(skill)},
                call_id="load_orchestration",
            )
        if provider_calls["count"] == 2:
            assert "# Local Tmux Orchestration" in last_tool_text(context)
            command = " ".join(
                [
                    "python3",
                    shlex.quote(str(helper)),
                    "run-create",
                    "--request-file",
                    shlex.quote(str(run_request)),
                    "--consume-request-file",
                    "--idempotency-key",
                    "tui-durable-run",
                ]
            )
            return tool_call_response_events(
                model,
                "bash",
                {"command": command},
                call_id="create_run",
            )
        if provider_calls["count"] == 3:
            receipt = json.loads(last_tool_text(context))
            state["runId"] = receipt["result"]["run"]["runId"]
            assert receipt["nextActions"]
            command = " ".join(
                [
                    "python3",
                    shlex.quote(str(helper)),
                    "task-create",
                    "--run-id",
                    state["runId"],
                    "--request-file",
                    shlex.quote(str(task_request)),
                    "--consume-request-file",
                    "--idempotency-key",
                    "tui-durable-task",
                ]
            )
            return tool_call_response_events(
                model,
                "bash",
                {"command": command},
                call_id="create_task",
            )
        receipt = json.loads(last_tool_text(context))
        task = receipt["result"]["task"]
        state["taskId"] = task["taskId"]
        assert task["mode"] == "supervised"
        assert task["ownership"]["responsibility"] == "read-only research"
        assert task["acceptanceCriteria"] == ["Name the owning module", "Cite one file"]
        assert task["maxRounds"] == 4
        assert receipt["nextActions"]
        return text_response_events(
            model,
            f"Prepared Run {state['runId']} and Task {state['taskId']}; stopped before worker start.",
        )

    register_api_provider(create_faux_provider(first_provider))
    first_app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=35),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        first_app.run_turn(
            "Prepare a supervised orchestration Run and Task, but stop before a worktree or worker."
        )
        visible_receipts = "\n".join(
            block.text
            for message in first_app.messages
            if isinstance(message, ToolResultMessage)
            for block in message.content
            if isinstance(block, TextContent)
        )
        assert state["runId"] in visible_receipts
        assert state["taskId"] in visible_receipts
        assert "supervised" in visible_receipts
        assert "read-only research" in visible_receipts
        assert "Name the owning module" in visible_receipts
        assert "maxRounds" in visible_receipts
    finally:
        first_app.close()

    assert "<name>orchestration</name>" in initial_prompts[0]
    assert "# Local Tmux Orchestration" not in initial_prompts[0]
    assert not run_request.exists()
    assert not task_request.exists()
    assert not (tmp_path / ".worktrees").exists()
    assert list((agent_dir / "orchestration" / "sockets").iterdir()) == []

    reset_api_providers()
    restart_calls = {"count": 0}
    restarted_receipts: list[dict[str, object]] = []

    def restarted_provider(model, context):
        restart_calls["count"] += 1
        if restart_calls["count"] == 1:
            command = " ".join(
                [
                    "python3",
                    shlex.quote(str(helper)),
                    "run-show",
                    "--run-id",
                    state["runId"],
                ]
            )
            return tool_call_response_events(model, "bash", {"command": command}, call_id="show_run")
        if restart_calls["count"] == 2:
            restarted_receipts.append(json.loads(last_tool_text(context)))
            command = " ".join(
                [
                    "python3",
                    shlex.quote(str(helper)),
                    "task-show",
                    "--task-id",
                    state["taskId"],
                ]
            )
            return tool_call_response_events(model, "bash", {"command": command}, call_id="show_task")
        restarted_receipts.append(json.loads(last_tool_text(context)))
        return text_response_events(model, "Recovered the same durable Run and Task; no worker exists.")

    register_api_provider(create_faux_provider(restarted_provider))
    restarted_app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=35),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        restarted_app.run_turn("Recover the prepared orchestration Run and Task without starting work.")
    finally:
        restarted_app.close()

    assert restarted_receipts[0]["result"]["run"]["runId"] == state["runId"]
    assert restarted_receipts[1]["result"]["task"]["taskId"] == state["taskId"]
    database = sqlite3.connect(agent_dir / "orchestration" / "state.sqlite3")
    try:
        assert database.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM workers").fetchone()[0] == 0
    finally:
        database.close()
