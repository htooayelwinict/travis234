from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from appv231.coding_agent.execution_backend import ExecutionBackend, TrustedLocalBackend
from appv231.coding_agent.processes.containment import ProcessTreeController
from appv231.coding_agent.processes.local import create_local_process_transport
from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import (
    ProcessLaunchRequest,
    ProcessOwner,
    ProcessState,
)


def python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def request(
    command: str,
    cwd: Path,
    *,
    tty: bool = False,
    rows: int = 24,
    cols: int = 80,
    timeout: float | None = None,
) -> ProcessLaunchRequest:
    return ProcessLaunchRequest(
        command=command,
        cwd=str(cwd),
        env=dict(os.environ),
        shell_path="/bin/bash",
        tty=tty,
        rows=rows,
        cols=cols,
        timeout_seconds=timeout,
    )


def local_factory(backend: ExecutionBackend | None = None):
    selected = backend or TrustedLocalBackend()
    return lambda launch: create_local_process_transport(launch, selected)


def collect_until_terminal(
    service: ProcessSessionService,
    owner: ProcessOwner,
    snapshot,
    *,
    timeout: float = 5,
):
    output = snapshot.output
    cursor = snapshot.next_cursor
    current = snapshot
    deadline = time.monotonic() + timeout
    while not current.state.terminal and time.monotonic() < deadline:
        current = service.poll(owner, snapshot.session_id, cursor, wait_ms=250)
        output += current.output
        cursor = current.next_cursor
    assert current.state.terminal, f"process remained {current.state}"
    return current, output


def _process_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if Path("/proc/self/stat").exists():
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return False
        return state != "Z"
    return True


@pytest.fixture
def owner() -> ProcessOwner:
    return ProcessOwner("app-local", "/workspace", "agent")


@pytest.fixture
def service(tmp_path: Path):
    value = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.1,
        drain_timeout_seconds=0.5,
    )
    yield value
    value.close()


def test_execution_backend_preserves_defaults_and_honors_explicit_stdio(tmp_path: Path) -> None:
    backend = ExecutionBackend()
    stdin = object()
    stdout = object()
    stderr = object()
    process = object()
    with patch("appv231.coding_agent.execution_backend.subprocess.Popen", return_value=process) as popen:
        assert backend.spawn("true", str(tmp_path), {}, {"shell_path": "/bin/bash"}) is process
        defaults = popen.call_args.kwargs
        assert defaults["stdin"] is subprocess.DEVNULL
        assert defaults["stdout"] is subprocess.PIPE
        assert defaults["stderr"] is subprocess.PIPE

        backend.spawn(
            "true",
            str(tmp_path),
            {},
            {
                "shell_path": "/bin/bash",
                "stdin": stdin,
                "stdout": stdout,
                "stderr": stderr,
                "start_new_session": False,
            },
        )
        explicit = popen.call_args.kwargs
        assert explicit["stdin"] is stdin
        assert explicit["stdout"] is stdout
        assert explicit["stderr"] is stderr
        assert explicit["start_new_session"] is False


def test_pipe_transport_merges_stdout_stderr_and_reports_exit(service, owner, tmp_path: Path) -> None:
    launch = request(
        python_command("import sys; print('stdout'); print('stderr', file=sys.stderr)"),
        tmp_path,
    )

    started = service.start(owner, launch, local_factory(), yield_time_ms=2_000)

    assert started.state is ProcessState.EXITED
    assert started.exit_code == 0
    assert set(started.output.splitlines()) == {"stdout", "stderr"}


def test_pipe_transport_accepts_ordered_input_and_eof(service, owner, tmp_path: Path) -> None:
    launch = request(
        python_command("import sys; data=sys.stdin.read(); print('got=' + data.replace('\\n', '|'))"),
        tmp_path,
    )
    started = service.start(owner, launch, local_factory(), yield_time_ms=0)

    service.write(owner, started.session_id, "first\n", wait_ms=0)
    service.write(owner, started.session_id, "second\n", eof=True, wait_ms=0)
    terminal, output = collect_until_terminal(service, owner, started)

    assert terminal.exit_code == 0
    assert "got=first|second|" in output


def test_pipe_transport_spools_output_flood_without_tool_result_overflow(service, owner, tmp_path: Path) -> None:
    launch = request(python_command("print('x' * 200_000)"), tmp_path)

    started = service.start(owner, launch, local_factory(), yield_time_ms=2_000)

    assert started.state is ProcessState.EXITED
    assert len(started.output.encode("utf-8")) <= 51_200
    assert started.output_size == 200_001
    second = service.poll(owner, started.session_id, started.next_cursor, wait_ms=0)
    assert second.next_cursor > started.next_cursor


