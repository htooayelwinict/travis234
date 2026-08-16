from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

import pytest

from travis.coding_agent.project_trust import (
    ProjectTrustStore,
    has_trust_requiring_project_resources,
)


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "travis/resources/skills/orchestration/scripts/orchestrate.py"


def load_helper():
    name = f"travis234_orchestration_relay_{secrets.token_hex(4)}"
    spec = importlib.util.spec_from_file_location(name, HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def initialize_repository(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Travis234 Relay Test")
    git(path, "config", "user.email", "relay-test@travis234.invalid")
    (path / "README.md").write_text("relay fixture\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


def install_fake_travis(bin_dir: Path) -> Path:
    bin_dir.mkdir()
    executable = bin_dir / "travis234"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "args = sys.argv[1:]\n"
        "cwd = args[args.index('--cwd') + 1]\n"
        "log = os.environ.get('FAKE_RPC_ARGS_LOG')\n"
        "if log:\n"
        "    Path(log).write_text(json.dumps(args), encoding='utf-8')\n"
        "stderr_value = os.environ.get('FAKE_RPC_STDERR')\n"
        "if stderr_value:\n"
        "    print(stderr_value, file=sys.stderr, flush=True)\n"
        "behavior = os.environ.get('FAKE_RPC_BEHAVIOR', 'ready')\n"
        "for raw in sys.stdin:\n"
        "    request = json.loads(raw)\n"
        "    request_id = request.get('id')\n"
        "    method = request.get('method')\n"
        "    if behavior == 'malformed':\n"
        "        print('not-json', flush=True)\n"
        "        behavior = 'ready'\n"
        "        continue\n"
        "    if method == 'get_state':\n"
        "        if behavior == 'delay':\n"
        "            time.sleep(5)\n"
        "        reported = str(Path(cwd).parent) if behavior == 'cwd-mismatch' else cwd\n"
        "        result = {'busy': False, 'sessionId': 'fake-session-1', 'cwd': reported, 'model': {'provider': 'fake', 'id': 'fake'}, 'thinkingLevel': 'medium', 'messageCount': 0}\n"
        "    elif method == 'prompt':\n"
        "        result = {'stopReason': 'stop', 'text': 'fake worker result'}\n"
        "    elif method == 'abort':\n"
        "        result = {'aborted': False}\n"
        "    elif method == 'close':\n"
        "        result = {'closed': True}\n"
        "    else:\n"
        "        print(json.dumps({'id': request_id, 'error': {'code': 'unknown_method', 'message': 'unknown'}}), flush=True)\n"
        "        continue\n"
        "    print(json.dumps({'id': request_id, 'result': result}, separators=(',', ':')), flush=True)\n"
        "    if method == 'close':\n"
        "        break\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


@pytest.fixture
def tmux_client():
    executable = shutil.which("tmux")
    if executable is None:
        pytest.skip("tmux is unavailable")
    server = f"travis234-pytest-{secrets.token_hex(6)}"
    module = load_helper()
    client = module.TmuxClient((executable, "-L", server))
    try:
        yield module, client
    finally:
        subprocess.run(
            [executable, "-L", server, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )


@pytest.fixture
def short_agent_dir():
    path = Path(tempfile.mkdtemp(prefix="t234-orch-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def seed_task(module, agent_dir: Path, repo: Path):
    os.environ["TRAVIS234_CODING_AGENT_DIR"] = str(agent_dir)
    state = module.StateStore.open()
    run = state.create_run({"objective": "relay run", "coordinatorSessionId": "coordinator"}, "run")
    task = state.create_task(
        run["run"]["runId"],
        {
            "objective": "relay task",
            "ownership": {"ownedPaths": []},
            "acceptanceCriteria": ["worker becomes ready"],
            "mode": "supervised",
            "commitPolicy": "no_commit",
        },
        "task",
    )
    request = module.WorkerStartRequest(repository=repo, workspace_mode="current")
    return state, task["task"]["taskId"], request


def close_worker(module, client, worker) -> None:
    try:
        module.RelayClient(Path(worker.socket_path)).request("close", timeout=3)
    except Exception:
        pass
    for _ in range(30):
        if not client.has_session(worker.tmux_session):
            break
        import time

        time.sleep(0.05)


def test_worker_identity_and_socket_bounds_are_stable(
    tmp_path: Path,
    short_agent_dir: Path,
) -> None:
    module = load_helper()
    worker_id = "worker_0123456789abcdef01234567"
    digest = module.worker_digest(worker_id)
    root = short_agent_dir / "orchestration"

    assert module.tmux_name(worker_id) == f"travis234-orch-{digest[:16]}"
    path = module.socket_path(root, worker_id)
    assert path.name == f"{digest[:24]}.sock"
    assert len(str(path).encode()) < module.MAX_UNIX_SOCKET_PATH_BYTES

    long_root = tmp_path / ("a" * 90) / "orchestration"
    with pytest.raises(module.HelperError) as raised:
        module.socket_path(long_root, worker_id)
    assert raised.value.code == "socket_path_too_long"
    assert not long_root.exists()


def test_mirrored_trust_reader_matches_canonical_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    agent_dir = home / ".travis234" / "agent"
    project = home / "projects" / "repo"
    project.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    assert module.read_project_trust_entry(project) is None
    assert ProjectTrustStore(agent_dir).get(project) is None
    assert module.has_trust_requiring_project_resources_mirror(project) is False
    assert has_trust_requiring_project_resources(project) is False

    (project / ".travis234" / "skills").mkdir(parents=True)
    assert module.has_trust_requiring_project_resources_mirror(project) is True
    assert has_trust_requiring_project_resources(project) is True

    (agent_dir / "trust.json").write_text(
        json.dumps({str(project.parent.resolve()): False}), encoding="utf-8"
    )
    assert module.read_project_trust_entry(project) is False
    assert ProjectTrustStore(agent_dir).get(project) is False

    (agent_dir / "trust.json").write_text("[]", encoding="utf-8")
    with pytest.raises(module.HelperError) as raised:
        module.read_project_trust_entry(project)
    assert raised.value.code == "invalid_trust_store"


def test_real_tmux_relay_identity_readiness_and_reconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_client,
    short_agent_dir: Path,
) -> None:
    module, client = tmux_client
    repo = initialize_repository(tmp_path / "repo with spaces;safe")
    fake = install_fake_travis(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    stderr_secret = "sk-proj-this-stderr-value-must-not-persist"
    monkeypatch.setenv("FAKE_RPC_STDERR", stderr_secret)
    agent_dir = short_agent_dir
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    state, task_id, request = seed_task(module, agent_dir, repo)
    try:
        worker = module.start_worker(
            state,
            task_id,
            request,
            "ready-worker",
            tmux_client=client,
            readiness_timeout=5,
        )
        digest = module.worker_digest(worker.worker_id)
        assert worker.tmux_session == f"travis234-orch-{digest[:16]}"
        assert Path(worker.socket_path).name == f"{digest[:24]}.sock"
        assert len(worker.socket_path.encode()) < 100
        assert stat.S_IMODE(Path(worker.socket_path).stat().st_mode) == 0o600
        assert worker.travis_session_id == "fake-session-1"
        assert worker.status == "ready"
        assert client.has_session(worker.tmux_session)

        first = module.RelayClient(Path(worker.socket_path))
        second = module.RelayClient(Path(worker.socket_path))
        assert first.request("health", timeout=2)["status"] == "ready"
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(2)
        try:
            connection.connect(worker.socket_path)
            module._send_frame(
                connection,
                {
                    "protocolVersion": 999,
                    "requestId": "incompatible-version",
                    "action": "close",
                    "params": {},
                },
            )
            incompatible = module._receive_frame(connection)
        finally:
            connection.close()
        assert incompatible["ok"] is False
        assert incompatible["error"]["code"] == "incompatible_protocol"
        assert first.request("health", timeout=2)["status"] == "ready"
        capability = f"dispatch-capability-{secrets.token_hex(24)}"
        assert first.request(
            "configure_dispatch",
            {"capability": capability},
            timeout=2,
        ) == {"configured": True}
        assert first.request("prompt", {"text": "fake prompt"}, timeout=2) == {
            "stopReason": "stop",
            "text": "fake worker result",
        }
        state_result = second.request("state", timeout=2)
        assert Path(state_result["cwd"]).resolve() == repo.resolve()
        assert state_result["sessionId"] == "fake-session-1"
        assert not (agent_dir / "orchestration" / "runs" / worker.run_id / "workers" / worker.worker_id / "launch.json").exists()
        stderr_log = (
            agent_dir
            / "orchestration"
            / "runs"
            / worker.run_id
            / "workers"
            / worker.worker_id
            / "stderr.jsonl"
        )
        stderr_body = stderr_log.read_text(encoding="utf-8")
        assert stderr_secret not in stderr_body
        assert '"contentOmitted":true' in stderr_body
        assert all(
            capability not in path.read_text(encoding="utf-8", errors="replace")
            for path in (agent_dir / "orchestration").rglob("*")
            if path.is_file()
        )
        assert all(
            stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
            for path in (agent_dir / "orchestration").rglob("*")
        )
    finally:
        if "worker" in locals():
            close_worker(module, client, worker)
        state.close()


@pytest.mark.parametrize(
    ("behavior", "expected_code"),
    [
        ("malformed", "worker_start_failed"),
        ("cwd-mismatch", "worker_start_failed"),
        ("delay", "worker_start_timeout"),
    ],
)
def test_relay_startup_failures_are_bounded_and_never_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_client,
    behavior: str,
    expected_code: str,
    short_agent_dir: Path,
) -> None:
    module, client = tmux_client
    repo = initialize_repository(tmp_path / "repo")
    fake = install_fake_travis(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_RPC_BEHAVIOR", behavior)
    agent_dir = short_agent_dir
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    state, task_id, request = seed_task(module, agent_dir, repo)
    before_rows = state.connection.execute("SELECT count(*) FROM workers").fetchone()[0]
    try:
        with pytest.raises(module.HelperError) as raised:
            module.start_worker(
                state,
                task_id,
                request,
                f"failure-{behavior}",
                tmux_client=client,
                readiness_timeout=0.4,
            )
        assert raised.value.code == expected_code
        rows = state.connection.execute(
            "SELECT worker_id, tmux_session, status FROM workers ORDER BY created_at"
        ).fetchall()
        assert len(rows) == before_rows + 1
        assert rows[-1]["status"] in {"stopped", "outcome_unknown"}
        assert state.connection.execute("SELECT count(*) FROM workers").fetchone()[0] == 1
    finally:
        state.close()


def test_missing_tmux_and_rpc_executable_fail_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_client,
    short_agent_dir: Path,
) -> None:
    module, client = tmux_client
    repo = initialize_repository(tmp_path / "repo")
    agent_dir = short_agent_dir
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    state, task_id, request = seed_task(module, agent_dir, repo)
    try:
        with pytest.raises(module.HelperError) as tmux_error:
            module.start_worker(
                state,
                task_id,
                request,
                "missing-tmux",
                tmux_client=module.TmuxClient((str(tmp_path / "missing-tmux"),)),
            )
        assert tmux_error.value.code == "tmux_unavailable"
        assert state.connection.execute("SELECT count(*) FROM workers").fetchone()[0] == 0

        git_executable = shutil.which("git")
        assert git_executable is not None
        monkeypatch.setenv("PATH", str(Path(git_executable).parent))
        with pytest.raises(module.HelperError) as rpc_error:
            module.start_worker(
                state,
                task_id,
                request,
                "missing-rpc",
                tmux_client=client,
                readiness_timeout=2,
            )
        assert rpc_error.value.code == "worker_start_failed"
    finally:
        state.close()


def test_worker_trust_decisions_gate_launch_and_never_add_approve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_client,
    short_agent_dir: Path,
) -> None:
    module, client = tmux_client
    repo = initialize_repository(tmp_path / "repo")
    (repo / ".travis234").mkdir()
    (repo / ".travis234" / "settings.json").write_text("{}\n", encoding="utf-8")
    fake = install_fake_travis(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    args_log = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_RPC_ARGS_LOG", str(args_log))
    agent_dir = short_agent_dir
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    state, task_id, request = seed_task(module, agent_dir, repo)
    try:
        with pytest.raises(module.HelperError) as unresolved:
            module.start_worker(
                state,
                task_id,
                request,
                "trust-unresolved",
                tmux_client=client,
            )
        assert unresolved.value.code == "trust_required"
        assert state.connection.execute("SELECT count(*) FROM workers").fetchone()[0] == 0

        (agent_dir / "trust.json").write_text(
            json.dumps({str(repo.resolve()): False}), encoding="utf-8"
        )
        worker = module.start_worker(
            state,
            task_id,
            request,
            "trust-denied",
            tmux_client=client,
            readiness_timeout=5,
        )
        arguments = json.loads(args_log.read_text(encoding="utf-8"))
        assert "--no-approve" in arguments
        assert "--approve" not in arguments
    finally:
        if "worker" in locals():
            close_worker(module, client, worker)
        state.close()


def test_worker_limits_accept_two_default_three_explicit_and_never_four(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_client,
    short_agent_dir: Path,
) -> None:
    module, client = tmux_client
    repo = initialize_repository(tmp_path / "repo")
    fake = install_fake_travis(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    agent_dir = short_agent_dir
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    state, task_id, request = seed_task(module, agent_dir, repo)
    workers = []
    try:
        for number in (1, 2):
            workers.append(
                module.start_worker(
                    state,
                    task_id,
                    request,
                    f"worker-{number}",
                    tmux_client=client,
                    readiness_timeout=5,
                )
            )
        with pytest.raises(module.HelperError) as third_default:
            module.start_worker(
                state,
                task_id,
                request,
                "worker-3-default",
                tmux_client=client,
            )
        assert third_default.value.code == "worker_limit"
        workers.append(
            module.start_worker(
                state,
                task_id,
                request,
                "worker-3-explicit",
                tmux_client=client,
                max_workers=3,
                readiness_timeout=5,
            )
        )
        with pytest.raises(module.HelperError) as fourth:
            module.start_worker(
                state,
                task_id,
                request,
                "worker-4",
                tmux_client=client,
                max_workers=3,
            )
        assert fourth.value.code == "worker_limit"
        assert len({worker.worker_id for worker in workers}) == 3
    finally:
        for worker in workers:
            close_worker(module, client, worker)
        state.close()
