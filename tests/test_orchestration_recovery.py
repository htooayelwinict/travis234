from __future__ import annotations

import json
from pathlib import Path
import secrets
import time

import pytest

from tests.test_orchestration_dispatch import handoff_packet, relay_context, write_private_json
from tests.test_orchestration_worker_relay import load_helper


def test_full_handoff_prompt_and_acceptance_are_nonmonitoring(
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, _client, state, _repo, _task_id, worker, _agent_dir, _capability_log = relay_context
    task_result = state.create_task(
        worker.run_id,
        {
            "objective": "Own the complete architecture research scope",
            "ownership": {"ownedPaths": [], "forbiddenPaths": ["README.md"]},
            "acceptanceCriteria": ["Preserve a durable report"],
            "mode": "full_handoff",
            "maxRounds": 4,
            "commitPolicy": "no_commit",
        },
        "full-handoff-task",
    )
    task_id = task_result["task"]["taskId"]
    capability = f"dispatch-capability-{secrets.token_hex(24)}"
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: capability)
    request = module.DispatchStartRequest(
        prompt="Take ownership of the handed-off research scope.",
        context=("The coordinator is not waiting synchronously.",),
        required_verification=("Preserve a report",),
    )
    preview = module.build_worker_prompt(
        state.get_task(task_id),
        worker,
        {"dispatchId": "dispatch_0123456789abcdef01234567", "roundNumber": 1},
        request,
    )
    lowered = preview.lower()
    assert "owns the whole handed-off scope" in lowered
    assert "may preserve a report" in lowered
    assert "no obligation to notify a waiting coordinator" in lowered
    assert "original user authorized" in lowered

    started_at = time.monotonic()
    dispatch = module.start_dispatch(
        state,
        task_id,
        worker.worker_id,
        request,
        "full-handoff-dispatch",
    )
    receipt = module.dispatch_receipt(state, dispatch)

    assert time.monotonic() - started_at < 0.7
    assert receipt["monitoring"] is False
    assert receipt["automaticReplay"] is False
    assert receipt["automaticIntegration"] is False
    assert receipt["runId"] == worker.run_id
    for field in (
        "taskId",
        "workerId",
        "dispatchId",
        "branch",
        "worktree",
        "tmuxSession",
        "travisSessionId",
    ):
        assert receipt[field]
    retained = state.get_worker(worker.worker_id)
    assert retained.retained is True
    assert retained.status == "retained"
    assert state.get_task(task_id)["status"] == "active"
    assert state.connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_retain_release_and_cancel_preserve_git_state(
    relay_context,
) -> None:
    module, client, state, _repo, task_id, worker, _agent_dir, _capability_log = relay_context
    workspace = Path(str(worker.workspace))
    retained = module.retain_worker(state, worker.worker_id, "retain-worker")
    assert retained["worker"]["retained"] is True
    assert retained["worker"]["status"] == "retained"
    released = module.release_worker(
        state,
        worker.worker_id,
        "release-worker",
        tmux_client=client,
    )
    assert released["worker"]["status"] == "stopped"
    assert released["actionsNotPerformed"] == [
        "replay",
        "integration",
        "push",
        "branchDeletion",
        "worktreeDeletion",
    ]
    assert workspace.exists()
    assert not client.has_session(worker.tmux_session)

    # A released Worker cannot be reused for a new Dispatch.
    with pytest.raises(module.HelperError) as raised:
        module.start_dispatch(
            state,
            task_id,
            worker.worker_id,
            module.DispatchStartRequest("Do not run", (), ()),
            "after-release",
        )
    assert raised.value.code == "worker_not_idle"


def test_supervised_cancel_stops_only_exact_worker_and_preserves_workspace(
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, client, state, _repo, task_id, worker, _agent_dir, _capability_log = relay_context
    monkeypatch.setattr(
        module.secrets,
        "token_urlsafe",
        lambda _size: f"dispatch-capability-{secrets.token_hex(24)}",
    )
    dispatch = module.start_dispatch(
        state,
        task_id,
        worker.worker_id,
        module.DispatchStartRequest("Begin cancellable work", (), ()),
        "cancel-dispatch",
    )
    receipt = module.cancel_dispatch(
        state,
        dispatch.dispatch_id,
        "cancel-one",
        tmux_client=client,
    )
    assert receipt["dispatch"]["status"] == "cancelled"
    assert receipt["worker"]["status"] == "stopped"
    assert receipt["automaticReplay"] is False
    assert receipt["actionsNotPerformed"][-1] == "worktreeDeletion"
    assert Path(str(worker.workspace)).exists()
    assert not client.has_session(worker.tmux_session)


def test_abandon_keeps_worker_and_late_packet_is_stale_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relay_context,
) -> None:
    module, client, state, _repo, task_id, worker, agent_dir, _capability_log = relay_context
    capability = f"dispatch-capability-{secrets.token_hex(24)}"
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: capability)
    dispatch = module.start_dispatch(
        state,
        task_id,
        worker.worker_id,
        module.DispatchStartRequest("Continue without coordinator monitoring", (), ()),
        "abandon-dispatch",
    )
    time.sleep(0.9)
    abandoned = module.abandon_dispatch(state, dispatch.dispatch_id, "abandon-one")
    assert abandoned["dispatch"]["status"] == "abandoned"
    assert abandoned["monitoring"] is False
    assert client.has_session(worker.tmux_session)
    packet_file = write_private_json(tmp_path / "late.json", handoff_packet())
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("TRAVIS234_ORCHESTRATION_CAPABILITY", capability)
    completed = module.execute(
        [
            "worker-complete",
            "--dispatch-id",
            dispatch.dispatch_id,
            "--request-file",
            str(packet_file),
            "--idempotency-key",
            "late-terminal",
        ]
    )
    assert completed["result"]["stale"] is True
    assert state.get_dispatch(dispatch.dispatch_id).status == "abandoned"
    assert state.get_task(task_id)["status"] == "abandoned"


