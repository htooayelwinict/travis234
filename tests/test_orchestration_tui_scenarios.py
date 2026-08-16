from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import tempfile

import pytest

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
from tests.test_orchestration_worker_relay import (
    git,
    initialize_repository,
    install_fake_travis,
    load_helper,
)


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


def test_tui_worker_ready_receipt_follows_worktree_and_relay_readiness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tmux_executable = shutil.which("tmux")
    if tmux_executable is None:
        pytest.skip("tmux is unavailable")
    server_name = f"travis234-tui-{secrets.token_hex(6)}"
    agent_dir = Path(tempfile.mkdtemp(prefix="t234-tui-", dir="/tmp"))
    repo = initialize_repository(tmp_path / "worker-ready-repo")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore orchestration worktrees")
    (repo / "README.md").write_text("dirty coordinator state\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    fake_travis = install_fake_travis(bin_dir)
    tmux_wrapper = bin_dir / "tmux"
    tmux_wrapper.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(tmux_executable)} -L {shlex.quote(server_name)} \"$@\"\n",
        encoding="utf-8",
    )
    tmux_wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    helper = (
        Path(__file__).parents[1]
        / "travis/resources/skills/orchestration/scripts/orchestrate.py"
    )
    skill = helper.parents[1] / "SKILL.md"
    run_request = tmp_path / "worker-run.json"
    run_request.write_text(
        json.dumps({"objective": "Start a durable parser research worker"}),
        encoding="utf-8",
    )
    run_request.chmod(0o600)
    task_request = tmp_path / "worker-task.json"
    task_request.write_text(
        json.dumps(
            {
                "objective": "Inspect parser ownership read-only",
                "ownership": {"ownedPaths": [], "forbiddenPaths": ["*"]},
                "acceptanceCriteria": ["Worker reaches RPC readiness"],
                "mode": "supervised",
                "maxRounds": 4,
                "commitPolicy": "no_commit",
            }
        ),
        encoding="utf-8",
    )
    task_request.chmod(0o600)
    worker_request = tmp_path / "worker.json"
    worker_request.write_text(
        json.dumps(
            {
                "repository": str(repo),
                "workspaceMode": "worktree",
                "worktreeName": "tui-worker-ready",
                "branch": "tui-worker-ready",
                "base": "main",
            }
        ),
        encoding="utf-8",
    )
    worker_request.chmod(0o600)
    calls = {"count": 0}
    identities: dict[str, str] = {}
    worker_receipt: dict[str, object] = {}

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            assert "<name>orchestration</name>" in (context.system_prompt or "")
            assert "# Local Tmux Orchestration" not in (context.system_prompt or "")
            return tool_call_response_events(
                model,
                "read",
                {"path": str(skill)},
                call_id="worker_load_skill",
            )
        if calls["count"] == 2:
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
                    "tui-worker-run",
                ]
            )
            return tool_call_response_events(model, "bash", {"command": command}, call_id="worker_run")
        if calls["count"] == 3:
            receipt = json.loads(last_tool_text(context))
            identities["runId"] = receipt["result"]["run"]["runId"]
            command = " ".join(
                [
                    "python3",
                    shlex.quote(str(helper)),
                    "task-create",
                    "--run-id",
                    identities["runId"],
                    "--request-file",
                    shlex.quote(str(task_request)),
                    "--consume-request-file",
                    "--idempotency-key",
                    "tui-worker-task",
                ]
            )
            return tool_call_response_events(model, "bash", {"command": command}, call_id="worker_task")
        if calls["count"] == 4:
            receipt = json.loads(last_tool_text(context))
            identities["taskId"] = receipt["result"]["task"]["taskId"]
            command = " ".join(
                [
                    "python3",
                    shlex.quote(str(helper)),
                    "worker-start",
                    "--task-id",
                    identities["taskId"],
                    "--request-file",
                    shlex.quote(str(worker_request)),
                    "--consume-request-file",
                    "--idempotency-key",
                    "tui-worker-start",
                ]
            )
            return tool_call_response_events(
                model,
                "bash",
                {"command": command, "yield_time_ms": 10_000},
                call_id="worker_start",
            )
        receipt = json.loads(last_tool_text(context))
        worker_receipt.update(receipt["result"]["worker"])
        identities["workerId"] = worker_receipt["workerId"]
        return text_response_events(
            model,
            "Worker is ready in its isolated worktree; stopped before dispatch.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(repo),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=35),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        app.run_turn(
            "Start another durable Travis234 in a new Git worktree for supervised parser research, but stop after it is ready."
        )
        visible = "\n".join(
            block.text
            for message in app.messages
            if isinstance(message, ToolResultMessage)
            for block in message.content
            if isinstance(block, TextContent)
        )
        for value in (
            identities["runId"],
            identities["taskId"],
            identities["workerId"],
            worker_receipt["worktree"],
            worker_receipt["baseCommit"],
            worker_receipt["tmuxSession"],
            worker_receipt["travisSessionId"],
            worker_receipt["workspace"],
            worker_receipt["branch"],
            "ready",
            "uncommittedChangesTransferred",
        ):
            assert str(value) in visible
        assert worker_receipt["dirty"] is True
        assert worker_receipt["uncommittedChangesTransferred"] is False
        forbidden = (
            "launch.json",
            "dotenvPath",
            "TRAVIS234_ORCHESTRATION_CAPABILITY",
            "dispatch-capability-",
            "raw stderr",
        )
        assert not any(value in visible for value in forbidden)
    finally:
        app.close()
        if worker_receipt:
            module = load_helper()
            try:
                module.RelayClient(Path(worker_receipt["socketPath"])).request("close", timeout=3)
            except Exception:
                pass
        subprocess.run(
            [tmux_executable, "-L", server_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        shutil.rmtree(agent_dir, ignore_errors=True)

    assert fake_travis.exists()


@pytest.mark.parametrize("scenario", ["research_handoff", "verified_code_return"])
def test_tui_dispatch_returns_bounded_verified_handoff(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    tmux_executable = shutil.which("tmux")
    if tmux_executable is None:
        pytest.skip("tmux is unavailable")
    server_name = f"travis234-tui-dispatch-{secrets.token_hex(6)}"
    agent_dir = Path(tempfile.mkdtemp(prefix="t234-tui-dispatch-", dir="/tmp"))
    repo = initialize_repository(tmp_path / f"{scenario}-repo")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore worktrees")
    coordinator_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    helper = Path(__file__).parents[1] / "travis/resources/skills/orchestration/scripts/orchestrate.py"
    skill = helper.parents[1] / "SKILL.md"
    bin_dir = tmp_path / "bin"
    install_fake_travis(bin_dir)
    tmux_wrapper = bin_dir / "tmux"
    tmux_wrapper.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(tmux_executable)} -L {shlex.quote(server_name)} \"$@\"\n",
        encoding="utf-8",
    )
    tmux_wrapper.chmod(0o755)
    handoff_file = tmp_path / "worker-handoff.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("FAKE_RPC_HELPER", str(helper))
    monkeypatch.setenv("FAKE_RPC_HANDOFF_FILE", str(handoff_file))
    monkeypatch.setenv("FAKE_RPC_PROMPT_DELAY", "0.2")

    def private_request(name: str, value: dict[str, object]) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    run_request = private_request("run.json", {"objective": f"TUI {scenario}"})
    task_request = private_request(
        "task.json",
        {
            "objective": "Return evidence from an isolated worker",
            "ownership": {
                "ownedPaths": [] if scenario == "research_handoff" else ["worker-result.txt"],
                "forbiddenPaths": ["README.md"],
            },
            "acceptanceCriteria": ["Return one verified handoff packet"],
            "mode": "supervised",
            "maxRounds": 4,
            "commitPolicy": "no_commit" if scenario == "research_handoff" else "commit",
        },
    )
    worker_request = private_request(
        "worker.json",
        {
            "repository": str(repo),
            "workspaceMode": "worktree",
            "worktreeName": f"tui-{scenario}",
            "branch": f"tui-{scenario}",
            "base": "main",
        },
    )
    dispatch_request = private_request(
        "dispatch.json",
        {
            "prompt": "Verify the assigned result and return the bounded packet.",
            "context": ["The coordinator needs evidence, not an automatic merge."],
            "requiredVerification": ["Inspect the worker workspace before reporting."],
        },
    )
    calls = {"count": 0}
    identities: dict[str, str] = {}
    worker_receipt: dict[str, object] = {}
    terminal_receipt: dict[str, object] = {}

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(
                model, "read", {"path": str(skill)}, call_id=f"{scenario}_load"
            )
        if calls["count"] == 2:
            command = f"python3 {shlex.quote(str(helper))} run-create --request-file {shlex.quote(str(run_request))} --consume-request-file --idempotency-key {scenario}-run"
            return tool_call_response_events(model, "bash", {"command": command}, call_id=f"{scenario}_run")
        if calls["count"] == 3:
            receipt = json.loads(last_tool_text(context))
            identities["runId"] = receipt["result"]["run"]["runId"]
            command = f"python3 {shlex.quote(str(helper))} task-create --run-id {identities['runId']} --request-file {shlex.quote(str(task_request))} --consume-request-file --idempotency-key {scenario}-task"
            return tool_call_response_events(model, "bash", {"command": command}, call_id=f"{scenario}_task")
        if calls["count"] == 4:
            receipt = json.loads(last_tool_text(context))
            identities["taskId"] = receipt["result"]["task"]["taskId"]
            command = f"python3 {shlex.quote(str(helper))} worker-start --task-id {identities['taskId']} --request-file {shlex.quote(str(worker_request))} --consume-request-file --idempotency-key {scenario}-worker"
            return tool_call_response_events(model, "bash", {"command": command, "yield_time_ms": 10_000}, call_id=f"{scenario}_worker")
        if calls["count"] == 5:
            receipt = json.loads(last_tool_text(context))
            worker_receipt.update(receipt["result"]["worker"])
            identities["workerId"] = str(worker_receipt["workerId"])
            changed_files: list[str] = []
            commit: str | None = None
            if scenario == "verified_code_return":
                workspace = Path(str(worker_receipt["workspace"]))
                (workspace / "worker-result.txt").write_text("verified worker result\n", encoding="utf-8")
                git(workspace, "add", "worker-result.txt")
                git(workspace, "commit", "-m", "test: return worker result")
                changed_files = ["worker-result.txt"]
                commit = git(workspace, "rev-parse", "HEAD").stdout.strip()
            handoff_file.write_text(
                json.dumps(
                    {
                        "outcome": "succeeded",
                        "summary": f"{scenario} completed with worker evidence.",
                        "evidence": ["Worker inspected its isolated workspace"],
                        "changedFiles": changed_files,
                        "commit": commit,
                        "tests": ["focused worker verification passed"],
                        "artifacts": [],
                        "failedAttempts": [],
                        "blockers": [],
                        "questions": [],
                        "recommendedNextAction": "Coordinator should inspect; do not auto-integrate.",
                    }
                ),
                encoding="utf-8",
            )
            handoff_file.chmod(0o600)
            command = f"python3 {shlex.quote(str(helper))} dispatch-start --task-id {identities['taskId']} --worker-id {identities['workerId']} --request-file {shlex.quote(str(dispatch_request))} --consume-request-file --idempotency-key {scenario}-dispatch"
            return tool_call_response_events(model, "bash", {"command": command}, call_id=f"{scenario}_dispatch")
        if calls["count"] == 6:
            receipt = json.loads(last_tool_text(context))
            identities["dispatchId"] = receipt["result"]["dispatch"]["dispatchId"]
            assert receipt["result"]["dispatch"]["status"] == "accepted"
            command = f"python3 {shlex.quote(str(helper))} dispatch-wait --dispatch-id {identities['dispatchId']} --wait-seconds 5"
            return tool_call_response_events(model, "bash", {"command": command, "yield_time_ms": 10_000}, call_id=f"{scenario}_wait")
        receipt = json.loads(last_tool_text(context))
        terminal_receipt.update(receipt["result"])
        return text_response_events(model, f"Received verified {scenario}; no automatic integration performed.")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(repo),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=35),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        app.run_turn(f"Use orchestration to perform the {scenario} scenario and return its evidence.")
        assert terminal_receipt["terminal"] is True
        assert terminal_receipt["packet"]["summary"] == f"{scenario} completed with worker evidence."
        assert terminal_receipt["automaticIntegration"] is False
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == coordinator_head
        visible = "\n".join(
            block.text
            for message in app.messages
            if isinstance(message, ToolResultMessage)
            for block in message.content
            if isinstance(block, TextContent)
        )
        assert identities["dispatchId"] in visible
        assert "dispatch-capability-" not in visible
        assert "TRAVIS234_ORCHESTRATION_CAPABILITY" not in visible
    finally:
        app.close()
        if worker_receipt:
            try:
                load_helper().RelayClient(Path(str(worker_receipt["socketPath"]))).request(
                    "close", timeout=3
                )
            except Exception:
                pass
        subprocess.run(
            [tmux_executable, "-L", server_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        shutil.rmtree(agent_dir, ignore_errors=True)


@pytest.mark.parametrize("scenario", ["question_reply", "bounded_ping_pong"])
def test_tui_question_reply_and_bounded_ping_pong(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    tmux_executable = shutil.which("tmux")
    if tmux_executable is None:
        pytest.skip("tmux is unavailable")
    server_name = f"travis234-tui-dialogue-{secrets.token_hex(6)}"
    agent_dir = Path(tempfile.mkdtemp(prefix="t234-tui-dialogue-", dir="/tmp"))
    repo = initialize_repository(tmp_path / f"{scenario}-repo")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore worktrees")
    coordinator_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    helper = Path(__file__).parents[1] / "travis/resources/skills/orchestration/scripts/orchestrate.py"
    skill = helper.parents[1] / "SKILL.md"
    bin_dir = tmp_path / "bin"
    install_fake_travis(bin_dir)
    tmux_wrapper = bin_dir / "tmux"
    tmux_wrapper.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(tmux_executable)} -L {shlex.quote(server_name)} \"$@\"\n",
        encoding="utf-8",
    )
    tmux_wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("FAKE_RPC_HELPER", str(helper))
    handoff_file = tmp_path / "dialogue-handoff.json"
    monkeypatch.setenv("FAKE_RPC_HANDOFF_FILE", str(handoff_file))
    question_file = tmp_path / "dialogue-question.json"
    if scenario == "question_reply":
        monkeypatch.setenv("FAKE_RPC_QUESTION_FILE", str(question_file))

    def private_request(name: str, value: dict[str, object]) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    run_request = private_request("dialogue-run.json", {"objective": scenario})
    task_request = private_request(
        "dialogue-task.json",
        {
            "objective": "Resolve parser evidence through bounded dialogue",
            "ownership": {"ownedPaths": [], "forbiddenPaths": ["README.md"]},
            "acceptanceCriteria": ["Return acknowledged evidence"],
            "mode": "supervised",
            "maxRounds": 4,
            "commitPolicy": "no_commit",
        },
    )
    worker_request = private_request(
        "dialogue-worker.json",
        {
            "repository": str(repo),
            "workspaceMode": "worktree",
            "worktreeName": f"tui-{scenario}",
            "branch": f"tui-{scenario}",
            "base": "main",
        },
    )
    first_dispatch_request = private_request(
        "dialogue-dispatch-one.json",
        {
            "prompt": "Inspect the selected parser evidence.",
            "context": ["Use only the isolated worker workspace."],
            "requiredVerification": ["Cite inspected evidence"],
        },
    )
    reply_request = private_request(
        "dialogue-reply.json",
        {
            "prompt": "Use the stable parser fixture.",
            "context": ["The coordinator selected stable."],
            "requiredVerification": ["Verify the stable fixture"],
        },
    )
    question_file.write_text(
        json.dumps(
            {
                "kind": "question",
                "payload": {"text": "Stable or experimental fixture?", "choices": ["stable", "experimental"]},
                "parentMessageId": None,
            }
        ),
        encoding="utf-8",
    )
    question_file.chmod(0o600)

    def write_handoff(summary: str) -> None:
        handoff_file.write_text(
            json.dumps(
                {
                    "outcome": "succeeded",
                    "summary": summary,
                    "evidence": ["Worker verified the isolated parser fixture"],
                    "changedFiles": [],
                    "commit": None,
                    "tests": ["focused evidence check passed"],
                    "artifacts": [],
                    "failedAttempts": [],
                    "blockers": [],
                    "questions": [],
                    "recommendedNextAction": "Acknowledge and summarize the evidence.",
                }
            ),
            encoding="utf-8",
        )
        handoff_file.chmod(0o600)

    write_handoff("final answer after coordinator reply" if scenario == "question_reply" else "first answer needs correction")
    calls = {"count": 0}
    ids: dict[str, str] = {}
    worker_receipt: dict[str, object] = {}

    def bash(model, command: str, call_id: str):
        return tool_call_response_events(
            model,
            "bash",
            {"command": command, "yield_time_ms": 10_000},
            call_id=call_id,
        )

    def provider(model, context):
        calls["count"] += 1
        count = calls["count"]
        if count == 1:
            return tool_call_response_events(model, "read", {"path": str(skill)}, call_id="dialogue-load")
        if count == 2:
            return bash(model, f"python3 {shlex.quote(str(helper))} run-create --request-file {shlex.quote(str(run_request))} --consume-request-file --idempotency-key {scenario}-run", "dialogue-run")
        receipt = json.loads(last_tool_text(context))
        if count == 3:
            ids["runId"] = receipt["result"]["run"]["runId"]
            return bash(model, f"python3 {shlex.quote(str(helper))} task-create --run-id {ids['runId']} --request-file {shlex.quote(str(task_request))} --consume-request-file --idempotency-key {scenario}-task", "dialogue-task")
        if count == 4:
            ids["taskId"] = receipt["result"]["task"]["taskId"]
            return bash(model, f"python3 {shlex.quote(str(helper))} worker-start --task-id {ids['taskId']} --request-file {shlex.quote(str(worker_request))} --consume-request-file --idempotency-key {scenario}-worker", "dialogue-worker")
        if count == 5:
            worker_receipt.update(receipt["result"]["worker"])
            ids["workerId"] = str(worker_receipt["workerId"])
            return bash(model, f"python3 {shlex.quote(str(helper))} dispatch-start --task-id {ids['taskId']} --worker-id {ids['workerId']} --request-file {shlex.quote(str(first_dispatch_request))} --consume-request-file --idempotency-key {scenario}-dispatch-one", "dialogue-dispatch-one")
        if count == 6:
            ids["dispatchOne"] = receipt["result"]["dispatch"]["dispatchId"]
            return bash(model, f"python3 {shlex.quote(str(helper))} message-check --run-id {ids['runId']} --wait-seconds 5 --limit 10", "dialogue-check-one")
        if count == 7:
            first_message = receipt["result"]["messages"][0]
            ids["firstMessage"] = first_message["messageId"]
            expected_kind = "question" if scenario == "question_reply" else "handoff"
            assert first_message["kind"] == expected_kind
            return bash(model, f"python3 {shlex.quote(str(helper))} message-ack --message-id {ids['firstMessage']} --idempotency-key {scenario}-ack-one", "dialogue-ack-one")
        if count == 8:
            assert receipt["result"]["message"]["acknowledgedAt"] is not None
            if scenario == "question_reply":
                return bash(model, f"python3 {shlex.quote(str(helper))} message-reply --message-id {ids['firstMessage']} --request-file {shlex.quote(str(reply_request))} --consume-request-file --idempotency-key question-reply", "dialogue-reply")
            write_handoff("corrected answer after coordinator review")
            correction_request = private_request(
                "dialogue-correction.json",
                {
                    "prompt": "Correct the first answer with the missing evidence.",
                    "context": ["The first packet was reviewed and acknowledged."],
                    "requiredVerification": ["Return corrected evidence"],
                    "parentMessageId": ids["firstMessage"],
                },
            )
            return bash(model, f"python3 {shlex.quote(str(helper))} dispatch-start --task-id {ids['taskId']} --worker-id {ids['workerId']} --request-file {shlex.quote(str(correction_request))} --consume-request-file --idempotency-key correction-two", "dialogue-correction")
        if count == 9:
            if scenario == "question_reply":
                assert receipt["result"]["message"]["kind"] == "reply"
            else:
                ids["dispatchTwo"] = receipt["result"]["dispatch"]["dispatchId"]
                assert receipt["result"]["dispatch"]["parentMessageId"] == ids["firstMessage"]
            return bash(model, f"python3 {shlex.quote(str(helper))} message-check --run-id {ids['runId']} --wait-seconds 5 --limit 10", "dialogue-check-final")
        if count == 10:
            final_message = receipt["result"]["messages"][0]
            ids["finalMessage"] = final_message["messageId"]
            assert final_message["kind"] == "handoff"
            expected_summary = "final answer after coordinator reply" if scenario == "question_reply" else "corrected answer after coordinator review"
            assert final_message["payload"]["summary"] == expected_summary
            return bash(model, f"python3 {shlex.quote(str(helper))} message-ack --message-id {ids['finalMessage']} --idempotency-key {scenario}-ack-final", "dialogue-ack-final")
        assert receipt["result"]["message"]["acknowledgedAt"] is not None
        return text_response_events(model, f"Completed and acknowledged {scenario} in the same Travis worker session.")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(repo),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=35),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        app.run_turn(f"Use orchestration for the {scenario} flow and acknowledge the final evidence.")
        database = sqlite3.connect(agent_dir / "orchestration" / "state.sqlite3")
        try:
            kinds = tuple(
                row[0]
                for row in database.execute(
                    "SELECT kind FROM messages ORDER BY created_at, message_id"
                )
            )
            expected = ("question", "reply", "handoff") if scenario == "question_reply" else ("handoff", "handoff")
            assert kinds == expected
            assert database.execute("SELECT count(*) FROM messages WHERE sender = 'worker' AND acknowledged_at IS NULL").fetchone()[0] == 0
            assert database.execute("SELECT prompt_count FROM tasks").fetchone()[0] == 2
            assert database.execute("SELECT count(*) FROM workers").fetchone()[0] == 1
        finally:
            database.close()
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == coordinator_head
        assert ids["workerId"] in "\n".join(app.tui.render(120)) or ids["workerId"] in str(app.messages)
    finally:
        app.close()
        if worker_receipt:
            try:
                load_helper().RelayClient(Path(str(worker_receipt["socketPath"]))).request("close", timeout=3)
            except Exception:
                pass
        subprocess.run(
            [tmux_executable, "-L", server_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        shutil.rmtree(agent_dir, ignore_errors=True)
