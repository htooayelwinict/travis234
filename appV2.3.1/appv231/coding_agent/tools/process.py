"""Model-facing control tool for app-owned managed processes."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path

from appv231.agent.types import AgentTool, AgentToolResult
from appv231.ai.types import TextContent
from appv231.coding_agent.artifacts import ArtifactRegistry
from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import (
    DEFAULT_PROCESS_POLL_DELAY_MS,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from appv231.coding_agent.tools.types import ToolDefinition, wrap_tool_definition
from appv231.coding_agent.tools.truncate import truncation_to_details

PROCESS_ACTIONS = ("poll", "wait", "write", "resize", "interrupt", "terminate", "kill", "list")
PROCESS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(PROCESS_ACTIONS)},
        "session_id": {"type": "string"},
        "cursor": {"type": "integer", "minimum": 0},
        "input": {"type": "string"},
        "eof": {"type": "boolean"},
        "yield_time_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
        "wait_time_ms": {"type": "integer", "minimum": 1000, "maximum": 900000},
        "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 51200},
        "rows": {"type": "integer", "minimum": 2, "maximum": 200},
        "cols": {"type": "integer", "minimum": 20, "maximum": 500},
    },
    "required": ["action"],
}

_ACTION_FIELDS = {
    "poll": {"action", "session_id", "cursor", "yield_time_ms", "max_bytes"},
    "wait": {"action", "session_id", "cursor", "wait_time_ms", "max_bytes"},
    "write": {"action", "session_id", "input", "eof", "yield_time_ms"},
    "resize": {"action", "session_id", "rows", "cols"},
    "interrupt": {"action", "session_id", "yield_time_ms"},
    "terminate": {"action", "session_id", "yield_time_ms"},
    "kill": {"action", "session_id"},
    "list": {"action"},
}


def create_process_tool_definition(
    service: ProcessSessionService,
    owner: ProcessOwner,
    artifacts: ArtifactRegistry | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name="process",
        label="process",
        description=(
            "Inspect or control commands returned by bash with status=running. Wait for required results; "
            "poll with the exact nextCursor for interactive or incremental output; "
            "write input, resize a PTY, interrupt, terminate, kill, or list current-workspace jobs."
        ),
        parameters=PROCESS_SCHEMA,
        prompt_snippet="Poll and control managed background commands",
        prompt_guidelines=[
            "Use the exact nextCursor returned by bash/process so output is not repeated.",
            "Use process.poll only for interactive input, quick status checks, or intentionally incremental output.",
            "When a command result is required, continue independent work first and then use process.wait; wait ignores output-only wakeups and does not set the command timeout.",
            "Leave a process detached only for a requested server/watcher or when its result is not required.",
            "Set bash.timeout only when an actual execution deadline is intended.",
        ],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_process(
            service,
            owner,
            artifacts,
            tid,
            args,
            signal,
            on_update,
            ctx,
        ),
        render_call=_render_process_call,
        execution_mode="sequential",
    )


def create_process_tool(
    service: ProcessSessionService,
    owner: ProcessOwner,
    artifacts: ArtifactRegistry | None = None,
) -> AgentTool:
    return wrap_tool_definition(create_process_tool_definition(service, owner, artifacts))


def _execute_process(
    service: ProcessSessionService,
    owner: ProcessOwner,
    artifacts: ArtifactRegistry | None,
    _tool_call_id,
    raw_args,
    signal=None,
    on_update=None,
    ctx=None,
) -> AgentToolResult:
    args = _validate_args(raw_args)
    action = args["action"]
    if action != "wait" and signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")
    if action == "list":
        return _list_result(service.list(owner))
    session_id = args["session_id"]
    if action == "poll":
        snapshot = service.poll(
            owner,
            session_id,
            args["cursor"],
            wait_ms=args.get("yield_time_ms", DEFAULT_PROCESS_POLL_DELAY_MS),
            max_bytes=args.get("max_bytes", 51_200),
        )
    elif action == "wait":
        snapshot = service.wait_terminal(
            owner,
            session_id,
            args["cursor"],
            wait_ms=args.get("wait_time_ms", 60_000),
            max_bytes=args.get("max_bytes", 51_200),
            signal=signal,
            on_update=(lambda update: on_update(_snapshot_result(update))) if on_update else None,
        )
        if snapshot.state.terminal:
            return _terminal_process_result(service, owner, snapshot, artifacts)
    elif action == "write":
        snapshot = service.write(
            owner,
            session_id,
            args["input"],
            eof=args.get("eof", False),
            wait_ms=args.get("yield_time_ms", 1000),
        )
    elif action == "resize":
        snapshot = service.resize(owner, session_id, rows=args["rows"], cols=args["cols"])
    elif action == "interrupt":
        snapshot = service.interrupt(
            owner,
            session_id,
            wait_ms=args.get("yield_time_ms", 1000),
        )
    elif action == "terminate":
        snapshot = service.terminate(
            owner,
            session_id,
            wait_ms=args.get("yield_time_ms", 2000),
        )
    else:
        snapshot = service.kill(owner, session_id)
    return _snapshot_result(snapshot)


def _validate_args(raw_args) -> dict[str, object]:
    if not isinstance(raw_args, Mapping):
        raise ValueError("process arguments must be an object")
    args = dict(raw_args)
    action = args.get("action")
    if action not in PROCESS_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(PROCESS_ACTIONS)}")
    unexpected = set(args) - _ACTION_FIELDS[action]
    if unexpected:
        name = sorted(unexpected)[0]
        raise ValueError(f"{action} does not accept {name}")
    if action != "list":
        _require_string(args, action, "session_id")
    if action in {"poll", "wait"}:
        cursor = args.get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError("cursor must be a nonnegative integer")
    elif action == "write":
        _require_string(args, action, "input", allow_empty=True)
        if "eof" in args and not isinstance(args["eof"], bool):
            raise ValueError("eof must be a boolean")
    elif action == "resize":
        for field in ("rows", "cols"):
            if not isinstance(args.get(field), int) or isinstance(args.get(field), bool):
                raise ValueError(f"resize requires {field}")
    if "yield_time_ms" in args:
        value = args["yield_time_ms"]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 30_000:
            raise ValueError("yield_time_ms must be an integer between 0 and 30000")
    if "wait_time_ms" in args:
        value = args["wait_time_ms"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1_000 <= value <= 900_000:
            raise ValueError("wait_time_ms must be an integer between 1000 and 900000")
    if "max_bytes" in args:
        value = args["max_bytes"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1024 <= value <= 51_200:
            raise ValueError("max_bytes must be an integer between 1024 and 51200")
    return args


def _require_string(args: dict[str, object], action: str, field: str, *, allow_empty: bool = False) -> str:
    value = args.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{action} requires {field}")
    return value


def _snapshot_result(snapshot: ProcessSnapshot) -> AgentToolResult:
    footer = _snapshot_footer(snapshot)
    content = f"{snapshot.output}\n\n{footer}" if snapshot.output else footer
    return AgentToolResult(content=[TextContent(text=content)], details=snapshot.as_details())


def _snapshot_footer(snapshot: ProcessSnapshot) -> str:
    position = f"next cursor {snapshot.next_cursor}, output size {snapshot.output_size}"
    if snapshot.state is ProcessState.EXITED:
        return f"Process {snapshot.session_id} exited with code {snapshot.exit_code}; {position}."
    if snapshot.state is ProcessState.TIMED_OUT:
        return f"Process {snapshot.session_id} timed out (exit {snapshot.exit_code}); {position}."
    if snapshot.state is ProcessState.TERMINATED:
        return f"Process {snapshot.session_id} was terminated (exit {snapshot.exit_code}); {position}."
    if snapshot.state is ProcessState.FAILED:
        if snapshot.failure_code == "output_limit":
            return (
                f"Process {snapshot.session_id} was stopped after reaching the sanitized-output budget; "
                f"{position}. This was not a command timeout."
            )
        return f"Process {snapshot.session_id} failed; {position}."
    return (
        f"Process {snapshot.session_id} is {snapshot.state.value}; {position}. "
        f"Use process.wait when the final result is required. "
        f"Suggested poll delay: {snapshot.suggested_poll_delay_ms} ms."
    )


def _terminal_process_result(
    service: ProcessSessionService,
    owner: ProcessOwner,
    snapshot: ProcessSnapshot,
    artifacts: ArtifactRegistry | None,
) -> AgentToolResult:
    tail = service.tail_snapshot(owner, snapshot.session_id)
    details = snapshot.as_details()
    details["nextCursor"] = snapshot.output_size
    full_output_path = Path(snapshot.full_output_path) if snapshot.full_output_path else None
    artifact = None
    if full_output_path is not None and artifacts is not None:
        artifact = artifacts.register(
            full_output_path,
            kind="process-output",
            access="read",
            remove_on_close=False,
        )
    elif tail.truncated:
        full_output_path = service.export_output(owner, snapshot.session_id, tempfile.gettempdir())
        if artifacts is not None:
            artifact = artifacts.register(full_output_path, kind="process-output", access="read")
    if full_output_path is not None:
        details["fullOutputPath"] = str(full_output_path)
    if artifact is not None:
        details["artifactId"] = artifact.id
    if tail.truncated:
        details["truncation"] = truncation_to_details(tail)
    terminal = ProcessSnapshot(
        session_id=snapshot.session_id,
        state=snapshot.state,
        output=tail.content,
        cursor=snapshot.cursor,
        next_cursor=snapshot.output_size,
        output_size=snapshot.output_size,
        exit_code=snapshot.exit_code,
        tty=snapshot.tty,
        elapsed_ms=snapshot.elapsed_ms,
        command=snapshot.command,
        cwd=snapshot.cwd,
        suggested_poll_delay_ms=snapshot.suggested_poll_delay_ms,
        durable_output=snapshot.durable_output,
        full_output_path=str(full_output_path) if full_output_path is not None else None,
        failure_code=snapshot.failure_code,
    )
    result = _snapshot_result(terminal)
    return AgentToolResult(content=result.content, details=details)


def _list_result(snapshots: tuple[ProcessSnapshot, ...]) -> AgentToolResult:
    processes = []
    lines = []
    for snapshot in snapshots:
        command = snapshot.command[:200]
        processes.append(
            {
                "sessionId": snapshot.session_id,
                "status": snapshot.state.value,
                "command": command,
                "cwd": snapshot.cwd,
                "tty": snapshot.tty,
                "elapsedMs": snapshot.elapsed_ms,
                "outputSize": snapshot.output_size,
                "exitCode": snapshot.exit_code,
            }
        )
        lines.append(f"{snapshot.session_id}  {snapshot.state.value}  {command}")
    return AgentToolResult(
        content=[TextContent(text="\n".join(lines) if lines else "No managed processes for this workspace.")],
        details={"processes": processes},
    )


def _render_process_call(args, ctx=None) -> str:
    if not isinstance(args, Mapping):
        return "process"
    action = str(args.get("action") or "")
    session_id = str(args.get("session_id") or "")
    suffix = f" {session_id[:13]}" if session_id else ""
    return f"process {action}{suffix}".strip()


__all__ = [
    "PROCESS_ACTIONS",
    "PROCESS_SCHEMA",
    "create_process_tool",
    "create_process_tool_definition",
]
