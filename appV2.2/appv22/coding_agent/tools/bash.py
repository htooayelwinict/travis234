"""bash tool. Port of pi/packages/coding-agent/src/core/tools/bash.ts."""

from __future__ import annotations

import os
import signal as signal_module
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.output_accumulator import OutputAccumulator, OutputSnapshot
from appv22.coding_agent.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Bash command to execute"},
        "timeout": {"type": "number", "description": "Timeout in seconds (optional, no default timeout)"},
    },
    "required": ["command"],
}

BASH_UPDATE_THROTTLE_SECONDS = 0.1


@dataclass(frozen=True)
class BashExecOptions:
    on_data: Callable[[bytes], None]
    signal: object | None = None
    timeout: float | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class BashOperations:
    exec: Callable[[str, str, BashExecOptions], dict[str, int | None]]


@dataclass(frozen=True)
class BashSpawnContext:
    command: str
    cwd: str
    env: dict[str, str]


BashSpawnHook = Callable[[BashSpawnContext], BashSpawnContext]


def _coerce_exec_options(options: BashExecOptions | dict) -> BashExecOptions:
    if isinstance(options, BashExecOptions):
        return options
    return BashExecOptions(
        on_data=options["on_data"],
        signal=options.get("signal"),
        timeout=options.get("timeout"),
        env=options.get("env"),
    )


def _is_aborted(signal) -> bool:
    return signal is not None and getattr(signal, "aborted", False)


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal_module.SIGTERM)
        else:
            process.kill()
    except ProcessLookupError:
        return
    except Exception:
        process.kill()


def _reader_thread(pipe, on_data: Callable[[bytes], None]) -> threading.Thread:
    def read_loop() -> None:
        try:
            while True:
                chunk = pipe.readline()
                if not chunk:
                    break
                on_data(chunk)
        except ValueError:
            return

    thread = threading.Thread(target=read_loop, daemon=True)
    thread.start()
    return thread


def create_local_bash_operations(shell_path: str | None = None) -> BashOperations:
    def exec_command(command: str, cwd: str, options: BashExecOptions | dict) -> dict[str, int | None]:
        options = _coerce_exec_options(options)
        if not os.path.exists(cwd):
            raise RuntimeError(f"Working directory does not exist: {cwd}\nCannot execute bash commands.")
        if shell_path is not None and not os.path.exists(shell_path):
            raise RuntimeError(f"Custom shell path not found: {shell_path}")
        if _is_aborted(options.signal):
            raise RuntimeError("aborted")

        shell = shell_path or os.environ.get("SHELL") or "/bin/bash"
        process = subprocess.Popen(
            [shell, "-c", command],
            cwd=cwd,
            env=options.env or os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        stdout_thread = _reader_thread(process.stdout, options.on_data) if process.stdout else None
        stderr_thread = _reader_thread(process.stderr, options.on_data) if process.stderr else None
        started_at = time.monotonic()
        timed_out = False
        try:
            while process.poll() is None:
                if _is_aborted(options.signal):
                    _kill_process_tree(process)
                    raise RuntimeError("aborted")
                if options.timeout is not None and options.timeout > 0 and time.monotonic() - started_at >= options.timeout:
                    timed_out = True
                    _kill_process_tree(process)
                    raise RuntimeError(f"timeout:{options.timeout:g}")
                time.sleep(0.01)
            return {"exit_code": process.returncode}
        finally:
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            for thread in (stdout_thread, stderr_thread):
                if thread:
                    thread.join(timeout=0.5)
            if process.poll() is None:
                _kill_process_tree(process)
            if timed_out:
                process.wait(timeout=0.5)

    return BashOperations(exec=exec_command)


def _resolve_spawn_context(command: str, cwd: str, spawn_hook: BashSpawnHook | None = None) -> BashSpawnContext:
    context = BashSpawnContext(command=command, cwd=cwd, env=os.environ.copy())
    return spawn_hook(context) if spawn_hook else context


def _format_output(output: OutputAccumulator, snapshot: OutputSnapshot, empty_text: str = "(no output)") -> tuple[str, dict | None]:
    truncation = snapshot.truncation
    text = snapshot.content if snapshot.content else empty_text
    details = None
    if truncation.truncated:
        details = {"truncation": truncation, "fullOutputPath": snapshot.full_output_path}
        start_line = truncation.total_lines - truncation.output_lines + 1
        end_line = truncation.total_lines
        if truncation.last_line_partial:
            last_line_size = format_size(output.get_last_line_bytes())
            text += (
                f"\n\n[Showing last {format_size(truncation.output_bytes)} of line {end_line} "
                f"(line is {last_line_size}). Full output: {snapshot.full_output_path}]"
            )
        elif truncation.truncated_by == "lines":
            text += f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines}. Full output: {snapshot.full_output_path}]"
        else:
            text += (
                f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines} "
                f"({format_size(DEFAULT_MAX_BYTES)} limit). Full output: {snapshot.full_output_path}]"
            )
    return text, details


