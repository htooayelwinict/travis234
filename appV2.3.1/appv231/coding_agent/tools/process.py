"""Model-facing control tool for app-owned managed processes."""

from __future__ import annotations

from collections.abc import Mapping

from appv231.agent.types import AgentTool, AgentToolResult
from appv231.ai.types import TextContent
from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import (
    DEFAULT_PROCESS_POLL_DELAY_MS,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)
from appv231.coding_agent.tools.types import ToolDefinition, wrap_tool_definition

PROCESS_ACTIONS = ("poll", "write", "resize", "interrupt", "terminate", "kill", "list")
PROCESS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(PROCESS_ACTIONS)},
        "session_id": {"type": "string"},
        "cursor": {"type": "integer", "minimum": 0},
        "input": {"type": "string"},
        "eof": {"type": "boolean"},
        "yield_time_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
        "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 51200},
        "rows": {"type": "integer", "minimum": 2, "maximum": 200},
        "cols": {"type": "integer", "minimum": 20, "maximum": 500},
    },
    "required": ["action"],
}

_ACTION_FIELDS = {
    "poll": {"action", "session_id", "cursor", "yield_time_ms", "max_bytes"},
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
) -> ToolDefinition:
    return ToolDefinition(
        name="process",
        label="process",
        description=(
            "Inspect or control commands returned by bash with status=running. Poll with the exact nextCursor; "
            "write input, resize a PTY, interrupt, terminate, kill, or list current-workspace jobs."
        ),
        parameters=PROCESS_SCHEMA,
        prompt_snippet="Poll and control managed background commands",
        prompt_guidelines=[
            "Use the nextCursor returned by bash/process so output is not repeated.",
            (
                "Do not busy-poll unchanged processes; honor the suggested poll delay reported by bash/process and "
                "continue other work when possible."
            ),
            (
                "When a managed process result is required for the current request, treat status=running as unfinished "
                "work: continue useful independent work when possible, then poll to a terminal state and inspect its "
                "final output before claiming completion."
            ),
            (
                "Leave a process detached only when the user explicitly requested it or its result is not required; "
                "report the process ID and current status."
            ),
        ],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_process(
            service,
            owner,
            tid,
            args,
            signal,
            on_update,
            ctx,
        ),
        render_call=_render_process_call,
    )


def create_process_tool(service: ProcessSessionService, owner: ProcessOwner) -> AgentTool:
    return wrap_tool_definition(create_process_tool_definition(service, owner))


def _execute_process(
    service: ProcessSessionService,
    owner: ProcessOwner,
    _tool_call_id,
    raw_args,
    signal=None,
    on_update=None,
    ctx=None,
) -> AgentToolResult:
    args = _validate_args(raw_args)
    action = args["action"]
    if signal is not None and getattr(signal, "aborted", False):
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
    if action == "poll":
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
        return f"Process {snapshot.session_id} failed; {position}."
    return (
        f"Process {snapshot.session_id} is {snapshot.state.value}; {position}. "
        f"Suggested poll delay: {snapshot.suggested_poll_delay_ms} ms."
    )


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
