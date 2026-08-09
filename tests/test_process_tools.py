from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from travis.ai.types import AssistantMessage, ToolCall
from travis.ai.validation import ToolValidationError, compile_tool_schema, validate_tool_arguments
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.execution_backend import TrustedLocalBackend
from travis.coding_agent.processes.completions import ProcessCompletionStore
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import (
    ProcessLaunchRequest,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from travis.coding_agent.tools import process as process_tool_module
from travis.coding_agent.tools.bash import BashOperations, create_bash_tool, create_bash_tool_definition
from travis.coding_agent.tools.process import PROCESS_SCHEMA, create_process_tool, create_process_tool_definition
from travis.coding_agent.tools.truncate import truncate_tail


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


def test_process_schema_is_flat_provider_friendly_and_compact() -> None:
    assert PROCESS_SCHEMA["type"] == "object"
    assert "oneOf" not in PROCESS_SCHEMA
    assert "anyOf" not in PROCESS_SCHEMA
    assert PROCESS_SCHEMA["required"] == ["action"]
    assert PROCESS_SCHEMA["additionalProperties"] is False
    assert PROCESS_SCHEMA["properties"]["action"]["enum"] == list(process_tool_module.PROCESS_ACTIONS)
    assert set(PROCESS_SCHEMA["properties"]) == {
        "action",
        "session_id",
        "cursor",
        "input",
        "eof",
        "yield_time_ms",
        "wait_time_ms",
        "max_bytes",
        "rows",
        "cols",
    }
    compact = json.dumps(PROCESS_SCHEMA, separators=(",", ":"), sort_keys=True)
    assert len(compact) <= 1_600


def test_process_flat_schema_rejects_only_universally_invalid_shapes() -> None:
    schema = compile_tool_schema(PROCESS_SCHEMA)

    assert schema.errors({})
    assert schema.errors({"action": "unknown"})
    assert schema.errors({"action": "list", "unknown": True})
    assert not schema.errors({"action": "list"})
    assert not schema.errors({"action": "wait"})
    assert not schema.errors({"action": "write", "session_id": "proc_x"})


def test_process_schema_accepts_every_documented_complete_action() -> None:
    schema = compile_tool_schema(PROCESS_SCHEMA)
    valid = [
        {"action": "poll", "session_id": "proc_x", "cursor": 0, "yield_time_ms": 1_000},
        {"action": "wait", "session_id": "proc_x", "cursor": 4, "wait_time_ms": 60_000},
        {"action": "write", "session_id": "proc_x", "input": "yes", "eof": False},
        {"action": "write_raw", "session_id": "proc_x", "input": "yes\n", "eof": False},
        {"action": "resize", "session_id": "proc_x", "rows": 24, "cols": 80},
        {"action": "interrupt", "session_id": "proc_x", "yield_time_ms": 1_000},
        {"action": "terminate", "session_id": "proc_x", "yield_time_ms": 2_000},
        {"action": "kill", "session_id": "proc_x"},
        {"action": "list"},
    ]

    assert all(not schema.errors(arguments) for arguments in valid)


def test_process_prompt_metadata_is_compact_and_contains_no_placeholder_calls(managed_tools) -> None:
    service, owner, _bash, _process = managed_tools
    definition = create_process_tool_definition(service, owner)
    metadata = "\n".join([definition.prompt_snippet or "", *definition.prompt_guidelines])

    assert len(definition.prompt_guidelines) <= 7
    assert len(metadata) <= 1_050
    assert "<nextCursor>" not in metadata
    assert '"session_id":"<id>"' not in metadata
    assert "exact nextCursor" in metadata
    assert "write_raw" in metadata
    assert "actual execution deadline" in metadata


def test_readme_documents_action_based_process_handoff() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert '"action":"write"' in readme
    assert '"action":"wait"' in readme
    assert "real session ID" in readme
    assert "nextCursor returned by the write" in readme
    assert "PTY only for terminal interaction" in readme


def test_managed_process_routing_separates_background_work_from_pty_allocation(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=owner,
        agent_dir=str(tmp_path / "agent"),
    )
    try:
        assert "Use managed `bash` plus `process` for commands that remain running" in session.system_prompt
        assert "Set `tty=true` only for terminal interaction" in session.system_prompt
        assert "PTY plus `process` when a command requires interactive input or incremental output" not in session.system_prompt
        assert '"session_id":"<id>"' not in session.system_prompt
        assert "Use `tmux` for servers, watchers, REPLs" in session.system_prompt
    finally:
        session.shutdown()
        service.close()


def test_empty_process_call_reports_the_required_action_without_repair() -> None:
    tool = create_process_tool(None, None)
    call = ToolCall(id="empty-process", name="process", arguments={})

    assert tool.prepare_arguments({}) == {}
    with pytest.raises(ToolValidationError, match=r"process: missing required property 'action'"):
        validate_tool_arguments(tool, call)
    assert call.arguments == {}


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"action": "write", "session_id": "proc_x"}, r"write requires input.*\"action\":\"write\""),
        ({"action": "write_raw", "session_id": "proc_x"}, r"write_raw requires input.*\"action\":\"write_raw\""),
        ({"action": "resize", "session_id": "proc_x", "cols": 80}, r"resize requires rows.*\"rows\":24"),
        ({"action": "kill"}, r"kill requires session_id.*\"action\":\"kill\""),
    ],
)
def test_process_runtime_errors_include_the_relevant_action_shape(arguments, expected) -> None:
    definition = create_process_tool_definition(None, None)

    with pytest.raises(ValueError, match=expected):
        definition.execute("invalid-process", arguments)