def _append_status(text: str, status: str) -> str:
    return f"{text}\n\n{status}" if text else status


def _execute_bash(
    cwd: str,
    operations: BashOperations,
    command_prefix: str | None,
    spawn_hook: BashSpawnHook | None,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
):
    command = args["command"]
    timeout = args.get("timeout")
    resolved_command = f"{command_prefix}\n{command}" if command_prefix else command
    spawn_context = _resolve_spawn_context(resolved_command, cwd, spawn_hook)
    output = OutputAccumulator(temp_file_prefix="pi-bash")
    update_dirty = False
    last_update_at = 0.0

    def emit_output_update() -> None:
        nonlocal update_dirty, last_update_at
        if not on_update or not update_dirty:
            return
        update_dirty = False
        last_update_at = time.monotonic()
        snapshot = output.snapshot(persist_if_truncated=True)
        on_update(
            AgentToolResult(
                content=[TextContent(text=snapshot.content or "")],
                details={
                    "truncation": snapshot.truncation if snapshot.truncation.truncated else None,
                    "fullOutputPath": snapshot.full_output_path,
                },
            )
        )

    def schedule_output_update() -> None:
        nonlocal update_dirty
        if not on_update:
            return
        update_dirty = True
        if time.monotonic() - last_update_at >= BASH_UPDATE_THROTTLE_SECONDS:
            emit_output_update()

    if on_update:
        on_update(AgentToolResult(content=[], details=None))

    def handle_data(data: bytes) -> None:
        output.append(data)
        schedule_output_update()

    def finish_output() -> OutputSnapshot:
        output.finish()
        emit_output_update()
        snapshot = output.snapshot(persist_if_truncated=True)
        output.close_temp_file()
        return snapshot

    try:
        try:
            result = operations.exec(
                spawn_context.command,
                spawn_context.cwd,
                BashExecOptions(
                    on_data=handle_data,
                    signal=signal,
                    timeout=timeout,
                    env=spawn_context.env,
                ),
            )
            exit_code = result.get("exit_code")
        except RuntimeError as error:
            snapshot = finish_output()
            text, _details = _format_output(output, snapshot, "")
            message = str(error)
            if message == "aborted":
                raise RuntimeError(_append_status(text, "Command aborted")) from error
            if message.startswith("timeout:"):
                timeout_seconds = message.split(":", 1)[1]
                raise RuntimeError(_append_status(text, f"Command timed out after {timeout_seconds} seconds")) from error
            raise

        snapshot = finish_output()
        output_text, details = _format_output(output, snapshot)
        if exit_code is not None and exit_code != 0:
            raise RuntimeError(_append_status(output_text, f"Command exited with code {exit_code}"))
        return AgentToolResult(content=[TextContent(text=output_text)], details=details)
    finally:
        update_dirty = False


def create_bash_tool_definition(
    cwd: str,
    operations: BashOperations | None = None,
    command_prefix: str | None = None,
    shell_path: str | None = None,
    spawn_hook: BashSpawnHook | None = None,
) -> ToolDefinition:
    ops = operations or create_local_bash_operations(shell_path=shell_path)
    return ToolDefinition(
        name="bash",
        label="bash",
        description=(
            f"Execute a bash command in the current working directory. Returns stdout and stderr. Output is "
            f"truncated to last {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
            "If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds."
        ),
        parameters=BASH_SCHEMA,
        prompt_snippet="Execute bash commands (ls, grep, find, etc.)",
        prompt_guidelines=["Use bash for commands; prefer rg over grep -r."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_bash(
            cwd, ops, command_prefix, spawn_hook, tid, args, signal, on_update, ctx
        ),
        render_call=lambda args, ctx=None: f"bash {args.get('command', '')}",
    )


def create_bash_tool(
    cwd: str,
    operations: BashOperations | None = None,
    command_prefix: str | None = None,
    shell_path: str | None = None,
    spawn_hook: BashSpawnHook | None = None,
) -> AgentTool:
    return wrap_tool_definition(
        create_bash_tool_definition(
            cwd,
            operations=operations,
            command_prefix=command_prefix,
            shell_path=shell_path,
            spawn_hook=spawn_hook,
        ),
        lambda: ToolContext(cwd=cwd),
    )
