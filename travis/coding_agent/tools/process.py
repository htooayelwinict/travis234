"""Model-facing control tool for app-owned managed processes."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeGuard, overload

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifact_store import ArtifactPromotionError
from travis.coding_agent.artifacts import ArtifactRef, ArtifactRegistry, artifact_read_instruction
from travis.coding_agent.policy.context import action_policy_context
from travis.coding_agent.policy.types import ALL_TOOL_EFFECTS
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import (
    DEFAULT_PROCESS_POLL_DELAY_MS,
    InvalidCursorError,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from travis.coding_agent.tools.types import ToolDefinition, wrap_tool_definition
from travis.coding_agent.tools.truncate import TruncationResult, truncation_to_details

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

_ProcessAction = Literal[
    "poll",
    "wait",
    "write",
    "write_raw",
    "resize",
    "interrupt",
    "terminate",
    "kill",
    "list",
]


class _ValidatedProcessArguments(Protocol):
    @overload
    def __getitem__(self, field: Literal["action"]) -> _ProcessAction: ...

    @overload
    def __getitem__(self, field: Literal["session_id", "input"]) -> str: ...

    @overload
    def __getitem__(
        self,
        field: Literal[
            "cursor",
            "yield_time_ms",
            "wait_time_ms",
            "max_bytes",
            "rows",
            "cols",
        ],
    ) -> int: ...

    @overload
    def __getitem__(self, field: Literal["eof"]) -> bool: ...

    def __getitem__(self, field: str) -> object: ...

    @overload
    def get(
        self,
        field: Literal["yield_time_ms", "wait_time_ms", "max_bytes"],
    ) -> int | None: ...

    @overload
    def get(
        self,
        field: Literal["yield_time_ms", "wait_time_ms", "max_bytes"],
        default: int,
    ) -> int: ...

    @overload
    def get(self, field: Literal["eof"], default: bool) -> bool: ...

    def get(self, field: str, default: object = None) -> object: ...


def _coerce_process_integer(value: object) -> object:
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if candidate and candidate.removeprefix("-").isdigit():
        return int(candidate)
    return value


def _repair_process_session_id(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = _COLLAPSED_PROCESS_SESSION_ID.fullmatch(value)
    if match is None:
        return value
    return f"proc_{match.group(1)}"


def _normalized_process_field_value(field: str, value: object) -> object:
    if field in _PROCESS_INTEGER_FIELDS:
        return _coerce_process_integer(value)
    if field == "session_id":
        return _repair_process_session_id(value)
    return value


def _normalize_process_field_aliases(args: dict[str, object]) -> None:
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
                raise ValueError(f"conflicting process fields: {canonical_field} and {supplied_field}")
        else:
            args[canonical_field] = supplied_value
        args.pop(supplied_field)


def _normalize_process_scalar_fields(args: dict[str, object]) -> None:
    for field in _PROCESS_INTEGER_FIELDS.intersection(args):
        args[field] = _coerce_process_integer(args[field])
    if "session_id" in args:
        args["session_id"] = _repair_process_session_id(args["session_id"])


def _normalize_process_action(args: dict[str, object]) -> object:
    action = args.get("action")
    if action == "start":
        raise ValueError(
            "process has no start action; start the command with bash using yield_time_ms and "
            "stdin=open, then control the returned session_id with process"
        )
    if action == "write_line":
        args["action"] = "write"
        return "write"
    return action


def _normalize_process_write_payload(
    args: dict[str, object],
    action: object,
) -> None:
    if action not in {"write", "write_raw"}:
        return
    payload_fields = [name for name in ("input", "data", "content") if name in args]
    if len(payload_fields) > 1:
        raise ValueError("process write received multiple stdin payload fields; use only input")
    if payload_fields and payload_fields[0] != "input":
        args["input"] = args.pop(payload_fields[0])
    input_value = args.get("input")
    if action == "write" and isinstance(input_value, str) and any(character in input_value for character in "\r\n"):
        args["action"] = "write_raw"


def _normalize_process_observation(args: dict[str, object]) -> None:
    action = args.get("action")
    if action == "wait" and "yield_time_ms" in args:
        if "wait_time_ms" in args:
            raise ValueError("wait action received both wait_time_ms and yield_time_ms")
        args["wait_time_ms"] = args["yield_time_ms"]
        args.pop("yield_time_ms")
    elif action == "poll" and "wait_time_ms" in args:
        args["action"] = "wait"
        args.pop("yield_time_ms", None)
    wait_time = args.get("wait_time_ms")
    if args.get("action") == "wait" and isinstance(wait_time, int) and not isinstance(wait_time, bool):
        args["wait_time_ms"] = min(wait_time, MAX_PROCESS_WAIT_MS)


def _validate_prepared_process_observation(args: dict[str, object]) -> None:
    action = args.get("action")
    if action not in {"poll", "wait"}:
        return
    example = PROCESS_POLL_EXAMPLE if action == "poll" else PROCESS_WAIT_EXAMPLE
    session_id = args.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(f"{action} requires session_id; use tool process with {example}")
    cursor = args.get("cursor")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise ValueError(
            f"cursor must be a nonnegative integer for the {action} action; use tool process with {example}"
        )


def prepare_process_arguments(raw_args):
    if not isinstance(raw_args, Mapping):
        return raw_args
    args = dict(raw_args)
    _normalize_process_field_aliases(args)
    _normalize_process_scalar_fields(args)
    action = _normalize_process_action(args)
    _normalize_process_write_payload(args, action)
    _normalize_process_observation(args)
    _validate_prepared_process_observation(args)

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
    arguments = _compact_process_call({"action": "write", "session_id": session_id, "input": "<line>"})
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
        effects=ALL_TOOL_EFFECTS,
        policy_context=action_policy_context,
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
    args: _ValidatedProcessArguments,
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


def _is_process_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_optional_process_integer(
    args: dict[str, object],
    field: str,
) -> bool:
    return field not in args or _is_process_integer(args[field])


def _has_valid_process_field_types(
    args: dict[str, object],
) -> TypeGuard[_ValidatedProcessArguments]:
    action = args.get("action")
    eof = args.get("eof")
    return (
        isinstance(action, str)
        and action in PROCESS_ACTIONS
        and ("session_id" not in args or isinstance(args["session_id"], str))
        and ("input" not in args or isinstance(args["input"], str))
        and ("eof" not in args or isinstance(eof, bool))
        and all(_has_optional_process_integer(args, field) for field in _PROCESS_INTEGER_FIELDS)
    )


def _validated_process_action(args: dict[str, object]) -> _ProcessAction:
    action = args.get("action")
    if not isinstance(action, str) or action not in PROCESS_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(PROCESS_ACTIONS)}")
    return action


def _reject_unexpected_process_fields(
    args: dict[str, object],
    action: _ProcessAction,
) -> None:
    unexpected = set(args) - _ACTION_FIELDS[action]
    if unexpected:
        name = sorted(unexpected)[0]
        raise ValueError(f"{action} does not accept {name}")


def _validate_process_timing_fields(args: dict[str, object]) -> None:
    if "yield_time_ms" in args:
        value = args["yield_time_ms"]
        if not _is_process_integer(value) or not 0 <= value <= 30_000:
            raise ValueError("yield_time_ms must be an integer between 0 and 30000")
    if "wait_time_ms" in args:
        value = args["wait_time_ms"]
        if not _is_process_integer(value) or not 1_000 <= value <= MAX_PROCESS_WAIT_MS:
            raise ValueError(f"wait_time_ms must be an integer between 1000 and {MAX_PROCESS_WAIT_MS}")
    if "max_bytes" in args:
        value = args["max_bytes"]
        if not _is_process_integer(value) or not 1024 <= value <= 51_200:
            raise ValueError("max_bytes must be an integer between 1024 and 51200")


def _validated_observation_arguments(
    args: dict[str, object],
) -> _ValidatedProcessArguments:
    cursor = args.get("cursor")
    if not _is_process_integer(cursor) or cursor < 0:
        raise ValueError("cursor must be a nonnegative integer")
    _validate_process_timing_fields(args)
    if not _has_valid_process_field_types(args):
        raise ValueError("cursor must be a nonnegative integer")
    return args


def _validated_write_arguments(
    args: dict[str, object],
    action: Literal["write", "write_raw"],
) -> _ValidatedProcessArguments:
    input_text = _require_string(args, action, "input", allow_empty=True)
    if action == "write" and any(character in input_text for character in "\r\n"):
        raise ValueError("write input must contain exactly one line without a newline; use write_raw for exact input")
    if "eof" in args and not isinstance(args["eof"], bool):
        raise ValueError("eof must be a boolean")
    _validate_process_timing_fields(args)
    if not _has_valid_process_field_types(args):
        raise _missing_process_field(action, "input")
    return args


def _validated_resize_arguments(
    args: dict[str, object],
) -> _ValidatedProcessArguments:
    for field in ("rows", "cols"):
        if not _is_process_integer(args.get(field)):
            raise _missing_process_field("resize", field)
    if not _has_valid_process_field_types(args):
        raise _missing_process_field("resize", "rows")
    return args


def _validated_control_arguments(
    args: dict[str, object],
    action: Literal["interrupt", "terminate", "kill"],
) -> _ValidatedProcessArguments:
    _validate_process_timing_fields(args)
    if not _has_valid_process_field_types(args):
        raise _missing_process_field(action, "session_id")
    return args


def _validated_list_arguments(args: dict[str, object]) -> _ValidatedProcessArguments:
    if not _has_valid_process_field_types(args):
        raise ValueError(f"action must be one of: {', '.join(PROCESS_ACTIONS)}")
    return args


def _validate_args(raw_args: object) -> _ValidatedProcessArguments:
    prepared_args = prepare_process_arguments(raw_args)
    if not isinstance(prepared_args, Mapping):
        raise ValueError("process arguments must be an object")
    args = dict(prepared_args)
    action = _validated_process_action(args)
    _reject_unexpected_process_fields(args, action)
    if action == "list":
        return _validated_list_arguments(args)
    _require_string(args, action, "session_id")
    if action in {"poll", "wait"}:
        return _validated_observation_arguments(args)
    if action == "write":
        return _validated_write_arguments(args, "write")
    if action == "write_raw":
        return _validated_write_arguments(args, "write_raw")
    if action == "resize":
        return _validated_resize_arguments(args)
    if action == "interrupt":
        return _validated_control_arguments(args, "interrupt")
    if action == "terminate":
        return _validated_control_arguments(args, "terminate")
    return _validated_control_arguments(args, "kill")


def _require_string(args: dict[str, object], action: str, field: str, *, allow_empty: bool = False) -> str:
    value = args.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise _missing_process_field(action, field)
    return value


def _missing_process_field(action: str, field: str) -> ValueError:
    return ValueError(f"{action} requires {field}; use tool process with {_PROCESS_ACTION_EXAMPLES[action]}")


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


@dataclass(frozen=True)
class _TerminalArtifactResult:
    path: Path | None
    artifact: ArtifactRef | None
    unavailable: dict[str, str] | None


def _store_terminal_artifact(
    artifacts: ArtifactRegistry,
    path: Path,
    *,
    exported_temporary: bool,
    tool_call_id: str | None,
) -> tuple[ArtifactRef | None, dict[str, str] | None]:
    if artifacts.is_durable:
        try:
            return (
                artifacts.promote(
                    path,
                    kind="process-output",
                    tool_call_id=tool_call_id,
                ),
                None,
            )
        except ArtifactPromotionError as error:
            return None, _artifact_unavailable(error)
        except OSError:
            return None, _artifact_unavailable(None)
    if exported_temporary:
        return artifacts.register(path, kind="process-output", access="read"), None
    return (
        artifacts.register(
            path,
            kind="process-output",
            access="read",
            remove_on_close=False,
        ),
        None,
    )


def _resolve_terminal_artifact(
    service: ProcessSessionService,
    owner: ProcessOwner,
    snapshot: ProcessSnapshot,
    tail: TruncationResult,
    artifacts: ArtifactRegistry | None,
    *,
    tool_call_id: str | None,
) -> _TerminalArtifactResult:
    path = Path(snapshot.full_output_path) if snapshot.full_output_path else None
    exported_temporary = False
    artifact = None
    unavailable = None
    if path is not None and artifacts is not None:
        artifact, unavailable = _store_terminal_artifact(
            artifacts,
            path,
            exported_temporary=False,
            tool_call_id=tool_call_id,
        )
    elif tail.truncated:
        path = service.export_output(owner, snapshot.session_id, tempfile.gettempdir())
        exported_temporary = True
        if artifacts is not None:
            artifact, unavailable = _store_terminal_artifact(
                artifacts,
                path,
                exported_temporary=True,
                tool_call_id=tool_call_id,
            )
    if exported_temporary and artifacts is not None and artifacts.is_durable and path is not None:
        path.unlink(missing_ok=True)
    return _TerminalArtifactResult(path, artifact, unavailable)


def _apply_terminal_artifact_details(
    details: dict[str, object],
    state: _TerminalArtifactResult,
    tail: TruncationResult,
    artifacts: ArtifactRegistry | None,
) -> None:
    if state.path is not None and not (artifacts is not None and artifacts.is_durable):
        details["fullOutputPath"] = str(state.path)
    else:
        details.pop("fullOutputPath", None)
    if state.artifact is not None:
        details["artifactId"] = state.artifact.id
    if state.unavailable is not None:
        details["artifactUnavailable"] = state.unavailable
    if tail.truncated:
        details["truncation"] = truncation_to_details(tail)


def _reconstructed_terminal_snapshot(
    snapshot: ProcessSnapshot,
    tail: TruncationResult,
    state: _TerminalArtifactResult,
    artifacts: ArtifactRegistry | None,
) -> ProcessSnapshot:
    exposed_path = (
        str(state.path) if state.path is not None and not (artifacts is not None and artifacts.is_durable) else None
    )
    return ProcessSnapshot(
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
        full_output_path=exposed_path,
        failure_code=snapshot.failure_code,
    )


def _append_terminal_artifact_notice(
    result: AgentToolResult,
    state: _TerminalArtifactResult,
) -> None:
    if state.artifact is None and state.unavailable is None:
        return
    first_content = result.content[0]
    if not isinstance(first_content, TextContent):
        raise AttributeError(f"'{type(first_content).__name__}' object has no attribute 'text'")
    if state.artifact is not None:
        first_content.text += f"\n\n[{artifact_read_instruction(state.artifact.id)}]"
    elif state.unavailable is not None:
        first_content.text += f"\n\n[Full output artifact unavailable ({state.unavailable['code']}).]"


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
    artifact_state = _resolve_terminal_artifact(
        service,
        owner,
        snapshot,
        tail,
        artifacts,
        tool_call_id=tool_call_id,
    )
    _apply_terminal_artifact_details(details, artifact_state, tail, artifacts)
    terminal = _reconstructed_terminal_snapshot(snapshot, tail, artifact_state, artifacts)
    result = _snapshot_result(terminal)
    _append_terminal_artifact_notice(result, artifact_state)
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