def test_real_timeout_terminates_process_group(service, owner, tmp_path: Path) -> None:
    launch = request(python_command("import time; time.sleep(30)"), tmp_path, timeout=0.05)

    started = service.start(owner, launch, local_factory(), yield_time_ms=0)
    terminal, _output = collect_until_terminal(service, owner, started)

    assert terminal.state is ProcessState.TIMED_OUT
    assert terminal.exit_code is not None


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_terminate_kills_descendant_in_same_process_group(service, owner, tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    source = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    started = service.start(owner, request(python_command(source), tmp_path), local_factory(), yield_time_ms=0)
    deadline = time.monotonic() + 2
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_pid = int(child_pid_path.read_text())

    service.terminate(owner, started.session_id, wait_ms=1_000)
    terminal, _output = collect_until_terminal(service, owner, started)

    assert terminal.state is ProcessState.TERMINATED
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not _process_is_live(child_pid):
            break
        time.sleep(0.01)
    else:
        os.kill(child_pid, signal.SIGKILL)
        pytest.fail("descendant survived process-group termination")


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_leader_exit_cleans_descendant_that_keeps_output_open(service, owner, tmp_path: Path) -> None:
    child_pid_path = tmp_path / "orphan.pid"
    source = (
        "import pathlib,subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid))"
    )
    started = service.start(owner, request(python_command(source), tmp_path), local_factory(), yield_time_ms=0)
    deadline = time.monotonic() + 2
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_pid = int(child_pid_path.read_text())
    try:
        terminal, _output = collect_until_terminal(service, owner, started)

        assert terminal.state is ProcessState.EXITED
        deadline = time.monotonic() + 2
        while _process_is_live(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _process_is_live(child_pid)
    finally:
        if _process_is_live(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.skipif(os.name != "posix", reason="v1 PTY is POSIX-only")
def test_pty_transport_reports_tty_and_accepts_input(service, owner, tmp_path: Path) -> None:
    source = "import os,sys; print(os.isatty(0), flush=True); print(sys.stdin.readline().strip(), flush=True)"
    launch = request(python_command(source), tmp_path, tty=True)
    started = service.start(owner, launch, local_factory(), yield_time_ms=0)

    service.write(owner, started.session_id, "hello\n", wait_ms=0)
    terminal, output = collect_until_terminal(service, owner, started)

    assert terminal.state is ProcessState.EXITED
    assert "True\n" in output
    assert "hello\n" in output


@pytest.mark.skipif(os.name != "posix", reason="v1 PTY is POSIX-only")
def test_pty_resize_updates_child_terminal_dimensions(service, owner, tmp_path: Path) -> None:
    source = "import os,time; time.sleep(.1); size=os.get_terminal_size(0); print(size.lines, size.columns)"
    launch = request(python_command(source), tmp_path, tty=True)
    started = service.start(owner, launch, local_factory(), yield_time_ms=0)

    service.resize(owner, started.session_id, rows=40, cols=120)
    terminal, output = collect_until_terminal(service, owner, started)

    assert terminal.state is ProcessState.EXITED
    assert "40 120" in output


def test_local_transport_rejects_missing_working_directory(tmp_path: Path) -> None:
    launch = request("true", tmp_path / "missing")

    with pytest.raises(RuntimeError, match="Working directory does not exist"):
        create_local_process_transport(launch, TrustedLocalBackend())


def test_local_transport_rejects_pty_on_non_posix(tmp_path: Path) -> None:
    launch = request("true", tmp_path, tty=True)

    with patch("appv231.coding_agent.processes.local.os.name", "nt"):
        with pytest.raises(RuntimeError, match="PTY mode requires POSIX"):
            create_local_process_transport(launch, TrustedLocalBackend())


@pytest.mark.skipif(os.name != "posix", reason="process-tree containment requires POSIX")
def test_timeout_kills_descendant_that_calls_setsid(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.1,
        drain_timeout_seconds=0.5,
    )
    pid_file = tmp_path / "escaped.pid"
    child = (
        "import os,pathlib,time; os.setsid(); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    )
    escaped_pid: int | None = None
    try:
        started = service.start(
            owner,
            request(python_command(parent), tmp_path, timeout=0.4),
            local_factory(),
            yield_time_ms=0,
        )
        deadline = time.monotonic() + 2
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        escaped_pid = int(pid_file.read_text())
        terminal, _output = collect_until_terminal(service, owner, started)

        assert terminal.state is ProcessState.TIMED_OUT
        deadline = time.monotonic() + 3
        while _process_is_live(escaped_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _process_is_live(escaped_pid)
    finally:
        service.close()
        if escaped_pid is not None and _process_is_live(escaped_pid):
            os.kill(escaped_pid, signal.SIGKILL)


def test_process_tree_skips_reused_pid(monkeypatch) -> None:
    sent: list[tuple[int, int]] = []

    class FakeProcess:
        def __init__(self, pid: int, created: float, *, children=(), parents=()) -> None:
            self.pid = pid
            self.created = created
            self.child_items = list(children)
            self.parent_items = list(parents)

        def create_time(self) -> float:
            return self.created

        def children(self, recursive: bool = False):
            return list(self.child_items)

        def parents(self):
            return list(self.parent_items)

        def send_signal(self, selected: int) -> None:
            sent.append((self.pid, selected))

    root = FakeProcess(1, 10.0)
    original_child = FakeProcess(2, 20.0, parents=[root])
    root.child_items = [original_child]
    processes = {1: root, 2: original_child}
    monkeypatch.setattr(
        "appv231.coding_agent.processes.containment.psutil.Process",
        lambda pid: processes[pid],
    )
    controller = ProcessTreeController(1)
    controller.refresh()

    root.child_items = []
    processes[2] = FakeProcess(2, 30.0)
    controller.signal("kill")

    assert all(pid != 2 for pid, _signal in sent)