def test_process_preparation_never_invents_identity_or_cursor() -> None:
    assert process_tool_module.prepare_process_arguments({"action": "write", "input": "yes"}) == {
        "action": "write",
        "input": "yes",
    }
    with pytest.raises(ValueError, match=r"cursor must be a nonnegative integer.*tool process"):
        process_tool_module.prepare_process_arguments(
            {"action": "wait", "session_id": "proc_x"}
        )


def test_process_start_error_routes_model_to_bash_launcher() -> None:
    with pytest.raises(ValueError, match="has no start action.*bash"):
        process_tool_module.prepare_process_arguments(
            {"action": "start", "command": "python3 producer.py"}
        )


def test_process_argument_preparation_normalizes_wait_modes_without_inventing_cursor() -> None:
    wait_with_yield = {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 4,
        "yield_time_ms": 30_000,
    }

    assert process_tool_module.prepare_process_arguments(wait_with_yield) == {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 4,
        "wait_time_ms": 30_000,
    }
    assert wait_with_yield == {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 4,
        "wait_time_ms": 30_000,
    }
    assert process_tool_module.prepare_process_arguments(
        {
            "action": "poll",
            "session_id": "proc_x",
            "cursor": 4,
            "yield_time_ms": 1_000,
            "wait_time_ms": 60_000,
        }
    ) == {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 4,
        "wait_time_ms": 60_000,
    }
def test_process_argument_preparation_rejects_ambiguous_wait_timing() -> None:
    with pytest.raises(ValueError, match="wait action received both wait_time_ms and yield_time_ms"):
        process_tool_module.prepare_process_arguments(
            {
                "action": "wait",
                "session_id": "proc_x",
                "cursor": 4,
                "wait_time_ms": 60_000,
                "yield_time_ms": 1_000,
            }
        )


def test_process_argument_preparation_recovers_collapsed_wait_fields_and_id() -> None:
    arguments = {
        "action": "wait",
        "cursor": 17,
        "sessionid": "proc8e355b88e4fad64f5a9bd8c1e9cbc284",
        "waittimems": 120_000,
    }

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "wait",
        "cursor": 17,
        "session_id": "proc_8e355b88e4fad64f5a9bd8c1e9cbc284",
        "wait_time_ms": 60_000,
    }


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            {"action": "poll", "sessionId": "proc_x", "cursor": 0},
            {"action": "poll", "session_id": "proc_x", "cursor": 0},
        ),
        (
            {"action": "poll", "session_id": "proc_x", "nextCursor": 4},
            {"action": "poll", "session_id": "proc_x", "cursor": 4},
        ),
        (
            {"action": "poll", "session_id": "proc_x", "cursor": 0, "yieldTimeMs": 1_000},
            {"action": "poll", "session_id": "proc_x", "cursor": 0, "yield_time_ms": 1_000},
        ),
        (
            {"action": "wait", "session_id": "proc_x", "cursor": 0, "waitTimeMs": 1_000},
            {"action": "wait", "session_id": "proc_x", "cursor": 0, "wait_time_ms": 1_000},
        ),
        (
            {"action": "poll", "session_id": "proc_x", "cursor": 0, "maxBytes": 1_024},
            {"action": "poll", "session_id": "proc_x", "cursor": 0, "max_bytes": 1_024},
        ),
    ],
)
def test_process_argument_preparation_recovers_compatibility_fields(arguments, expected) -> None:
    assert process_tool_module.prepare_process_arguments(arguments) == expected


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("session_id", "sessionid"),
        ("cursor", "nextCursor"),
        ("yield_time_ms", "yieldTimeMs"),
        ("wait_time_ms", "waittimems"),
        ("max_bytes", "maxBytes"),
    ],
)
def test_process_argument_preparation_rejects_conflicting_aliases(canonical: str, alias: str) -> None:
    arguments = {"action": "wait", canonical: 1, alias: 2}

    with pytest.raises(ValueError, match="conflicting process fields"):
        process_tool_module.prepare_process_arguments(arguments)


