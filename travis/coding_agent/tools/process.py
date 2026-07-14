"""Model-facing control tool for app-owned managed processes."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import (
    DEFAULT_PROCESS_POLL_DELAY_MS,
    InvalidCursorError,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from travis.coding_agent.tools.types import ToolDefinition, wrap_tool_definition
from travis.coding_agent.tools.truncate import truncation_to_details

PROCESS_ACTIONS = ("poll", "wait", "write", "write_raw", "resize", "interrupt", "terminate", "kill", "list")

_PROCESS_FIELDS = {
    "session_id": {"type": "string", "minLength": 1, "description": "Exact process session ID"},
    "cursor": {"type": "integer", "minimum": 0, "description": "Exact nextCursor from the last result"},
    "input": {
        "type": "string",
        "description": (
            "Raw input to write exactly as provided. Include a trailing newline (\\n) to submit a line or press Enter"
        ),
    },
    "eof": {"type": "boolean", "description": "Close stdin after writing"},
    "yield_time_ms": {
        "type": "integer",
        "minimum": 0,
        "maximum": 30000,
        "description": "Short observation delay for poll/write/control; never use with wait",
    },
    "wait_time_ms": {
        "type": "integer",
        "minimum": 1000,
        "maximum": 60000,
        "description": "Terminal-state wait deadline; valid only for wait and never a command timeout",
    },
    "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 51200},
    "rows": {"type": "integer", "minimum": 2, "maximum": 200},
    "cols": {"type": "integer", "minimum": 20, "maximum": 500},
}


def _process_action_schema(action: str, fields: tuple[str, ...], required: tuple[str, ...]) -> dict[str, object]:
    properties = {
        "action": {"type": "string", "const": action, "description": f"Use exactly '{action}' for this action"}
    }
    properties.update({name: dict(_PROCESS_FIELDS[name]) for name in fields})
    if action == "write":
        properties["input"]["description"] = "One line of input without a newline; the tool appends one newline to press Enter"
    return {
        "type": "object",
        "title": f"{action} action",
        "properties": properties,
        "required": ["action", *required],
        "additionalProperties": False,
    }


PROCESS_SCHEMA = {
    "type": "object",
    "description": "One action per call; use only the fields declared by that action",
    "oneOf": [
        _process_action_schema("poll", ("session_id", "cursor", "yield_time_ms", "max_bytes"), ("session_id", "cursor")),
        _process_action_schema("wait", ("session_id", "cursor", "wait_time_ms", "max_bytes"), ("session_id", "cursor")),
        _process_action_schema("write", ("session_id", "input", "eof", "yield_time_ms"), ("session_id", "input")),
        _process_action_schema("write_raw", ("session_id", "input", "eof", "yield_time_ms"), ("session_id", "input")),
        _process_action_schema("resize", ("session_id", "rows", "cols"), ("session_id", "rows", "cols")),
        _process_action_schema("interrupt", ("session_id", "yield_time_ms"), ("session_id",)),
        _process_action_schema("terminate", ("session_id", "yield_time_ms"), ("session_id",)),
        _process_action_schema("kill", ("session_id",), ("session_id",)),
        _process_action_schema("list", (), ()),
    ],
}

PROCESS_WAIT_EXAMPLE = '{"action":"wait","session_id":"<id>","cursor":<nextCursor>,"wait_time_ms":60000}'
PROCESS_POLL_EXAMPLE = '{"action":"poll","session_id":"<id>","cursor":<nextCursor>,"yield_time_ms":1000}'
MAX_PROCESS_WAIT_MS = 60_000

_ACTION_FIELDS = {
    "poll": {"action", "session_id", "cursor", "yield_time_ms", "max_bytes"},
    "wait": {"action", "session_id", "cursor", "wait_time_ms", "max_bytes"},
    "write": {"action", "session_id", "input", "eof", "yield_time_ms"},
    "write_raw": {"action", "session_id", "input", "eof", "yield_time_ms"},
    "resize": {"action", "session_id", "rows", "cols"},
    "interrupt": {"action", "session_id", "yield_time_ms"},
    "terminate": {"action", "session_id", "yield_time_ms"},
    "kill": {"action", "session_id"},
    "list": {"action"},
}

_PROCESS_INTEGER_FIELDS = {"cursor", "yield_time_ms", "wait_time_ms", "max_bytes", "rows", "cols"}


def _coerce_process_integer(value):
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if candidate and candidate.removeprefix("-").isdigit():
        return int(candidate)
    return value


def prepare_process_arguments(raw_args):
    if not isinstance(raw_args, Mapping):
        return raw_args
    args = dict(raw_args)
    for field in _PROCESS_INTEGER_FIELDS.intersection(args):
        args[field] = _coerce_process_integer(args[field])

    action = args.get("action")
    if action == "start":
        raise ValueError(
            "process has no start action; start the command with bash using yield_time_ms and "
            "stdin=open, then control the returned session_id with process"
        )
    if action == "write_line":
        args["action"] = "write"
        action = "write"
    if action in {"write", "write_raw"}:
        payload_fields = [name for name in ("input", "data", "content") if name in args]
        if len(payload_fields) > 1:
            raise ValueError(
                "process write received multiple stdin payload fields; use only input"
            )
        if payload_fields and payload_fields[0] != "input":
            args["input"] = args.pop(payload_fields[0])
    if action == "write" and isinstance(args.get("input"), str) and any(
        character in args["input"] for character in "\r\n"
    ):
        args["action"] = "write_raw"
        action = "write_raw"
    if action == "wait" and "yield_time_ms" in args:
        if "wait_time_ms" in args:
            raise ValueError("wait action received both wait_time_ms and yield_time_ms")
        args["wait_time_ms"] = args["yield_time_ms"]
        args.pop("yield_time_ms")
    elif action == "poll" and "wait_time_ms" in args:
        args["action"] = "wait"
        args.pop("yield_time_ms", None)

    normalized_action = args.get("action")
    if normalized_action in {"poll", "wait"}:
        example = PROCESS_POLL_EXAMPLE if normalized_action == "poll" else PROCESS_WAIT_EXAMPLE
        session_id = args.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError(f"{normalized_action} requires session_id; use tool process with {example}")
        cursor = args.get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError(
                f"cursor must be a nonnegative integer for the {normalized_action} action; "
                f"use tool process with {example}"
            )

    if isinstance(raw_args, dict):
        raw_args.clear()
        raw_args.update(args)
        return raw_args
    return args


def format_process_wait_instruction(session_id: str, cursor: int, wait_time_ms: int = 60_000) -> str:
    arguments = json.dumps(
        {
            "action": "wait",
            "session_id": session_id,
            "cursor": cursor,
            "wait_time_ms": wait_time_ms,
        },
        separators=(",", ":"),
    )
    return f"Call the process tool with {arguments}. Do not pass yield_time_ms to the wait action."


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
            "write raw input or a submitted line, resize a PTY, interrupt, terminate, kill, or list current-workspace jobs."
        ),
        parameters=PROCESS_SCHEMA,
        prompt_snippet="Poll and control managed background commands",
        prompt_guidelines=[
            "Use the exact nextCursor returned by bash/process so output is not repeated.",
            "Use the poll action only for interactive input, quick status checks, or intentionally incremental output.",
            "When a command result is required, continue independent work first and then use the wait action; wait ignores output-only wakeups and does not set the command timeout.",
            "When the final result is required, do not call the poll action before the wait action; use one wait from the latest cursor and act on its terminal result.",
            f"Exact terminal-wait shape: Call tool process with {PROCESS_WAIT_EXAMPLE}; never use yield_time_ms with wait.",
            f"Exact quick-poll shape: Call tool process with {PROCESS_POLL_EXAMPLE}; never use wait_time_ms with poll.",
            "Use write to submit one line or press Enter. Use write_raw only for exact bytes, control sequences, or partial input.",
            "Do not repeat unchanged file reads around process checks; retain earlier read results unless a tool operation could have changed that file.",
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
        prepare_arguments=prepare_process_arguments,
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
        return _list_result(tuple(snapshot for snapshot in service.list(owner) if not snapshot.state.terminal))
    session_id = args["session_id"]
    if action == "poll":
        try:
            snapshot = service.poll(
                owner,
                session_id,
                args["cursor"],
                wait_ms=args.get("yield_time_ms", DEFAULT_PROCESS_POLL_DELAY_MS),
                max_bytes=args.get("max_bytes", 51_200),
            )
        except InvalidCursorError as error:
            return _recover_invalid_cursor(service, owner, session_id, args, error, artifacts)
    elif action == "wait":
        try:
            snapshot = service.wait_terminal(
                owner,
                session_id,
                args["cursor"],
                wait_ms=args.get("wait_time_ms", 60_000),
                max_bytes=args.get("max_bytes", 51_200),
                signal=signal,
                on_update=(lambda update: on_update(_snapshot_result(update))) if on_update else None,
            )
        except InvalidCursorError as error:
            return _recover_invalid_cursor(service, owner, session_id, args, error, artifacts)
        if snapshot.state.terminal:
            return _terminal_process_result(service, owner, snapshot, artifacts)
    elif action in {"write", "write_raw"}:
        input_text = args["input"]
        if action == "write":
            input_text += "\n"
        snapshot = service.write(
            owner,
            session_id,
            input_text,
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


def _recover_invalid_cursor(
    service: ProcessSessionService,
    owner: ProcessOwner,
    session_id: str,
    args: Mapping[str, object],
    error: InvalidCursorError,
    artifacts: ArtifactRegistry | None,
) -> AgentToolResult:
    snapshot = service.poll(
        owner,
        session_id,
        0,
        wait_ms=0,
        max_bytes=args.get("max_bytes", 51_200),
    )
    result = (
        _terminal_process_result(service, owner, snapshot, artifacts)
        if snapshot.state.terminal
        else _snapshot_result(snapshot)
    )
    details = dict(result.details or {})
    details["recoveredCursor"] = error.cursor
    warning = (
        f"Recovered from invalid cursor {error.cursor}; current output size was {error.output_size}. "
        "Returned available output from cursor 0."
    )
    return AgentToolResult(
        content=[TextContent(text=f"{warning}\n\n"), *result.content],
        details=details,
    )


def _validate_args(raw_args) -> dict[str, object]:
    prepared_args = prepare_process_arguments(raw_args)
    if not isinstance(prepared_args, Mapping):
        raise ValueError("process arguments must be an object")
    args = dict(prepared_args)
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
    elif action in {"write", "write_raw"}:
        _require_string(args, action, "input", allow_empty=True)
        if action == "write" and any(character in args["input"] for character in "\r\n"):
            raise ValueError("write input must contain exactly one line without a newline; use write_raw for exact input")
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
        if not isinstance(value, int) or isinstance(value, bool) or not 1_000 <= value <= MAX_PROCESS_WAIT_MS:
            raise ValueError(f"wait_time_ms must be an integer between 1000 and {MAX_PROCESS_WAIT_MS}")
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
        f"{format_process_wait_instruction(snapshot.session_id, snapshot.next_cursor)} "
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
        content=[TextContent(text="\n".join(lines) if lines else "No active managed processes for this workspace.")],
        details={"processes": processes},
    )


def _render_process_call(args, ctx=None) -> str:
    if not isinstance(args, Mapping):
        return "process"
    action = str(args.get("action") or "")
    session_id = str(args.get("session_id") or "")
    suffix = f" {session_id[:13]}" if session_id else ""
    metadata: list[str] = []
    if action in {"poll", "wait"} and isinstance(args.get("cursor"), int):
        metadata.append(f"cursor={args['cursor']}")
    if action == "wait" and isinstance(args.get("wait_time_ms"), int):
        metadata.append(f"wait={args['wait_time_ms']}ms")
    elif action == "poll" and isinstance(args.get("yield_time_ms"), int):
        metadata.append(f"yield={args['yield_time_ms']}ms")
    detail = f" {' '.join(metadata)}" if metadata else ""
    return f"process {action}{suffix}{detail}".strip()


__all__ = [
    "PROCESS_ACTIONS",
    "PROCESS_POLL_EXAMPLE",
    "PROCESS_SCHEMA",
    "PROCESS_WAIT_EXAMPLE",
    "create_process_tool",
    "create_process_tool_definition",
    "format_process_wait_instruction",
    "prepare_process_arguments",
]
