"""Named tmux sessions for long-lived terminal work."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.capabilities import WorkspaceCapability
from travis.coding_agent.tools.bash import get_shell_env
from travis.coding_agent.tools.truncate import truncate_tail, truncation_to_details
from travis.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition


TMUX_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
TMUX_RESOLVED_NAME_PATTERN = re.compile(r"^travis234-[0-9a-f]{12}-")
TMUX_CAPTURE_LINES_DEFAULT = 200
TMUX_CAPTURE_LINES_MAX = 2000
TMUX_CAPTURE_BYTES_MAX = 50 * 1024
TMUX_ERROR_MAX_LINES = 20
TMUX_ERROR_MAX_BYTES = 4000

TMUX_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["start", "send", "capture", "list", "stop"],
        },
        "name": {
            "type": "string",
            "description": (
                "Logical tmux session name or exact resolved name returned by this tool"
            ),
        },
        "command": {
            "type": "string",
            "description": "Command for a detached new session",
        },
        "cwd": {
            "type": "string",
            "description": "Directory for start; defaults to the workspace",
        },
        "input": {"type": "string", "description": "Literal keys for send"},
        "enter": {
            "type": "boolean",
            "description": "Send Enter after literal input; defaults to true",
        },
        "lines": {
            "type": "integer",
            "minimum": 1,
            "maximum": TMUX_CAPTURE_LINES_MAX,
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}

_ACTION_FIELDS = {
    "start": {"action", "name", "command", "cwd"},
    "send": {"action", "name", "input", "enter"},
    "capture": {"action", "name", "lines"},
    "list": {"action"},
    "stop": {"action", "name"},
}


@dataclass(frozen=True)
class TmuxOperations:
    which: Callable[[str], str | None]
    run: Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=get_shell_env(),
    )


DEFAULT_TMUX_OPERATIONS = TmuxOperations(which=shutil.which, run=_default_run)


def _workspace_prefix(workspace: WorkspaceCapability) -> str:
    digest = hashlib.sha256(str(workspace.root).encode("utf-8")).hexdigest()[:12]
    return f"travis234-{digest}-"


def _session_identity(
    workspace: WorkspaceCapability,
    name: object,
) -> tuple[str, str]:
    prefix = _workspace_prefix(workspace)
    if isinstance(name, str) and name.startswith(prefix):
        logical_name = name[len(prefix) :]
        if TMUX_NAME_PATTERN.fullmatch(logical_name) is not None:
            return logical_name, name
    if isinstance(name, str) and TMUX_RESOLVED_NAME_PATTERN.match(name):
        raise ValueError("tmux session name belongs to another workspace")
    if not isinstance(name, str) or TMUX_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(
            "tmux name must match [A-Za-z0-9][A-Za-z0-9_-]{0,47}"
        )
    return name, f"{prefix}{name}"


def _session_name(workspace: WorkspaceCapability, name: object) -> str:
    return _session_identity(workspace, name)[1]


def _validated_args(
    workspace: WorkspaceCapability,
    raw_args: object,
) -> tuple[str, dict[str, object]]:
    if not isinstance(raw_args, Mapping):
        raise ValueError("tmux arguments must be an object")
    args = dict(raw_args)
    action = args.get("action")
    if not isinstance(action, str) or action not in _ACTION_FIELDS:
        raise ValueError(f"unknown tmux action: {action}")
    unexpected = sorted(set(args) - _ACTION_FIELDS[action])
    if unexpected:
        raise ValueError(
            f"{action} does not accept {', '.join(unexpected)}"
        )
    if action != "list":
        _session_name(workspace, args.get("name"))
    if action == "start":
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("tmux start requires command")
        if "cwd" in args and not isinstance(args["cwd"], str):
            raise ValueError("tmux start cwd must be a string")
    elif action == "send":
        if not isinstance(args.get("input"), str):
            raise ValueError("tmux send requires input as a string")
        if "enter" in args and not isinstance(args["enter"], bool):
            raise ValueError("tmux send enter must be a boolean")
    elif action == "capture":
        lines = args.get("lines", TMUX_CAPTURE_LINES_DEFAULT)
        if (
            isinstance(lines, bool)
            or not isinstance(lines, int)
            or not 1 <= lines <= TMUX_CAPTURE_LINES_MAX
        ):
            raise ValueError("tmux capture lines must be an integer from 1 to 2000")
    return action, args


def _tool_result(message: str, details: dict[str, object]) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)], details=details)


def _bounded_command_error(argv: list[str], completed: subprocess.CompletedProcess[str]) -> RuntimeError:
    prefix = f"tmux command failed ({completed.returncode}): "
    byte_budget = max(1, TMUX_ERROR_MAX_BYTES - len(prefix.encode("utf-8")))
    stderr = completed.stderr or completed.stdout or "unknown tmux error"
    bounded = truncate_tail(
        stderr.strip(),
        max_lines=TMUX_ERROR_MAX_LINES,
        max_bytes=byte_budget,
    ).content
    return RuntimeError(f"{prefix}{bounded}".rstrip())


def _run_checked(
    operations: TmuxOperations,
    argv: list[str],
) -> subprocess.CompletedProcess[str]:
    completed = operations.run(argv)
    if completed.returncode != 0:
        raise _bounded_command_error(argv, completed)
    return completed


def _has_session(
    operations: TmuxOperations,
    executable: str,
    session_name: str,
) -> bool:
    argv = [executable, "has-session", "-t", session_name]
    completed = operations.run(argv)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise _bounded_command_error(argv, completed)


def _execute_tmux(
    workspace: WorkspaceCapability,
    operations: TmuxOperations,
    raw_args: object,
) -> AgentToolResult:
    action, args = _validated_args(workspace, raw_args)
    executable = operations.which("tmux")
    if not executable:
        raise RuntimeError("tmux executable not found; install tmux and retry")

    if action == "list":
        argv = [executable, "list-sessions", "-F", "#{session_name}"]
        completed = operations.run(argv)
        no_server = completed.returncode == 1 and "no server running" in completed.stderr.lower()
        if completed.returncode != 0 and not no_server:
            raise _bounded_command_error(argv, completed)
        prefix = _workspace_prefix(workspace)
        sessions = [] if no_server else [
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip().startswith(prefix)
        ]
        return _tool_result(
            "No Travis234 tmux sessions." if not sessions else "\n".join(sessions),
            {"action": "list", "sessions": sessions},
        )

    logical_name, resolved_name = _session_identity(workspace, args["name"])
    details: dict[str, object] = {
        "action": action,
        "name": logical_name,
        "sessionName": resolved_name,
    }

    if action == "start":
        requested_cwd = str(args.get("cwd", "."))
        resolved_cwd = workspace.resolve(requested_cwd, "execute")
        if not resolved_cwd.exists():
            raise FileNotFoundError(f"tmux cwd does not exist: {resolved_cwd}")
        if not resolved_cwd.is_dir():
            raise NotADirectoryError(f"tmux cwd is not a directory: {resolved_cwd}")
        if _has_session(operations, executable, resolved_name):
            raise RuntimeError(f"tmux session already exists: {resolved_name}")
        _run_checked(
            operations,
            [
                executable,
                "new-session",
                "-d",
                "-s",
                resolved_name,
                "-c",
                str(resolved_cwd),
            ],
        )
        try:
            _run_checked(
                operations,
                [
                    executable,
                    "set-option",
                    "-w",
                    "-t",
                    resolved_name,
                    "remain-on-exit",
                    "on",
                ],
            )
            _run_checked(
                operations,
                [
                    executable,
                    "respawn-pane",
                    "-k",
                    "-t",
                    resolved_name,
                    "-c",
                    str(resolved_cwd),
                    "--",
                    str(args["command"]),
                ],
            )
        except Exception:
            operations.run([executable, "kill-session", "-t", resolved_name])
            raise
        details["cwd"] = str(resolved_cwd)
        return _tool_result(
            (
                "Started durable tmux session. "
                f"Logical name: {logical_name}. "
                f"Resolved native name: {resolved_name}. "
                "Output remains capturable after command exit; stop the session explicitly."
            ),
            details,
        )

    if action == "stop":
        if not _has_session(operations, executable, resolved_name):
            details["alreadyAbsent"] = True
            return _tool_result(f"Tmux session already absent: {resolved_name}.", details)
        _run_checked(
            operations,
            [executable, "kill-session", "-t", resolved_name],
        )
        details["alreadyAbsent"] = False
        return _tool_result(f"Stopped tmux session {resolved_name}.", details)

    if not _has_session(operations, executable, resolved_name):
        raise RuntimeError(f"tmux session is not running: {resolved_name}")

    if action == "send":
        _run_checked(
            operations,
            [
                executable,
                "send-keys",
                "-t",
                resolved_name,
                "-l",
                "--",
                str(args["input"]),
            ],
        )
        enter = bool(args.get("enter", True))
        if enter:
            _run_checked(
                operations,
                [executable, "send-keys", "-t", resolved_name, "Enter"],
            )
        details["enter"] = enter
        return _tool_result(f"Sent input to tmux session {resolved_name}.", details)

    lines = int(args.get("lines", TMUX_CAPTURE_LINES_DEFAULT))
    completed = _run_checked(
        operations,
        [
            executable,
            "capture-pane",
            "-p",
            "-t",
            resolved_name,
            "-S",
            f"-{lines}",
        ],
    )
    truncation = truncate_tail(
        completed.stdout,
        max_lines=lines,
        max_bytes=TMUX_CAPTURE_BYTES_MAX,
    )
    details["lines"] = lines
    if truncation.truncated:
        details["truncation"] = truncation_to_details(truncation)
    return _tool_result(truncation.content or "(no tmux output)", details)


def create_tmux_tool_definition(
    cwd: str,
    operations: TmuxOperations | None = None,
    workspace: WorkspaceCapability | None = None,
) -> ToolDefinition:
    resolved_operations = operations or DEFAULT_TMUX_OPERATIONS
    resolved_workspace = workspace or WorkspaceCapability(Path(cwd))
    return ToolDefinition(
        name="tmux",
        label="tmux",
        description=(
            "Manage named, detached tmux sessions for development servers, watchers, REPLs, test loops, and long builds. "
            "Actions: start, send literal input, capture recent output, list Travis234 sessions, and stop."
        ),
        parameters=TMUX_SCHEMA,
        prompt_snippet="Manage named long-lived tmux sessions",
        prompt_guidelines=[
            "Use tmux for development servers, watchers, REPLs, test loops, long builds, and work that must survive turns.",
            "For follow-up tmux tool calls, use either the logical name or the exact resolved name returned by this tool; use the resolved name for native tmux commands.",
            "Use capture to collect evidence and stop sessions explicitly when they are no longer needed.",
        ],
        execute=lambda _tid, args, signal=None, on_update=None, ctx=None: _execute_tmux(
            resolved_workspace,
            resolved_operations,
            args,
        ),
        execution_mode="sequential",
    )


def create_tmux_tool(
    cwd: str,
    operations: TmuxOperations | None = None,
    workspace: WorkspaceCapability | None = None,
) -> AgentTool:
    return wrap_tool_definition(
        create_tmux_tool_definition(cwd, operations, workspace),
        lambda: ToolContext(cwd=cwd),
    )


__all__ = [
    "TMUX_SCHEMA",
    "TmuxOperations",
    "create_tmux_tool",
    "create_tmux_tool_definition",
]
