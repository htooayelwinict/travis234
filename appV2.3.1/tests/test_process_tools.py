from __future__ import annotations

import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from appv231.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv231.coding_agent.agent_session import AgentSession
from appv231.coding_agent.artifacts import ArtifactRegistry
from appv231.coding_agent.execution_backend import TrustedLocalBackend
from appv231.coding_agent.processes.completions import ProcessCompletionStore
from appv231.coding_agent.processes.local import create_local_process_transport
from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import (
    ProcessLaunchRequest,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from appv231.coding_agent.tools.bash import BashOperations, create_bash_tool, create_bash_tool_definition
from appv231.coding_agent.tools.process import create_process_tool, create_process_tool_definition
from appv231.coding_agent.tools.truncate import truncate_tail


def python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def collect(service, owner, process_tool, started, timeout: float = 5):
    output = text(started)
    cursor = started.details["nextCursor"]
    deadline = time.monotonic() + timeout
    current = started
    while current.details["status"] not in {"exited", "timed_out", "terminated", "failed"}:
        assert time.monotonic() < deadline
        current = process_tool.execute(
            "poll",
            {
                "action": "poll",
                "session_id": started.details["sessionId"],
                "cursor": cursor,
                "yield_time_ms": 250,
            },
        )
        output += text(current)
        cursor = current.details["nextCursor"]
    return current, output


@pytest.fixture
def managed_tools(tmp_path: Path):
    store = ProcessCompletionStore(tmp_path / ".completions")
    service = ProcessSessionService(
        directory=tmp_path / ".processes",
        completion_store=store,
        termination_grace_seconds=0.05,
    )
    artifacts = ArtifactRegistry()
    owner = ProcessOwner("app-tools", str(tmp_path.resolve()), "agent")
    backend = TrustedLocalBackend()
    factory = lambda request: create_local_process_transport(request, backend)
    bash = create_bash_tool(
        str(tmp_path),
        artifacts=artifacts,
        process_service=service,
        process_owner=owner,
        transport_factory=factory,
    )
    process = create_process_tool(service, owner, artifacts)
    yield service, owner, bash, process
    service.close()
    artifacts.close(remove_files=True)
    store.close()


def test_managed_bash_default_yield_is_independent_from_timeout(tmp_path: Path) -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.yield_time_ms = None
            self.request = None

        def start(self, owner, request, transport_factory, *, yield_time_ms, signal=None, on_update=None):
            self.yield_time_ms = yield_time_ms
            self.request = request
            return ProcessSnapshot(
                session_id="proc_recorded",
                state=ProcessState.EXITED,
                output="ok",
                cursor=0,
                next_cursor=2,
                output_size=2,
                exit_code=0,
                tty=False,
                elapsed_ms=5,
            )

        def tail_snapshot(self, owner, session_id):
            return truncate_tail("ok")

    service = RecordingService()
    owner = ProcessOwner("app", str(tmp_path), "agent")
    definition = create_bash_tool_definition(
        str(tmp_path),
        process_service=service,
        process_owner=owner,
        transport_factory=lambda _request: None,
        launch_session_id="session-abc",
    )

    result = definition.execute("call", {"command": "true", "timeout": 600})

    assert text(result) == "ok"
    assert service.yield_time_ms == 10_000
    assert service.request.timeout_seconds == 600
    assert service.request.launch_session_id == "session-abc"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rows", 1),
        ("rows", True),
        ("cols", 19),
        ("cols", 501),
    ],
)
def test_managed_bash_validates_pty_dimensions_before_start(tmp_path: Path, field: str, value) -> None:
    class NoStartService:
        def start(self, *args, **kwargs):
            pytest.fail("invalid PTY dimensions must not start a process")

    definition = create_bash_tool_definition(
        str(tmp_path),
        process_service=NoStartService(),
        process_owner=ProcessOwner("app", str(tmp_path), "agent"),
        transport_factory=lambda _request: None,
    )

    with pytest.raises(ValueError, match=field):
        definition.execute("call", {"command": "true", "tty": True, field: value})