class FakeTmux:
    def __init__(self, alive: set[str]) -> None:
        self.alive = set(alive)

    def has_session(self, name: str) -> bool:
        return name in self.alive


class FakeRelay:
    def __init__(self, result: dict[str, object] | Exception) -> None:
        self.result = result

    def request(self, action: str, *, timeout: float):
        assert action == "state"
        assert timeout <= 10
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_recover_matrix_never_replays_and_inspect_only_never_mutates(tmp_path: Path) -> None:
    module = load_helper()
    root = tmp_path / "agent" / "orchestration"
    root.mkdir(parents=True)
    state = module.StateStore.open_at(root)
    try:
        run = state.create_run({"objective": "recover matrix"}, "recover-run")["run"]
        task = state.create_task(
            run["runId"],
            {
                "objective": "observe workers",
                "ownership": {"ownedPaths": []},
                "acceptanceCriteria": ["No replay"],
                "mode": "supervised",
                "commitPolicy": "no_commit",
            },
            "recover-task",
        )["task"]
        workers: dict[str, dict[str, str]] = {}
        timestamp = module.utc_now()
        with state.transaction():
            for label in ("healthy", "missing", "socket_absent", "version_bad"):
                worker_id = module.new_id("worker")
                session = module.tmux_name(worker_id)
                socket = str(root / "sockets" / f"{label}.sock")
                workspace = str(tmp_path / label)
                Path(workspace).mkdir()
                state.connection.execute(
                    """
                    INSERT INTO workers(
                        worker_id, run_id, workspace, repository, branch, base_commit,
                        worktree_path, tmux_session, socket_path, travis_session_id,
                        status, retained, protocol_version, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', 0, ?, ?, ?)
                    """,
                    (
                        worker_id,
                        run["runId"],
                        workspace,
                        workspace,
                        label,
                        "0" * 40,
                        workspace,
                        session,
                        socket,
                        f"session-{label}",
                        module.PROTOCOL_VERSION,
                        timestamp,
                        timestamp,
                    ),
                )
                workers[label] = {"id": worker_id, "session": session, "socket": socket, "workspace": workspace}
        stale_dir = root / "runs" / run["runId"] / "workers" / workers["missing"]["id"]
        stale_dir.mkdir(parents=True)
        stale_launch = stale_dir / "launch.json"
        stale_launch.write_text("{}", encoding="utf-8")
        stale_launch.chmod(0o600)
        tmux = FakeTmux(
            {
                workers["healthy"]["session"],
                workers["socket_absent"]["session"],
                workers["version_bad"]["session"],
            }
        )
        relay_results = {
            workers["healthy"]["socket"]: {
                "busy": False,
                "sessionId": "session-healthy",
                "cwd": workers["healthy"]["workspace"],
            },
            workers["socket_absent"]["socket"]: module.HelperError(
                "relay_unavailable", "unavailable"
            ),
            workers["version_bad"]["socket"]: module.HelperError(
                "incompatible_protocol", "mismatch"
            ),
        }
        factory = lambda path: FakeRelay(relay_results[str(path)])

        inspected = module.recover_run(
            state,
            run["runId"],
            inspect_only=True,
            tmux_client=tmux,
            relay_factory=factory,
        )
        assert inspected["automaticReplay"] is False
        assert stale_launch.exists()
        assert all(state.get_worker(value["id"]).status == "ready" for value in workers.values())

        recovered = module.recover_run(
            state,
            run["runId"],
            inspect_only=False,
            tmux_client=tmux,
            relay_factory=factory,
        )
        observations = {item["workerId"]: item for item in recovered["workers"]}
        assert observations[workers["healthy"]["id"]]["recovery"] == "reconnected"
        assert state.get_worker(workers["healthy"]["id"]).status == "ready"
        assert state.get_worker(workers["missing"]["id"]).status == "lost"
        assert state.get_worker(workers["socket_absent"]["id"]).status == "outcome_unknown"
        assert state.get_worker(workers["version_bad"]["id"]).status == "outcome_unknown"
        assert observations[workers["version_bad"]["id"]]["errorCode"] == "incompatible_protocol"
        assert not stale_launch.exists()
        assert recovered["automaticReplay"] is False
        assert recovered["actionsNotPerformed"] == [
            "prompt",
            "integration",
            "worktreeOrBranchCleanup",
        ]
    finally:
        state.close()