def test_process_argument_preparation_deduplicates_equivalent_aliases() -> None:
    arguments = {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 4,
        "wait_time_ms": 60_000,
        "waittimems": "60000",
    }

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 4,
        "wait_time_ms": 60_000,
    }


def test_process_argument_preparation_leaves_unknown_fields_for_strict_validation() -> None:
    arguments = {
        "action": "poll",
        "session_id": "proc_x",
        "cursor": 0,
        "sessionHandle": "legacy",
    }

    prepared = process_tool_module.prepare_process_arguments(arguments)
    with pytest.raises(ValueError, match="poll does not accept sessionHandle"):
        process_tool_module._validate_args(prepared)


def test_process_argument_preparation_explains_required_wait_shape() -> None:
    with pytest.raises(ValueError, match=r"wait requires session_id; use tool process"):
        process_tool_module.prepare_process_arguments({"action": "wait", "cursor": 4})

    with pytest.raises(ValueError, match=r"cursor must be a nonnegative integer.*tool process"):
        process_tool_module.prepare_process_arguments({"action": "poll", "session_id": "proc_x"})


def test_process_argument_preparation_preserves_legacy_write_shapes() -> None:
    submitted = {"action": "write_line", "session_id": "proc_x", "input": "yes"}
    raw = {"action": "write", "session_id": "proc_x", "input": "yes\n"}

    assert process_tool_module.prepare_process_arguments(submitted)["action"] == "write"
    assert process_tool_module.prepare_process_arguments(raw)["action"] == "write_raw"


@pytest.mark.parametrize("payload_field", ["content", "data"])
def test_process_argument_preparation_normalizes_common_stdin_payload_names(
    payload_field: str,
) -> None:
    arguments = {
        "action": "write",
        "session_id": "proc_x",
        payload_field: "ping\n",
    }

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "write_raw",
        "session_id": "proc_x",
        "input": "ping\n",
    }


def test_process_argument_preparation_rejects_ambiguous_stdin_payload_names() -> None:
    with pytest.raises(ValueError, match="multiple stdin payload fields"):
        process_tool_module.prepare_process_arguments(
            {
                "action": "write",
                "session_id": "proc_x",
                "input": "ping",
                "content": "different",
            }
        )


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
    assert service.request.stdin_open is False


def test_managed_bash_closes_stdin_by_default_so_searches_do_not_wait_for_input(managed_tools) -> None:
    _service, _owner, bash, _process = managed_tools
    command = python_command(
        "import sys; data=sys.stdin.read(); print('EOF' if data == '' else 'UNEXPECTED_INPUT')"
    )

    result = bash.execute("bash", {"command": command, "yield_time_ms": 1_000})

    assert result.details["status"] == "exited"
    assert result.details["exitCode"] == 0
    assert "EOF" in text(result)


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
        {"command": python_command("import time; time.sleep(3)"), "yield_time_ms": 0},
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
    assert '"action":"wait"' in text(started)
    assert '"wait_time_ms":60000' in text(started)
    assert "Call the process tool with" in text(started)
    assert "Do not pass yield_time_ms to the wait action." in text(started)
    assert "process.wait" not in text(started)
    for result in (started, polled):
        poll_shape = json.dumps(
            {
                "action": "poll",
                "session_id": result.details["sessionId"],
                "cursor": result.details["nextCursor"],
                "yield_time_ms": result.details["suggestedPollDelayMs"],
            },
            separators=(",", ":"),
        )
        assert poll_shape in text(result)

    waited = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": polled.details["nextCursor"],
            "wait_time_ms": 1_000,
        },
    )

    assert waited.details["status"] == "running"
    assert '"action":"wait"' in text(waited)
    assert '"action":"poll"' not in text(waited)