def test_managed_bash_yields_handle_then_process_poll_collects_completion(managed_tools) -> None:
    service, owner, bash, process = managed_tools
    command = python_command("import time; print('START', flush=True); time.sleep(.2); print('DONE')")

    started = bash.execute("bash", {"command": command, "yield_time_ms": 50})
    terminal, output = collect(service, owner, process, started)

    assert started.details["status"] == "running"
    assert started.details["sessionId"].startswith("proc_")
    assert "START" in output
    assert "DONE" in output
    assert terminal.details["status"] == "exited"
    assert terminal.details["exitCode"] == 0
    assert f"next cursor {terminal.details['nextCursor']}" in text(terminal)
    assert f"output size {terminal.details['outputSize']}" in text(terminal)


def test_running_process_results_expose_suggested_poll_delay(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {"command": python_command("import time; time.sleep(1)"), "yield_time_ms": 0},
    )

    polled = process.execute(
        "poll",
        {
            "action": "poll",
            "session_id": started.details["sessionId"],
            "cursor": started.details["nextCursor"],
            "yield_time_ms": 0,
        },
    )

    assert started.details["status"] == "running"
    assert polled.details["status"] == "running"
    assert "Suggested poll delay: 1000 ms." in text(started)
    assert "Suggested poll delay: 1000 ms." in text(polled)


def test_managed_bash_streams_sanitized_updates_before_handoff(managed_tools) -> None:
    _service, _owner, bash, _process = managed_tools
    updates = []
    command = python_command("import time; print('EARLY', flush=True); time.sleep(1)")

    result = bash.execute(
        "bash",
        {"command": command, "yield_time_ms": 500},
        on_update=updates.append,
    )

    assert result.details["status"] == "running"
    assert updates[0].content == []
    assert any("EARLY" in text(update) for update in updates[1:])


def test_managed_bash_preserves_fast_nonzero_error(managed_tools) -> None:
    _service, _owner, bash, _process = managed_tools

    with pytest.raises(RuntimeError, match="Command exited with code 7"):
        bash.execute("bash", {"command": python_command("raise SystemExit(7)"), "yield_time_ms": 2_000})


def test_managed_bash_preserves_tail_truncation_and_exports_independent_artifact(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    registry = ArtifactRegistry()
    backend = TrustedLocalBackend()
    tool = create_bash_tool(
        str(tmp_path),
        artifacts=registry,
        process_service=service,
        process_owner=owner,
        transport_factory=lambda launch: create_local_process_transport(launch, backend),
    )
    try:
        result = tool.execute(
            "bash",
            {
                "command": python_command("print('x' * 70000); print('FINAL-MARKER')"),
                "yield_time_ms": 2_000,
            },
        )

        assert "FINAL-MARKER" in text(result)
        assert result.details["truncation"]["truncated"] is True
        assert result.details["fullOutputPath"]
        assert result.details["artifactId"].startswith("artifact-")
        full_output = Path(result.details["fullOutputPath"])
        assert full_output.read_text(encoding="utf-8").endswith("FINAL-MARKER\n")
        service.close()
        assert full_output.exists()
    finally:
        service.close()
        registry.close(remove_files=True)


def test_detached_nonzero_is_successful_process_observation(managed_tools) -> None:
    service, owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {"command": python_command("import time; time.sleep(.05); raise SystemExit(3)"), "yield_time_ms": 0},
    )

    terminal, _output = collect(service, owner, process, started)

    assert terminal.details["status"] == "exited"
    assert terminal.details["exitCode"] == 3


def test_process_tool_validates_action_specific_arguments_and_hides_stdin(managed_tools) -> None:
    service, owner, _bash, process = managed_tools
    definition = create_process_tool_definition(service, owner)

    with pytest.raises(ValueError, match="poll requires session_id"):
        process.execute("p", {"action": "poll", "cursor": 0})
    with pytest.raises(ValueError, match="cursor must be a nonnegative integer"):
        process.execute("p", {"action": "poll", "session_id": "proc_x", "cursor": -1})
    with pytest.raises(ValueError, match="poll does not accept wait_time_ms"):
        process.execute(
            "p",
            {
                "action": "poll",
                "session_id": "proc_x",
                "cursor": 0,
                "yield_time_ms": 0,
                "wait_time_ms": 1_000,
            },
        )
    with pytest.raises(ValueError, match="write does not accept cursor"):
        process.execute(
            "p",
            {"action": "write", "session_id": "proc_x", "input": "secret", "cursor": 0},
        )
    rendered = definition.render_call(
        {"action": "write", "session_id": "proc_0123456789", "input": "never render me"}
    )
    assert rendered == "process write proc_01234567"
    assert "never render me" not in rendered


