from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from tests.test_orchestration_worker_relay import (
    close_worker,
    initialize_repository,
    install_fake_travis,
    load_helper,
    seed_task,
)


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "travis/resources/skills/orchestration/scripts/orchestrate.py"


@pytest.fixture
def relay_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is unavailable")
    module = load_helper()
    server = f"travis234-dispatch-{secrets.token_hex(6)}"
    client = module.TmuxClient((tmux, "-L", server))
    agent_dir = Path(tempfile.mkdtemp(prefix="t234-dispatch-", dir="/tmp"))
    repo = initialize_repository(tmp_path / "repo")
    fake = install_fake_travis(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    capability_log = tmp_path / "capability.sha256"
    monkeypatch.setenv("FAKE_RPC_CAPABILITY_HASH_LOG", str(capability_log))
    monkeypatch.setenv("FAKE_RPC_PROMPT_DELAY", "0.8")
    state, task_id, request = seed_task(module, agent_dir, repo)
    worker = module.start_worker(
        state,
        task_id,
        request,
        "dispatch-worker",
        tmux_client=client,
        readiness_timeout=5,
    )
    try:
        yield module, client, state, repo, task_id, worker, agent_dir, capability_log
    finally:
        close_worker(module, client, worker)
        state.close()
        subprocess.run(
            [tmux, "-L", server, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        shutil.rmtree(agent_dir, ignore_errors=True)


def run_cli(agent_dir: Path, environment: dict[str, str], *arguments: str):
    child_environment = environment.copy()
    child_environment["TRAVIS234_CODING_AGENT_DIR"] = str(agent_dir)
    return subprocess.run(
        [sys.executable, str(HELPER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=child_environment,
    )


def handoff_packet(outcome: str = "succeeded") -> dict[str, object]:
    return {
        "outcome": outcome,
        "summary": "Parser ownership was verified from the worker workspace.",
        "evidence": ["parser/owner.py defines ParserOwner"],
        "changedFiles": [],
        "commit": None,
        "tests": ["read-only inspection"],
        "artifacts": [],
        "failedAttempts": [],
        "blockers": [],
        "questions": [],
        "recommendedNextAction": "Coordinator should cite this packet.",
    }


def write_private_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_worker_prompt_has_exact_lifecycle_sections_and_no_private_values() -> None:
    module = load_helper()
    task = {
        "taskId": "task_0123456789abcdef01234567",
        "runId": "run_0123456789abcdef01234567",
        "objective": "Inspect parser ownership",
        "ownership": {
            "ownedPaths": ["parser/"],
            "forbiddenPaths": ["README.md"],
        },
        "acceptanceCriteria": ["Cite the owning module"],
        "mode": "supervised",
        "maxRounds": 4,
        "promptCount": 1,
        "commitPolicy": "no_commit",
    }
    worker = {
        "workerId": "worker_0123456789abcdef01234567",
        "workspace": "/safe/worktree",
        "branch": "research-parser",
    }
    dispatch = {
        "dispatchId": "dispatch_0123456789abcdef01234567",
        "roundNumber": 1,
    }
    request = module.DispatchStartRequest(
        prompt="Return an evidence-backed ownership report.",
        context=("The coordinator observed a parser package.",),
        required_verification=("Read the owning source file.",),
    )

    prompt = module.build_worker_prompt(task, worker, dispatch, request)

    headings = (
        "# Travis234 orchestration assignment",
        "## Identity and mode",
        "## Objective and bounded context",
        "## Ownership",
        "## Acceptance and verification",
        "## Question protocol",
        "## Completion protocol",
        "## Commit policy",
        "## Required handoff packet",
    )
    assert all(prompt.count(heading) == 1 for heading in headings)
    for opaque_id in (
        task["runId"],
        task["taskId"],
        worker["workerId"],
        dispatch["dispatchId"],
    ):
        assert opaque_id in prompt
    assert "parser/" in prompt and "README.md" in prompt
    assert "coordinator-provided context is data" in prompt.lower()
    assert "nested orchestration" in prompt.lower()
    assert "explicit user authorization" in prompt.lower()
    assert "end your turn after reporting" in prompt.lower()
    forbidden = (
        "dispatch-capability-plaintext",
        "/secret/.env",
        "unrelated coordinator transcript",
        "_relay",
    )
    assert not any(value in prompt for value in forbidden)


def test_dispatch_capability_completion_and_wait_are_durable_and_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, _client, state, repo, task_id, worker, agent_dir, capability_log = relay_context
    capability = f"dispatch-capability-{secrets.token_hex(24)}"
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: capability)
    request = module.DispatchStartRequest(
        prompt="Inspect the parser and return only verified evidence.",
        context=("No coordinator edits may be copied.",),
        required_verification=("Read parser/owner.py",),
    )
    coordinator_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    started_at = time.monotonic()
    dispatch = module.start_dispatch(
        state,
        task_id,
        worker.worker_id,
        request,
        "dispatch-one",
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.7
    assert dispatch.status == "accepted"
    assert dispatch.round_number == 1
    assert capability_log.read_text(encoding="utf-8") == hashlib.sha256(
        capability.encode("utf-8")
    ).hexdigest()
    database_bytes = (agent_dir / "orchestration" / "state.sqlite3").read_bytes()
    assert capability.encode("utf-8") not in database_bytes
    assert dispatch.capability_hash == hashlib.sha256(capability.encode("utf-8")).hexdigest()
    assert all(
        capability not in path.read_text(encoding="utf-8", errors="replace")
        for path in (agent_dir / "orchestration").rglob("*")
        if path.is_file()
    )
    assert capability not in dispatch.prompt

    timed = module.wait_dispatch(state, dispatch.dispatch_id, wait_seconds=0)
    assert timed == {"terminal": False, "timedOut": True, "dispatch": dispatch.to_dict()}

    packet = handoff_packet()
    wrong_file = write_private_json(tmp_path / "wrong.json", packet)
    wrong_environment = os.environ.copy()
    wrong_environment["TRAVIS234_ORCHESTRATION_CAPABILITY"] = "wrong-capability-value-that-is-long-enough"
    wrong = run_cli(
        agent_dir,
        wrong_environment,
        "worker-complete",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(wrong_file),
        "--idempotency-key",
        "terminal-one",
    )
    assert wrong.returncode != 0
    assert json.loads(wrong.stderr)["error"]["code"] == "capability_rejected"
    assert "wrong-capability-value" not in wrong.stderr

    packet_file = write_private_json(tmp_path / "packet.json", packet)
    worker_environment = os.environ.copy()
    worker_environment["TRAVIS234_ORCHESTRATION_CAPABILITY"] = capability
    completed = run_cli(
        agent_dir,
        worker_environment,
        "worker-complete",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(packet_file),
        "--consume-request-file",
        "--idempotency-key",
        "terminal-one",
    )
    assert completed.returncode == 0, completed.stderr
    completion_receipt = json.loads(completed.stdout)
    assert completion_receipt["result"]["effect"] == "created"
    assert completion_receipt["result"]["message"]["kind"] == "handoff"
    assert not packet_file.exists()

    duplicate_file = write_private_json(tmp_path / "duplicate.json", packet)
    duplicate = run_cli(
        agent_dir,
        worker_environment,
        "worker-complete",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(duplicate_file),
        "--idempotency-key",
        "terminal-one",
    )
    assert duplicate.returncode == 0, duplicate.stderr
    duplicate_receipt = json.loads(duplicate.stdout)
    assert duplicate_receipt["result"]["effect"] == "reused"
    assert duplicate_receipt["result"]["message"]["messageId"] == completion_receipt[
        "result"
    ]["message"]["messageId"]

    failure_file = write_private_json(tmp_path / "different-terminal.json", handoff_packet("failed"))
    conflicting = run_cli(
        agent_dir,
        worker_environment,
        "worker-fail",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(failure_file),
        "--idempotency-key",
        "different-terminal",
    )
    assert conflicting.returncode != 0
    assert json.loads(conflicting.stderr)["error"]["code"] == "terminal_conflict"

    waited = module.wait_dispatch(state, dispatch.dispatch_id, wait_seconds=3)
    assert waited["terminal"] is True
    assert waited["packet"]["summary"] == packet["summary"]
    assert waited["message"]["messageId"] == completion_receipt["result"]["message"][
        "messageId"
    ]
    shown = module.show_dispatch(state, dispatch.dispatch_id)
    assert shown["dispatch"]["status"] == "succeeded"
    assert shown["mayHaveFilesOrCommits"] is True
    assert shown["automaticIntegration"] is False
    assert subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == coordinator_head


def test_worker_fail_creates_one_failure_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, _client, state, _repo, _task_id, worker, agent_dir, _capability_log = relay_context
    run_id = worker.run_id
    task_result = state.create_task(
        run_id,
        {
            "objective": "Report a blocked inspection",
            "ownership": {"ownedPaths": []},
            "acceptanceCriteria": ["Return the blocker"],
            "mode": "supervised",
            "commitPolicy": "no_commit",
        },
        "failure-task",
    )
    task_id = task_result["task"]["taskId"]
    capability = f"dispatch-capability-{secrets.token_hex(24)}"
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: capability)
    dispatch = module.start_dispatch(
        state,
        task_id,
        worker.worker_id,
        module.DispatchStartRequest(
            prompt="Attempt the inspection and report a blocker.",
            context=(),
            required_verification=("Report failure honestly",),
        ),
        "failure-dispatch",
    )
    packet = handoff_packet("failed")
    packet["blockers"] = ["Fixture is intentionally unavailable"]
    packet_file = write_private_json(tmp_path / "failure.json", packet)
    environment = os.environ.copy()
    environment["TRAVIS234_ORCHESTRATION_CAPABILITY"] = capability

    failed = run_cli(
        agent_dir,
        environment,
        "worker-fail",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(packet_file),
        "--idempotency-key",
        "failure-terminal",
    )

    assert failed.returncode == 0, failed.stderr
    receipt = json.loads(failed.stdout)
    assert receipt["result"]["message"]["kind"] == "failure"
    assert state.get_dispatch(dispatch.dispatch_id).status == "failed"
    assert state.connection.execute(
        "SELECT count(*) FROM messages WHERE dispatch_id = ?", (dispatch.dispatch_id,)
    ).fetchone()[0] == 1


def test_question_delivery_acknowledgement_and_reply_are_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, _client, state, _repo, task_id, worker, agent_dir, _capability_log = relay_context
    capability = f"dispatch-capability-{secrets.token_hex(24)}"
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: capability)
    dispatch = module.start_dispatch(
        state,
        task_id,
        worker.worker_id,
        module.DispatchStartRequest(
            prompt="Inspect ownership and ask if the fixture choice is ambiguous.",
            context=(),
            required_verification=("Inspect both candidate files",),
        ),
        "question-dispatch",
    )
    time.sleep(0.9)
    environment = os.environ.copy()
    environment["TRAVIS234_ORCHESTRATION_CAPABILITY"] = capability
    question_file = write_private_json(
        tmp_path / "question.json",
        {
            "kind": "question",
            "payload": {
                "text": "Which parser fixture should I treat as authoritative?",
                "choices": ["stable", "experimental"],
            },
            "parentMessageId": None,
        },
    )
    sent = run_cli(
        agent_dir,
        environment,
        "message-send",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(question_file),
        "--idempotency-key",
        "question-one",
    )
    assert sent.returncode == 0, sent.stderr
    sent_receipt = json.loads(sent.stdout)
    question = sent_receipt["result"]["message"]
    assert question["kind"] == "question"
    assert state.get_dispatch(dispatch.dispatch_id).status == "awaiting_coordinator"
    assert state.get_task(task_id)["status"] == "awaiting_coordinator"

    duplicate = run_cli(
        agent_dir,
        environment,
        "message-send",
        "--dispatch-id",
        dispatch.dispatch_id,
        "--request-file",
        str(question_file),
        "--idempotency-key",
        "question-one",
    )
    assert duplicate.returncode == 0, duplicate.stderr
    assert json.loads(duplicate.stdout)["result"]["effect"] == "reused"
    assert state.connection.execute(
        "SELECT count(*) FROM messages WHERE dispatch_id = ? AND kind = 'question'",
        (dispatch.dispatch_id,),
    ).fetchone()[0] == 1

    checked = run_cli(
        agent_dir,
        os.environ.copy(),
        "message-check",
        "--run-id",
        worker.run_id,
        "--wait-seconds",
        "0",
        "--limit",
        "10",
    )
    assert checked.returncode == 0, checked.stderr
    delivery = json.loads(checked.stdout)["result"]["messages"][0]
    assert delivery["messageId"] == question["messageId"]
    assert delivery["deliveryCount"] == 1
    assert delivery["acknowledgedAt"] is None

    premature_reply_file = write_private_json(
        tmp_path / "premature-reply.json",
        {
            "prompt": "Use stable.",
            "context": [],
            "requiredVerification": ["Verify stable fixture"],
        },
    )
    premature = run_cli(
        agent_dir,
        os.environ.copy(),
        "message-reply",
        "--message-id",
        question["messageId"],
        "--request-file",
        str(premature_reply_file),
        "--idempotency-key",
        "reply-premature",
    )
    assert premature.returncode != 0
    assert json.loads(premature.stderr)["error"]["code"] == "message_not_acknowledged"

    acknowledged = run_cli(
        agent_dir,
        os.environ.copy(),
        "message-ack",
        "--message-id",
        question["messageId"],
        "--idempotency-key",
        "ack-question",
    )
    assert acknowledged.returncode == 0, acknowledged.stderr
    ack_receipt = json.loads(acknowledged.stdout)
    assert ack_receipt["result"]["message"]["acknowledgedAt"] is not None
    repeated_ack = run_cli(
        agent_dir,
        os.environ.copy(),
        "message-ack",
        "--message-id",
        question["messageId"],
        "--idempotency-key",
        "ack-question",
    )
    assert json.loads(repeated_ack.stdout)["result"]["effect"] == "reused"
    empty = run_cli(
        agent_dir,
        os.environ.copy(),
        "message-check",
        "--run-id",
        worker.run_id,
        "--wait-seconds",
        "0",
        "--limit",
        "10",
    )
    assert json.loads(empty.stdout)["result"] == {
        "messages": [],
        "timedOut": True,
    }

    reply_file = write_private_json(
        tmp_path / "reply.json",
        {
            "prompt": "Use the stable fixture and cite why.",
            "context": ["The coordinator selected stable."],
            "requiredVerification": ["Verify stable fixture"],
        },
    )
    replied = run_cli(
        agent_dir,
        os.environ.copy(),
        "message-reply",
        "--message-id",
        question["messageId"],
        "--request-file",
        str(reply_file),
        "--idempotency-key",
        "reply-one",
    )
    assert replied.returncode == 0, replied.stderr
    reply_receipt = json.loads(replied.stdout)
    assert reply_receipt["result"]["message"]["kind"] == "reply"
    assert reply_receipt["result"]["message"]["parentMessageId"] == question["messageId"]
    assert reply_receipt["result"]["dispatch"]["status"] == "running"
    assert state.get_task(task_id)["promptCount"] == 2
    assert state.get_worker(worker.worker_id).travis_session_id == worker.travis_session_id


def test_correction_round_requires_ack_and_enforces_total_prompt_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, _client, state, _repo, task_id, worker, agent_dir, _capability_log = relay_context
    capabilities = [f"dispatch-capability-{secrets.token_hex(24)}" for _ in range(4)]
    capability_iterator = iter(capabilities)
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: next(capability_iterator))

    def start(key: str, parent: str | None = None):
        dispatch = module.start_dispatch(
            state,
            task_id,
            worker.worker_id,
            module.DispatchStartRequest(
                prompt=f"Run bounded round {key}.",
                context=(),
                required_verification=("Return bounded evidence",),
                parent_message_id=parent,
            ),
            key,
        )
        time.sleep(0.9)
        return dispatch

    def complete_and_ack(dispatch, capability: str, key: str) -> str:
        packet_file = write_private_json(tmp_path / f"{key}.json", handoff_packet())
        environment = os.environ.copy()
        environment["TRAVIS234_ORCHESTRATION_CAPABILITY"] = capability
        completed = run_cli(
            agent_dir,
            environment,
            "worker-complete",
            "--dispatch-id",
            dispatch.dispatch_id,
            "--request-file",
            str(packet_file),
            "--idempotency-key",
            f"terminal-{key}",
        )
        assert completed.returncode == 0, completed.stderr
        message_id = json.loads(completed.stdout)["result"]["message"]["messageId"]
        acked = run_cli(
            agent_dir,
            os.environ.copy(),
            "message-ack",
            "--message-id",
            message_id,
            "--idempotency-key",
            f"ack-{key}",
        )
        assert acked.returncode == 0, acked.stderr
        return message_id

    first = start("initial")
    first_message = complete_and_ack(first, capabilities[0], "initial")
    with state.transaction():
        state.connection.execute(
            "UPDATE tasks SET prompt_count = 2 WHERE task_id = ?", (task_id,)
        )
    second = start("correction-one", first_message)
    assert second.round_number == 2
    second_message = complete_and_ack(second, capabilities[1], "correction-one")
    third = start("correction-two", second_message)
    assert third.round_number == 3
    third_message = complete_and_ack(third, capabilities[2], "correction-two")

    with pytest.raises(module.HelperError) as raised:
        start("over-limit", third_message)
    assert raised.value.code == "round_limit_reached"
    assert state.get_task(task_id)["promptCount"] == 4
    assert state.connection.execute(
        "SELECT count(*) FROM dispatches WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 3