def test_managed_bash_pty_handoff_leads_with_live_write_then_returned_wait(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command(
                "import sys,time; print('READY', flush=True); "
                "value=sys.stdin.readline().rstrip('\\n'); time.sleep(.2); "
                "print('HANDOFF-OK:' + value, flush=True)"
            ),
            "stdin": "open",
            "tty": True,
            "yield_time_ms": 0,
        },
    )
    session_id = started.details["sessionId"]
    write_shape = json.dumps(
        {"action": "write", "session_id": session_id, "input": "<line>"},
        separators=(",", ":"),
    )

    assert started.details["status"] == "running"
    assert write_shape in text(started)
    assert "After that write, use the exact wait call returned by its result" in text(started)
    assert '"session_id":"<id>"' not in text(started)

    written = process.execute(
        "write",
        {
            "action": "write",
            "session_id": session_id,
            "input": "LIVE-ID",
            "yield_time_ms": 0,
        },
    )
    wait_shape = json.dumps(
        {
            "action": "wait",
            "session_id": session_id,
            "cursor": written.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
        separators=(",", ":"),
    )
    assert wait_shape in text(written)

    terminal = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": session_id,
            "cursor": written.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
    )
    assert terminal.details["status"] == "exited"
    assert "HANDOFF-OK:LIVE-ID" in text(terminal)


def test_managed_bash_open_pipe_handoff_leads_with_live_write(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command(
                "import sys,time; print('READY', flush=True); "
                "value=sys.stdin.readline().rstrip('\\n'); time.sleep(.2); "
                "print('PIPE:' + value, flush=True)"
            ),
            "stdin": "open",
            "yield_time_ms": 0,
        },
    )
    write_shape = json.dumps(
        {
            "action": "write",
            "session_id": started.details["sessionId"],
            "input": "<line>",
        },
        separators=(",", ":"),
    )

    assert started.details["tty"] is False
    assert write_shape in text(started)
    assert "Pipe stdin is open" in text(started)

    written = process.execute(
        "write",
        {
            "action": "write",
            "session_id": started.details["sessionId"],
            "input": "PIPE-LIVE",
            "yield_time_ms": 0,
        },
    )
    terminal = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": written.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
    )
    assert terminal.details["status"] == "exited"
    assert "PIPE:PIPE-LIVE" in text(terminal)


def test_managed_bash_noninteractive_handoff_leads_with_live_wait(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {"command": python_command("import time; time.sleep(.2); print('DONE')"), "yield_time_ms": 0},
    )
    expected_wait = json.dumps(
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": started.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
        separators=(",", ":"),
    )

    assert expected_wait in text(started)
    assert '"action":"write"' not in text(started)

    terminal = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": started.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
    )
    assert terminal.details["status"] == "exited"


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
        assert result.details["artifactId"] in text(result)
        assert "byte_offset=0" in text(result)
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
    assert definition.prepare_arguments(
        {
            "action": "poll",
            "session_id": "proc_x",
            "cursor": 0,
            "yield_time_ms": 0,
            "wait_time_ms": 1_000,
        }
    ) == {
        "action": "wait",
        "session_id": "proc_x",
        "cursor": 0,
        "wait_time_ms": 1_000,
    }
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
    assert definition.render_call(
        {
            "action": "wait",
            "session_id": "proc_0123456789",
            "cursor": 4,
            "wait_time_ms": 60_000,
        }
    ) == "process wait proc_01234567 cursor=4 wait=60000ms"
    assert definition.render_call(
        {
            "action": "poll",
            "session_id": "proc_0123456789",
            "cursor": 4,
            "yield_time_ms": 1_000,
        }
    ) == "process poll proc_01234567 cursor=4 yield=1000ms"