def test_process_list_is_scoped_and_bounds_displayed_command(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    long_command = python_command("import time; time.sleep(.5)") + " # " + "x" * 300
    started = bash.execute("bash", {"command": long_command, "yield_time_ms": 0})

    result = process.execute("list", {"action": "list"})

    assert started.details["sessionId"] in text(result)
    assert len(result.details["processes"][0]["command"]) == 200
    assert "pid" not in str(result.details).lower()


def test_custom_bash_operations_remain_synchronous_with_managed_options(tmp_path: Path) -> None:
    calls = []

    def execute(command, cwd, options):
        calls.append((command, cwd, options.timeout))
        options.on_data(b"legacy")
        return {"exit_code": 0}

    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path), "agent")
    try:
        tool = create_bash_tool(
            str(tmp_path),
            operations=BashOperations(exec=execute),
            process_service=service,
            process_owner=owner,
            transport_factory=lambda _request: pytest.fail("managed factory must not run"),
        )

        result = tool.execute("bash", {"command": "legacy", "yield_time_ms": 0, "timeout": 9})

        assert text(result) == "legacy"
        assert calls == [("legacy", str(tmp_path), 9)]
        assert service.list(owner) == ()
    finally:
        service.close()


def test_agent_session_exposes_process_only_when_service_is_injected(tmp_path: Path) -> None:
    plain = AgentSession(cwd=str(tmp_path), model=faux_model())
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    managed = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=owner,
    )
    try:
        assert plain.get_tool_definition("process") is None
        assert "process" not in plain.get_active_tool_names()
        assert managed.get_tool_definition("process") is not None
        assert managed.get_active_tool_names()[:5] == ["read", "bash", "process", "edit", "write"]
    finally:
        plain.shutdown()
        managed.shutdown()
        service.close()


def test_agent_session_prompt_keeps_required_managed_process_work_pending(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=owner,
    )
    try:
        assert "Use process.poll only for interactive input, quick status checks" in session.system_prompt
        assert "continue independent work first and then use process.wait" in session.system_prompt
        assert "wait ignores output-only wakeups and does not set the command timeout" in session.system_prompt
        assert "do not call process.poll before process.wait" in session.system_prompt
        assert "Do not repeat unchanged file reads around process checks" in session.system_prompt
        assert "Leave a process detached only for a requested server/watcher" in session.system_prompt
    finally:
        session.shutdown()
        service.close()


def test_agent_loop_continues_after_bash_yields_without_waiting_for_exit(tmp_path: Path) -> None:
    model = faux_model()
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    calls = {"count": 0}
    command = python_command("import time; time.sleep(.5); print('late')")

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        events = (
            tool_call_response_events(
                active_model,
                "bash",
                {"command": command, "yield_time_ms": 0},
                call_id="managed-bash",
            )
            if calls["count"] == 1
            else text_response_events(active_model, "I continued while the process was running.")
        )
        return create_faux_provider(lambda _model, _context: events).stream_simple(
            active_model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        process_service=service,
        process_owner=owner,
    )
    try:
        messages = session.prompt("start it and continue", stream_fn=stream_fn)
        jobs = service.list(owner)

        assert calls["count"] == 2
        assert messages[-1].content[0].text == "I continued while the process was running."
        assert len(jobs) == 1
        assert jobs[0].state is ProcessState.RUNNING
        tool_result = next(message for message in session.messages if getattr(message, "role", None) == "toolResult")
        assert tool_result.details["status"] == "running"
    finally:
        session.shutdown()
        service.close()


def test_process_wait_uses_terminal_wait_streams_updates_and_is_sequential(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command(
                "import time; print('progress', flush=True); time.sleep(.1); print('done')"
            ),
            "yield_time_ms": 0,
        },
    )
    updates = []

    result = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": started.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
        on_update=updates.append,
    )

    assert result.details["status"] == "exited"
    assert result.details["durableOutput"] is True
    assert result.details["nextCursor"] == result.details["outputSize"]
    assert "done" in text(result)
    assert updates
    assert process.execution_mode == "sequential"


