"""Model-facing control tool for app-owned managed processes."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifact_store import ArtifactPromotionError
from travis.coding_agent.artifacts import ArtifactRegistry, artifact_read_instruction
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
MAX_PROCESS_WAIT_MS = 60_000

_PROCESS_FIELDS = {
    "session_id": {"type": "string", "minLength": 1, "description": "Exact process session ID"},
    "cursor": {"type": "integer", "minimum": 0, "description": "Exact nextCursor from the last result"},
    "input": {
        "type": "string",
        "description": (
            "Input for write or write_raw. write accepts one line without a newline and appends Enter; "
            "write_raw sends the text exactly, including control characters or newlines"
        ),
    },
    "eof": {
        "type": "boolean",
        "description": "Close pipe stdin after writing; invalid for PTY sessions",
    },
    "yield_time_ms": {
        "type": "integer",
        "minimum": 0,
        "maximum": 30000,
        "description": "Short observation delay for poll/write/control; never use with wait",
    },
    "wait_time_ms": {
        "type": "integer",
        "minimum": 1000,
        "maximum": MAX_PROCESS_WAIT_MS,
        "description": "Terminal-state wait deadline; valid only for wait and never a command timeout",
    },
    "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 51200},
    "rows": {"type": "integer", "minimum": 2, "maximum": 200},
    "cols": {"type": "integer", "minimum": 20, "maximum": 500},
}


PROCESS_SCHEMA = {
    "type": "object",
    "description": (
        "Control one process session returned by bash. Choose one action and supply only fields valid for it; "
        "start commands with bash, not process."
    ),
    "properties": {
        "action": {
            "type": "string",
            "enum": list(PROCESS_ACTIONS),
            "description": "Process operation to perform",
        },
        **{name: dict(schema) for name, schema in _PROCESS_FIELDS.items()},
    },
    "required": ["action"],
    "additionalProperties": False,
}

PROCESS_WAIT_EXAMPLE = '{"action":"wait","session_id":"<id>","cursor":<nextCursor>,"wait_time_ms":60000}'
PROCESS_POLL_EXAMPLE = '{"action":"poll","session_id":"<id>","cursor":<nextCursor>,"yield_time_ms":1000}'
_PROCESS_ACTION_EXAMPLES = {
    "poll": PROCESS_POLL_EXAMPLE,
    "wait": PROCESS_WAIT_EXAMPLE,
    "write": '{"action":"write","session_id":"<id>","input":"<line>"}',
    "write_raw": '{"action":"write_raw","session_id":"<id>","input":"<exact-input>"}',
    "resize": '{"action":"resize","session_id":"<id>","rows":24,"cols":80}',
    "interrupt": '{"action":"interrupt","session_id":"<id>"}',
    "terminate": '{"action":"terminate","session_id":"<id>"}',
    "kill": '{"action":"kill","session_id":"<id>"}',
    "list": '{"action":"list"}',
}

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
_PROCESS_FIELD_TOKEN_MAP = {
    "sessionid": "session_id",
    "processid": "session_id",
    "cursor": "cursor",
    "nextcursor": "cursor",
    "yieldtimems": "yield_time_ms",
    "waittimems": "wait_time_ms",
    "maxbytes": "max_bytes",
}
_COLLAPSED_PROCESS_SESSION_ID = re.compile(r"^proc([0-9a-f]{32})$")


def _coerce_process_integer(value):
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if candidate and candidate.removeprefix("-").isdigit():
        return int(candidate)
    return value


def _repair_process_session_id(value):
    if not isinstance(value, str):
        return value
    match = _COLLAPSED_PROCESS_SESSION_ID.fullmatch(value)
    if match is None:
        return value
    return f"proc_{match.group(1)}"


def _normalized_process_field_value(field: str, value):
    if field in _PROCESS_INTEGER_FIELDS:
        return _coerce_process_integer(value)
    if field == "session_id":
        return _repair_process_session_id(value)
    return value


def _normalize_process_field_aliases(args: dict) -> None:
    canonical_fields = set(_PROCESS_FIELDS)
    for supplied_field in tuple(args):
        if supplied_field in canonical_fields or supplied_field == "action":
            continue
        token = supplied_field.replace("_", "").replace("-", "").lower()
        canonical_field = _PROCESS_FIELD_TOKEN_MAP.get(token)
        if canonical_field is None:
            continue
        supplied_value = _normalized_process_field_value(canonical_field, args[supplied_field])
        if canonical_field in args:
            canonical_value = _normalized_process_field_value(canonical_field, args[canonical_field])
            if canonical_value != supplied_value:
                raise ValueError(
                    f"conflicting process fields: {canonical_field} and {supplied_field}"
                )
        else:
            args[canonical_field] = supplied_value
        args.pop(supplied_field)


def prepare_process_arguments(raw_args):
    if not isinstance(raw_args, Mapping):
        return raw_args
    args = dict(raw_args)
    _normalize_process_field_aliases(args)
    for field in _PROCESS_INTEGER_FIELDS.intersection(args):
        args[field] = _coerce_process_integer(args[field])
    if "session_id" in args:
        args["session_id"] = _repair_process_session_id(args["session_id"])

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

    if (
        args.get("action") == "wait"
        and isinstance(args.get("wait_time_ms"), int)
        and not isinstance(args["wait_time_ms"], bool)
    ):
        args["wait_time_ms"] = min(args["wait_time_ms"], MAX_PROCESS_WAIT_MS)

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


def _compact_process_call(arguments: dict[str, object]) -> str:
    return json.dumps(arguments, separators=(",", ":"))


def format_process_wait_instruction(session_id: str, cursor: int, wait_time_ms: int = 60_000) -> str:
    arguments = _compact_process_call(
        {
            "action": "wait",
            "session_id": session_id,
            "cursor": cursor,
            "wait_time_ms": wait_time_ms,
        }
    )
    return f"Call the process tool with {arguments}. Do not pass yield_time_ms to the wait action."


def format_process_poll_instruction(session_id: str, cursor: int, yield_time_ms: int = 1_000) -> str:
    arguments = _compact_process_call(
        {
            "action": "poll",
            "session_id": session_id,
            "cursor": cursor,
            "yield_time_ms": yield_time_ms,
        }
    )
    return f"For a quick status check, call the process tool with {arguments}."


def format_process_write_instruction(session_id: str) -> str:
    arguments = _compact_process_call(
        {"action": "write", "session_id": session_id, "input": "<line>"}
    )
    return f"To submit one line, call the process tool with {arguments}."


def format_process_bash_handoff(snapshot: ProcessSnapshot, *, input_open: bool) -> str:
    if input_open:
        input_kind = "PTY input" if snapshot.tty else "Pipe stdin"
        return (
            f"{input_kind} is open. {format_process_write_instruction(snapshot.session_id)} "
            "After that write, use the exact wait call returned by its result and its nextCursor. "
            f"{format_process_poll_instruction(snapshot.session_id, snapshot.next_cursor, snapshot.suggested_poll_delay_ms)}"
        )
    return (
        f"{format_process_wait_instruction(snapshot.session_id, snapshot.next_cursor)} "
        f"{format_process_poll_instruction(snapshot.session_id, snapshot.next_cursor, snapshot.suggested_poll_delay_ms)}"
    )


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
            "Use the exact nextCursor returned by bash/process so output is neither repeated nor skipped.",
            "Use wait when a command result is required; use poll only for interactive input, quick status, or intentionally incremental output. A wait observation never changes bash.timeout or kills the command.",
            "If wait returns running, wait again from that result's exact nextCursor.",
            "Use write to submit one line; use write_raw for exact bytes, control sequences, partial input, or PTY Ctrl-D. eof is valid only for pipe stdin.",
            "Continue independent work before waiting, but do not repeat unchanged file reads around process checks.",
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
    tool_call_id,
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
            return _recover_invalid_cursor(
                service,
                owner,
                session_id,
                args,
                error,
                artifacts,
                tool_call_id=tool_call_id,
            )
    elif action == "wait":
        try:
            snapshot = service.wait_terminal(
                owner,
                session_id,
                args["cursor"],
                wait_ms=args.get("wait_time_ms", 60_000),
                max_bytes=args.get("max_bytes", 51_200),
                signal=signal,
                on_update=(lambda update: on_update(_snapshot_result(update, include_poll_hint=False)))
                if on_update
                else None,
            )
        except InvalidCursorError as error:
            return _recover_invalid_cursor(
                service,
                owner,
                session_id,
                args,
                error,
                artifacts,
                tool_call_id=tool_call_id,
                include_poll_hint=False,
            )
        if snapshot.state.terminal:
            return _terminal_process_result(
                service,
                owner,
                snapshot,
                artifacts,
                tool_call_id=tool_call_id,
            )
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
    return _snapshot_result(snapshot, include_poll_hint=action != "wait")


def _recover_invalid_cursor(
    service: ProcessSessionService,
    owner: ProcessOwner,
    session_id: str,
    args: Mapping[str, object],
    error: InvalidCursorError,
    artifacts: ArtifactRegistry | None,
    *,
    tool_call_id: str | None = None,
    include_poll_hint: bool = True,
) -> AgentToolResult:
    snapshot = service.poll(
        owner,
        session_id,
        0,
        wait_ms=0,
        max_bytes=args.get("max_bytes", 51_200),
    )
    result = (
        _terminal_process_result(
            service,
            owner,
            snapshot,
            artifacts,
            tool_call_id=tool_call_id,
        )
        if snapshot.state.terminal
        else _snapshot_result(snapshot, include_poll_hint=include_poll_hint)
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
                raise _missing_process_field("resize", field)
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
        raise _missing_process_field(action, field)
    return value


def _missing_process_field(action: str, field: str) -> ValueError:
    return ValueError(
        f"{action} requires {field}; use tool process with {_PROCESS_ACTION_EXAMPLES[action]}"
    )


def _snapshot_result(snapshot: ProcessSnapshot, *, include_poll_hint: bool = True) -> AgentToolResult:
    footer = _snapshot_footer(snapshot, include_poll_hint=include_poll_hint)
    content = f"{snapshot.output}\n\n{footer}" if snapshot.output else footer
    return AgentToolResult(content=[TextContent(text=content)], details=snapshot.as_details())


def _snapshot_footer(snapshot: ProcessSnapshot, *, include_poll_hint: bool = True) -> str:
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
    footer = (
        f"Process {snapshot.session_id} is {snapshot.state.value}; {position}. "
        f"{format_process_wait_instruction(snapshot.session_id, snapshot.next_cursor)}"
    )
    if include_poll_hint:
        footer += (
            " "
            f"{format_process_poll_instruction(snapshot.session_id, snapshot.next_cursor, snapshot.suggested_poll_delay_ms)}"
        )
    return footer


def _terminal_process_result(
    service: ProcessSessionService,
    owner: ProcessOwner,
    snapshot: ProcessSnapshot,
    artifacts: ArtifactRegistry | None,
    *,
    tool_call_id: str | None = None,
) -> AgentToolResult:
    tail = service.tail_snapshot(owner, snapshot.session_id)
    details = snapshot.as_details()
    details["nextCursor"] = snapshot.output_size
    full_output_path = Path(snapshot.full_output_path) if snapshot.full_output_path else None
    artifact = None
    artifact_unavailable: dict[str, str] | None = None
    exported_temporary = False
    if full_output_path is not None and artifacts is not None:
        if artifacts.is_durable:
            try:
                artifact = artifacts.promote(
                    full_output_path,
                    kind="process-output",
                    tool_call_id=tool_call_id,
                )
            except ArtifactPromotionError as error:
                artifact_unavailable = _artifact_unavailable(error)
            except OSError:
                artifact_unavailable = _artifact_unavailable(None)
        else:
            artifact = artifacts.register(
                full_output_path,
                kind="process-output",
                access="read",
                remove_on_close=False,
            )
    elif tail.truncated:
        full_output_path = service.export_output(owner, snapshot.session_id, tempfile.gettempdir())
        exported_temporary = True
        if artifacts is not None:
            if artifacts.is_durable:
                try:
                    artifact = artifacts.promote(
                        full_output_path,
                        kind="process-output",
                        tool_call_id=tool_call_id,
                    )
                except ArtifactPromotionError as error:
                    artifact_unavailable = _artifact_unavailable(error)
                except OSError:
                    artifact_unavailable = _artifact_unavailable(None)
            else:
                artifact = artifacts.register(full_output_path, kind="process-output", access="read")
    if exported_temporary and artifacts is not None and artifacts.is_durable and full_output_path is not None:
        full_output_path.unlink(missing_ok=True)
    if full_output_path is not None and not (artifacts is not None and artifacts.is_durable):
        details["fullOutputPath"] = str(full_output_path)
    else:
        details.pop("fullOutputPath", None)
    if artifact is not None:
        details["artifactId"] = artifact.id
    if artifact_unavailable is not None:
        details["artifactUnavailable"] = artifact_unavailable
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
        full_output_path=(
            str(full_output_path)
            if full_output_path is not None and not (artifacts is not None and artifacts.is_durable)
            else None
        ),
        failure_code=snapshot.failure_code,
    )
    result = _snapshot_result(terminal)
    if artifact is not None:
        result.content[0].text += f"\n\n[{artifact_read_instruction(artifact.id)}]"
    elif artifact_unavailable is not None:
        result.content[0].text += (
            f"\n\n[Full output artifact unavailable ({artifact_unavailable['code']}).]"
        )
    return AgentToolResult(content=result.content, details=details)


def _artifact_unavailable(error: ArtifactPromotionError | None) -> dict[str, str]:
    if error is None:
        return {"code": "unavailable", "message": "Artifact storage is unavailable"}
    message = " ".join(str(error).split())[:240] or "Artifact storage is unavailable"
    return {"code": error.code, "message": message}


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
    "format_process_bash_handoff",
    "format_process_poll_instruction",
    "format_process_wait_instruction",
    "format_process_write_instruction",
    "prepare_process_arguments",
]