def test_process_list_is_scoped_and_bounds_displayed_command(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    long_command = python_command("import time; time.sleep(.5)") + " # " + "x" * 300
    started = bash.execute("bash", {"command": long_command, "yield_time_ms": 0})

    result = process.execute("list", {"action": "list"})

    assert started.details["sessionId"] in text(result)
    assert len(result.details["processes"][0]["command"]) == 200
    assert "pid" not in str(result.details).lower()


def test_process_list_only_returns_active_jobs(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    completed = bash.execute("done", {"command": python_command("print('done')")})
    running = bash.execute(
        "running",
        {"command": python_command("import time; time.sleep(.5)"), "yield_time_ms": 0},
    )

    result = process.execute("list", {"action": "list"})
    listed_ids = {item["sessionId"] for item in result.details["processes"]}

    assert completed.details["sessionId"] not in listed_ids
    assert running.details["sessionId"] in listed_ids
    assert all(item["status"] == "running" for item in result.details["processes"])


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
        assert managed.get_active_tool_names() == [
            "read",
            "bash",
            "process",
            "tmux",
            "edit",
            "write",
            "spawn_subagent",
            "wait_subagent",
        ]
        assert "Manage named long-lived tmux sessions" in managed.system_prompt
        assert "Use managed `bash` plus `process` for commands that remain running" in managed.system_prompt
        assert "Set `tty=true` only for terminal interaction" in managed.system_prompt
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
        assert "Use wait when a command result is required" in session.system_prompt
        assert "use poll only for interactive input, quick status" in session.system_prompt
        assert "A wait observation never changes bash.timeout or kills the command" in session.system_prompt
        assert "If wait returns running, wait again from that result's exact nextCursor" in session.system_prompt
        assert '"session_id":"<id>"' not in session.system_prompt
        assert "<nextCursor>" not in session.system_prompt
        assert "process.wait" not in session.system_prompt
        assert "process.poll" not in session.system_prompt
        assert "do not repeat unchanged file reads around process checks" in session.system_prompt
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


def test_internal_child_can_send_follow_up_input_to_managed_pty(tmp_path: Path) -> None:
    model = faux_model()
    service = ProcessSessionService(directory=tmp_path / ".processes")
    parent_owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    calls = {"count": 0}
    command = python_command(
        "import sys; print('READY', flush=True); value=input(); print('PTY-CHILD-OK:' + value, flush=True)"
    )

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        tool_results = [message for message in context.messages if message.role == "toolResult"]
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {"command": command, "stdin": "open", "tty": True, "yield_time_ms": 0},
                call_id="child-bash",
            )
        elif calls["count"] == 2:
            started = tool_results[-1]
            events = tool_call_response_events(
                active_model,
                "process",
                {
                    "action": "write",
                    "session_id": started.details["sessionId"],
                    "input": "FOLLOW-UP",
                    "yield_time_ms": 0,
                },
                call_id="child-write",
            )
        elif calls["count"] == 3:
            written = tool_results[-1]
            events = tool_call_response_events(
                active_model,
                "process",
                {
                    "action": "wait",
                    "session_id": written.details["sessionId"],
                    "cursor": written.details["nextCursor"],
                    "wait_time_ms": 60_000,
                },
                call_id="child-wait",
            )
        else:
            events = text_response_events(active_model, "Confirmed PTY-CHILD-OK:FOLLOW-UP")
        return create_faux_provider(lambda _model, _context: events).stream_simple(active_model, context, options)

    parent = AgentSession(
        cwd=str(tmp_path),
        model=model,
        stream_fn=stream_fn,
        process_service=service,
        process_owner=parent_owner,
    )
    task = parent._build_subagent_task("pty-worker", "run the interactive PTY check")
    child_owner = parent._subagent_process_owner(task)
    try:
        result = parent._run_internal_subagent(task)

        assert result.status == "completed"
        assert [entry["toolName"] for entry in result.tool_trace] == ["bash", "process", "process"]
        assert "PTY-CHILD-OK:FOLLOW-UP" in json.dumps(result.tool_trace)
        assert child_owner is not None
        assert len(service.list(child_owner)) == 1
        assert service.list(parent_owner) == ()
        assert tuple(task.allowed_tools) == (
            "read",
            "grep",
            "find",
            "ls",
            "bash",
            "process",
            "edit",
            "write",
            "tmux",
        )
    finally:
        parent.shutdown()
        service.close()


def test_agent_loop_recovers_malformed_process_wait_before_schema_validation(tmp_path: Path) -> None:
    model = faux_model()
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    command = python_command("import time; time.sleep(.05); print('done')")
    calls = {"count": 0}

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        tool_results = [message for message in context.messages if message.role == "toolResult"]
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {"command": command, "yield_time_ms": 0},
                call_id="managed-bash",
            )
        elif calls["count"] == 2:
            started = tool_results[-1]
            events = tool_call_response_events(
                active_model,
                "process.wait",
                {
                    "sessionid": started.details["sessionId"].replace("proc_", "proc", 1),
                    "cursor": str(started.details["nextCursor"]),
                    "waittimems": "120000",
                },
                call_id="mixed-process-wait",
            )
        else:
            events = text_response_events(active_model, "complete")
        return create_faux_provider(lambda _model, _context: events).stream_simple(active_model, context, options)

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        session_path=str(tmp_path / "session.jsonl"),
        process_service=service,
        process_owner=owner,
    )
    try:
        session.prompt("run it and wait", stream_fn=stream_fn)

        tool_results = [message for message in session.messages if message.role == "toolResult"]
        assert calls["count"] == 3
        assert [message.tool_name for message in tool_results] == ["bash", "process"]
        assert tool_results[-1].details["status"] == "exited"
        assert tool_results[-1].details["exitCode"] == 0
        assert "done" in tool_results[-1].content[0].text

        process_call = next(
            block
            for message in session.messages
            if isinstance(message, AssistantMessage)
            for block in message.content
            if getattr(block, "name", None) == "process"
        )
        assert process_call.arguments == {
            "action": "wait",
            "session_id": process_call.arguments["session_id"],
            "cursor": process_call.arguments["cursor"],
            "wait_time_ms": 60_000,
        }

        persisted_process_call = next(
            block
            for entry in session._session_store.entries  # noqa: SLF001 - verifies the resumable transcript contract.
            if entry.get("type") == "message" and entry.get("message", {}).get("role") == "assistant"
            for block in entry["message"].get("content", [])
            if block.get("name") == "process"
        )
        assert persisted_process_call["arguments"] == process_call.arguments
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


