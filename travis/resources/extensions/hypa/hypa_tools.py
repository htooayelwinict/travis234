"""Hypa-backed shell and file tools for the optional first-party extension."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from . import HypaConfig
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.tools.types import ToolDefinition


DEFAULT_MAX_BYTES = 50 * 1024
DEFAULT_MAX_LINES = 2000
_POSIX_SAFE = re.compile(r"^[A-Za-z0-9_./:=@,+%^-]+$")
_WINDOWS_SAFE = re.compile(r"^[A-Za-z0-9_./:=@,+-]+$")


def shell_quote(value: str, platform_name: str | None = None) -> str:
    platform_name = platform_name or ("win32" if os.name == "nt" else "posix")
    windows = platform_name == "win32"
    if not value:
        return '""' if windows else "''"
    if (_WINDOWS_SAFE if windows else _POSIX_SAFE).fullmatch(value):
        return value
    if windows:
        return f'"{value.replace(chr(34), chr(34) * 2)}"'
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _normalize_path(value: str) -> str:
    path = value[1:] if value.startswith("@") else value
    if path == "~":
        return str(Path.home())
    if path.startswith("~/"):
        return str(Path.home()) + path[1:]
    return path


def build_read_command(path: str, offset: object = None, limit: object = None) -> str:
    quoted_path = shell_quote(_normalize_path(path))
    if offset is None and limit is None:
        return f"cat -- {quoted_path}"
    start = max(1, int(offset or 1))
    end = str(start + max(1, int(limit)) - 1) if limit is not None else "$"
    return f"sed -n {shell_quote(f'{start},{end}p')} -- {quoted_path}"


def build_grep_command(params: Mapping[str, Any]) -> str:
    args = ["rg", "--heading", "--line-number", "--color=never"]
    if params.get("ignoreCase"):
        args.append("--ignore-case")
    if params.get("literal"):
        args.append("--fixed-strings")
    if params.get("context") is not None:
        args.extend(("--context", str(max(0, int(params["context"])))))
    if params.get("limit") is not None:
        args.extend(("--max-count", str(max(1, int(params["limit"])))))
    if params.get("glob"):
        args.extend(("--glob", str(params["glob"])))
    args.extend(("-e", str(params["pattern"]), "--", _normalize_path(str(params.get("path") or "."))))
    return " ".join(shell_quote(arg) for arg in args)


def build_find_command(params: Mapping[str, Any]) -> str:
    args = (
        "rg",
        "--files",
        "--glob",
        str(params.get("pattern") or "*"),
        _normalize_path(str(params.get("path") or ".")),
    )
    return " ".join(shell_quote(arg) for arg in args)


def limit_stdout_lines(stdout: str, limit: object = None) -> str:
    if limit is None:
        return stdout
    maximum = max(1, int(float(limit)))
    lines = [line for line in stdout.replace("\r\n", "\n").split("\n") if line]
    selected = lines[:maximum]
    return "\n".join(selected) + ("\n" if selected else "")


def build_ls_command(params: Mapping[str, Any]) -> str:
    flags = ("" if params.get("long") is False else "l") + ("a" if params.get("all") else "")
    args = ["ls"]
    if flags:
        args.append(f"-{flags}")
    args.extend(("--", _normalize_path(str(params.get("path") or "."))))
    return " ".join(shell_quote(arg) for arg in args)


def _exec_target(binary: str, args: list[str]) -> tuple[str, list[str]]:
    lowered = binary.lower()
    if lowered.endswith(".js"):
        runtime = shutil.which("node") or shutil.which("bun")
        if runtime is None:
            raise RuntimeError("HYPA_BIN points to JavaScript but neither node nor bun is available")
        return runtime, [binary, *args]
    if os.name == "nt" and lowered.endswith((".cmd", ".bat")):
        return "cmd", ["/c", binary, *args]
    return binary, args


def _run_hypa(
    runner: ExtensionRunner,
    config: HypaConfig,
    command: str,
    timeout_ms: object = None,
    raw: bool = False,
    signal: object = None,
) -> dict[str, object]:
    args: list[str] = []
    timeout = max(1, int(timeout_ms)) if timeout_ms is not None else None
    if timeout is not None:
        args.extend(("--timeout-ms", str(timeout)))
    if raw:
        args.extend(("raw", *command.strip().split()))
    else:
        args.extend(("-c", command))
    executable, final_args = _exec_target(config.binary, args)
    return runner.exec(executable, final_args, {"signal": signal, "timeout": timeout})


def _select_output(text: str, *, prefer_tail: bool) -> tuple[str, dict[str, object]]:
    lines = text.split("\n")
    selected = lines[-DEFAULT_MAX_LINES:] if prefer_tail else lines[:DEFAULT_MAX_LINES]
    content = "\n".join(selected)
    encoded = content.encode("utf-8")
    if len(encoded) > DEFAULT_MAX_BYTES:
        encoded = encoded[-DEFAULT_MAX_BYTES:] if prefer_tail else encoded[:DEFAULT_MAX_BYTES]
        content = encoded.decode("utf-8", errors="ignore")
    details = {
        "truncated": len(selected) < len(lines) or len(text.encode("utf-8")) > DEFAULT_MAX_BYTES,
        "totalLines": len(lines),
        "outputLines": len(content.split("\n")) if content else 0,
        "totalBytes": len(text.encode("utf-8")),
        "outputBytes": len(content.encode("utf-8")),
    }
    return content, details


def _tool_result(result: Mapping[str, object], command: str, *, prefer_tail: bool = False) -> AgentToolResult:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    combined = "\n".join(part for part in (stdout, stderr) if part)
    text, truncation = _select_output(combined, prefer_tail=prefer_tail)
    details: dict[str, object] = {
        "source": "hypa-cli",
        "command": command,
        "exitCode": int(result.get("code") or 0),
    }
    if truncation["truncated"]:
        directory = Path(tempfile.mkdtemp(prefix="travis234-hypa-"))
        output_path = directory / "output.txt"
        output_path.write_text(combined, encoding="utf-8")
        details["truncation"] = truncation
        details["fullOutputPath"] = str(output_path)
        text += (
            f"\n\n[Output truncated: showing {truncation['outputLines']} of "
            f"{truncation['totalLines']} lines. Full output saved to: {output_path}]"
        )
    if result.get("killed") is True:
        text += "\n\n[Hypa command timed out or was killed]"
    if not text:
        text = f"(exit {details['exitCode']}, no output)"
    return AgentToolResult(content=[TextContent(text=text)], details=details)


def _schema(properties: dict[str, object], required: list[str] | None = None) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _parameter(kind: str, description: str) -> dict[str, str]:
    return {"type": kind, "description": description}


def register_hypa_tools(runner: ExtensionRunner, config: HypaConfig) -> None:
    common_description = (
        f"Output is capped at {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB; "
        "full output is saved when truncated."
    )

    def shell_execute(_tool_call_id, params, signal=None, _on_update=None, _ctx=None):
        command = str(params["command"])
        result = _run_hypa(
            runner,
            config,
            command,
            params.get("timeoutMs"),
            bool(params.get("raw")),
            signal,
        )
        return _tool_result(result, command, prefer_tail=True)

    runner.register_tool(
        ToolDefinition(
            name="hypa_shell",
            label="hypa_shell",
            description=f"Run shell commands through local deterministic compression. {common_description}",
            prompt_snippet="Run shell commands through local deterministic compression",
            prompt_guidelines=[
                "Use hypa_shell when compressed command output is preferred.",
                "Use hypa_read rather than shell file-preview commands.",
            ],
            parameters=_schema(
                {
                    "command": _parameter("string", "Shell command to execute through Hypa"),
                    "timeoutMs": _parameter("number", "Timeout in milliseconds"),
                    "raw": _parameter("boolean", "Run without compression"),
                },
                ["command"],
            ),
            execute=shell_execute,
        )
    )

    def read_execute(_tool_call_id, params, signal=None, _on_update=None, _ctx=None):
        command = build_read_command(str(params["path"]), params.get("offset"), params.get("limit"))
        return _tool_result(_run_hypa(runner, config, command, signal=signal), command)

    runner.register_tool(
        ToolDefinition(
            name="hypa_read",
            label="hypa_read",
            description=f"Read files through local deterministic compression. {common_description}",
            prompt_snippet="Read file contents through local deterministic compression",
            prompt_guidelines=["Use hypa_read for compressed file inspection."],
            parameters=_schema(
                {
                    "path": _parameter("string", "Path to read"),
                    "offset": _parameter("number", "One-indexed starting line"),
                    "limit": _parameter("number", "Maximum lines"),
                    "maxTokens": _parameter("number", "Approximate result token target"),
                },
                ["path"],
            ),
            execute=read_execute,
        )
    )

    def grep_execute(_tool_call_id, params, signal=None, _on_update=None, _ctx=None):
        command = build_grep_command(params)
        result = _run_hypa(runner, config, command, params.get("timeoutMs"), signal=signal)
        return _tool_result(result, command)

    runner.register_tool(
        ToolDefinition(
            name="hypa_grep",
            label="hypa_grep",
            description=f"Search file contents through local deterministic compression. {common_description}",
            prompt_snippet="Search file contents through local deterministic compression",
            parameters=_schema(
                {
                    "pattern": _parameter("string", "Search pattern"),
                    "path": _parameter("string", "Directory or file"),
                    "glob": _parameter("string", "File glob"),
                    "ignoreCase": _parameter("boolean", "Case-insensitive search"),
                    "literal": _parameter("boolean", "Treat pattern literally"),
                    "context": _parameter("number", "Context lines"),
                    "limit": _parameter("number", "Maximum matches"),
                    "timeoutMs": _parameter("number", "Timeout in milliseconds"),
                },
                ["pattern"],
            ),
            execute=grep_execute,
        )
    )

    def find_execute(_tool_call_id, params, signal=None, _on_update=None, _ctx=None):
        command = build_find_command(params)
        result = _run_hypa(runner, config, command, params.get("timeoutMs"), signal=signal)
        result = {**result, "stdout": limit_stdout_lines(str(result.get("stdout") or ""), params.get("limit"))}
        return _tool_result(result, command)

    runner.register_tool(
        ToolDefinition(
            name="hypa_find",
            label="hypa_find",
            description=f"Find files through local deterministic compression. {common_description}",
            prompt_snippet="Find files through local deterministic compression",
            parameters=_schema(
                {
                    "pattern": _parameter("string", "File glob"),
                    "path": _parameter("string", "Directory to search"),
                    "limit": _parameter("number", "Maximum paths"),
                    "timeoutMs": _parameter("number", "Timeout in milliseconds"),
                }
            ),
            execute=find_execute,
        )
    )

    def ls_execute(_tool_call_id, params, signal=None, _on_update=None, _ctx=None):
        command = build_ls_command(params)
        return _tool_result(
            _run_hypa(runner, config, command, params.get("timeoutMs"), signal=signal),
            command,
        )

    runner.register_tool(
        ToolDefinition(
            name="hypa_ls",
            label="hypa_ls",
            description=f"List directory contents through local deterministic compression. {common_description}",
            prompt_snippet="List directory contents through local deterministic compression",
            parameters=_schema(
                {
                    "path": _parameter("string", "Directory to list"),
                    "all": _parameter("boolean", "Include dotfiles"),
                    "long": _parameter("boolean", "Use long listing"),
                    "timeoutMs": _parameter("number", "Timeout in milliseconds"),
                }
            ),
            execute=ls_execute,
        )
    )


__all__ = [
    "build_find_command",
    "build_grep_command",
    "build_ls_command",
    "build_read_command",
    "limit_stdout_lines",
    "register_hypa_tools",
    "shell_quote",
]