def test_process_normalizes_poll_with_wait_deadline_to_terminal_wait(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command("import time; time.sleep(.05); print('done')"),
            "yield_time_ms": 0,
        },
    )

    result = process.execute(
        "wait",
        {
            "action": "poll",
            "session_id": started.details["sessionId"],
            "cursor": started.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
    )

    assert result.details["status"] == "exited"
    assert "done" in text(result)


@pytest.mark.parametrize("wait_time_ms", [999, 900_001, True])
def test_process_wait_validates_host_deadline(managed_tools, wait_time_ms) -> None:
    service, owner, _bash, _process = managed_tools
    definition = create_process_tool_definition(service, owner)

    with pytest.raises(ValueError, match="wait_time_ms"):
        definition.execute(
            "wait",
            {
                "action": "wait",
                "session_id": "proc_" + "a" * 32,
                "cursor": 0,
                "wait_time_ms": wait_time_ms,
            },
        )


def test_process_wait_collapses_large_output_to_bounded_borrowed_artifact(tmp_path: Path) -> None:
    store = ProcessCompletionStore(tmp_path / "completions")
    service = ProcessSessionService(directory=tmp_path / "processes", completion_store=store)
    artifacts = ArtifactRegistry()
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    backend = TrustedLocalBackend()
    bash = create_bash_tool(
        str(tmp_path),
        artifacts=artifacts,
        process_service=service,
        process_owner=owner,
        transport_factory=lambda launch: create_local_process_transport(launch, backend),
    )
    process = create_process_tool(service, owner, artifacts)
    try:
        started = bash.execute(
            "bash",
            {"command": python_command("print('x' * (2 * 1024 * 1024))"), "yield_time_ms": 0},
        )
        result = process.execute(
            "wait",
            {
                "action": "wait",
                "session_id": started.details["sessionId"],
                "cursor": started.details["nextCursor"],
                "wait_time_ms": 60_000,
            },
        )
        full_output = Path(result.details["fullOutputPath"])

        assert result.details["nextCursor"] == result.details["outputSize"]
        assert len(text(result).encode("utf-8")) < 60_000
        assert result.details["truncation"]["truncated"] is True
        assert artifacts.resolve_read(result.details["artifactId"]) == full_output
        assert full_output.stat().st_size == 2 * 1024 * 1024 + 1
        artifacts.close(remove_files=True)
        assert full_output.exists()
    finally:
        service.close()
        artifacts.close(remove_files=True)
        store.close()


def test_agent_uses_one_wait_call_despite_multiple_process_updates(tmp_path: Path) -> None:
    store = ProcessCompletionStore(tmp_path / "completions")
    service = ProcessSessionService(directory=tmp_path / "processes", completion_store=store)
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    model = faux_model()
    calls = {"count": 0}
    command = python_command(
        "import time; print('one', flush=True); time.sleep(.1); "
        "print('two', flush=True); time.sleep(.1); print('three')"
    )

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {"command": command, "yield_time_ms": 0},
                call_id="bash-call",
            )
        elif calls["count"] == 2:
            process_id = service.list(owner)[0].session_id
            events = tool_call_response_events(
                active_model,
                "process",
                {
                    "action": "wait",
                    "session_id": process_id,
                    "cursor": 0,
                    "wait_time_ms": 60_000,
                },
                call_id="wait-call",
            )
        else:
            events = text_response_events(active_model, "completed")
        return create_faux_provider(lambda _model, _context: events).stream_simple(
            active_model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        process_service=service,
        process_owner=owner,
    )
    try:
        messages = session.prompt("run the job and wait for its result", stream_fn=stream_fn)
        process_results = [
            message
            for message in session.messages
            if getattr(message, "role", None) == "toolResult"
            and getattr(message, "tool_name", None) == "process"
        ]

        assert calls["count"] == 3
        assert len(process_results) == 1
        assert process_results[0].details["status"] == "exited"
        assert messages[-1].content[0].text == "completed"
    finally:
        session.shutdown()
        service.close()
        store.close()