@pytest.mark.parametrize(
    ("action", "timing"),
    [
        ("wait", {"wait_time_ms": 60_000}),
        ("poll", {"yield_time_ms": 1_000}),
    ],
)
def test_process_reads_recover_an_impossible_future_cursor(managed_tools, action, timing) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command("print('complete output')"),
            "yield_time_ms": 0,
        },
    )

    result = process.execute(
        "wait",
        {
            "action": action,
            "session_id": started.details["sessionId"],
            "cursor": 10_000,
            **timing,
        },
    )

    assert result.details["status"] == "exited"
    assert result.details["recoveredCursor"] == 10_000
    assert "complete output" in text(result)
    assert "Recovered from invalid cursor 10000" in text(result)


def test_managed_bash_warns_models_not_to_infer_execution_deadlines(managed_tools) -> None:
    _service, _owner, _bash, _process = managed_tools
    definition = create_bash_tool_definition(".")

    assert "Never infer a timeout from expected command duration" in definition.description
    assert any(
        guideline
        == "For planned interactive follow-up, launch bash with tty=true and yield_time_ms=0."
        for guideline in definition.prompt_guidelines
    )


def test_process_write_explains_raw_input_and_line_submission(managed_tools) -> None:
    service, owner, _bash, _process = managed_tools
    definition = create_process_tool_definition(service, owner)
    input_description = definition.parameters["properties"]["input"]["description"]

    assert "appends Enter" in input_description
    assert "write_raw sends the text exactly" in input_description
    assert any("write_raw" in guideline for guideline in definition.prompt_guidelines)
    assert any(
        "A wait observation never changes bash.timeout or kills the command" in guideline
        for guideline in definition.prompt_guidelines
    )
    assert any(
        "eof is valid only for pipe stdin" in guideline
        for guideline in definition.prompt_guidelines
    )


def test_process_write_submits_one_line(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command(
                "import sys; print('READY', flush=True); line=sys.stdin.readline(); print('RECEIVED:'+line.rstrip('\\n'))"
            ),
            "stdin": "open",
            "yield_time_ms": 0,
        },
    )

    written = process.execute(
        "write-line",
        {
            "action": "write",
            "session_id": started.details["sessionId"],
            "input": "hello-process",
            "yield_time_ms": 1_000,
        },
    )
    result = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": written.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
    )

    assert result.details["status"] == "exited"
    assert result.details["exitCode"] == 0
    assert "RECEIVED:hello-process" in text(result)


def test_process_write_raw_preserves_exact_input(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command("import sys; print(repr(sys.stdin.read(5)))"),
            "stdin": "open",
            "yield_time_ms": 0,
        },
    )

    written = process.execute(
        "write-raw",
        {
            "action": "write_raw",
            "session_id": started.details["sessionId"],
            "input": "hello",
            "yield_time_ms": 1_000,
        },
    )
    result = process.execute(
        "wait",
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": written.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
    )

    assert result.details["status"] == "exited"
    assert "'hello'" in text(result)


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


@pytest.mark.parametrize("wait_time_ms", [999, True])
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
        assert result.details["artifactId"] in text(result)
        assert "byte_offset=0" in text(result)
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
