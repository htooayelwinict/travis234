"""bash tool. Port of pi/packages/coding-agent/src/core/tools/bash.ts."""

from __future__ import annotations

import os
import signal as signal_module
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from appv23.agent.types import AgentTool, AgentToolResult
from appv23.ai.types import TextContent
from appv23.coding_agent.tools.output_accumulator import OutputAccumulator, OutputSnapshot
from appv23.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncation_to_details,
)
from appv23.coding_agent.tools.trust import mark_agent_written_file
from appv23.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

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
            env=_with_python_bin_on_path(options.env or os.environ.copy()),
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


def _with_python_bin_on_path(env: dict[str, str]) -> dict[str, str]:
    python_bin = os.path.dirname(sys.executable)
    current_path = env.get("PATH", "")
    if python_bin and python_bin not in current_path.split(os.pathsep):
        env = dict(env)
        env["PATH"] = python_bin + (os.pathsep + current_path if current_path else "")
    return env


def _resolve_spawn_context(command: str, cwd: str, spawn_hook: BashSpawnHook | None = None) -> BashSpawnContext:
    context = BashSpawnContext(command=command, cwd=cwd, env=_with_python_bin_on_path(os.environ.copy()))
    return spawn_hook(context) if spawn_hook else context


def _format_output(output: OutputAccumulator, snapshot: OutputSnapshot, empty_text: str = "(no output)") -> tuple[str, dict | None]:
    truncation = snapshot.truncation
    text = snapshot.content if snapshot.content else empty_text
    details = None
    if truncation.truncated:
        details = {"truncation": truncation_to_details(truncation), "fullOutputPath": snapshot.full_output_path}
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
                    "truncation": truncation_to_details(snapshot.truncation) if snapshot.truncation.truncated else None,
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
        _mark_full_output_path(details, output_text, ctx)
        _mark_obvious_bash_write_targets(spawn_context.command, spawn_context.cwd, ctx)
        if exit_code is not None and exit_code != 0:
            raise RuntimeError(_append_status(output_text, f"Command exited with code {exit_code}"))
        return AgentToolResult(content=[TextContent(text=output_text)], details=details)
    finally:
        update_dirty = False


def _mark_obvious_bash_write_targets(command: str, cwd: str, ctx) -> None:
    trust_state = _ctx_trust_state(ctx)
    for target in _extract_obvious_write_targets(command, cwd):
        content = _read_bounded_text_file(target)
        if content is not None:
            mark_agent_written_file(target, content, trust_state)


def _extract_obvious_write_targets(command: str, cwd: str) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()

    def add_target(raw_target: str) -> None:
        target = raw_target.strip()
        if _ignored_bash_write_target(target):
            return
        absolute_target = target if os.path.isabs(target) else os.path.abspath(os.path.join(cwd, target))
        if absolute_target not in seen:
            seen.add(absolute_target)
            targets.append(absolute_target)

    for raw_line in _command_lines_without_heredoc_bodies(command):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for target in _extract_redirect_targets_from_line(line):
            add_target(target)
        tokens = _shellish_words_and_operators(line)
        for index, token in enumerate(tokens):
            if token == "tee":
                for candidate in tokens[index + 1 :]:
                    if candidate in {"|", ";", "&&", "||", ">", ">>", "<", "<<"}:
                        break
                    if not candidate.startswith("-"):
                        add_target(candidate)
    return targets


def _command_lines_without_heredoc_bodies(command: str) -> list[str]:
    lines: list[str] = []
    pending_delimiters: list[str] = []
    for raw_line in command.splitlines():
        stripped = raw_line.strip()
        if pending_delimiters:
            if stripped == pending_delimiters[0]:
                pending_delimiters.pop(0)
            continue
        lines.append(raw_line)
        pending_delimiters.extend(_extract_heredoc_delimiters(raw_line))
    return lines


def _extract_heredoc_delimiters(line: str) -> list[str]:
    delimiters: list[str] = []
    index = 0
    while index < len(line):
        index = _next_unquoted_operator(line, "<<", index)
        if index < 0:
            break
        index += 2
        if index < len(line) and line[index] == "-":
            index += 1
        while index < len(line) and line[index].isspace():
            index += 1
        delimiter, index = _read_shellish_word(line, index)
        if delimiter:
            delimiters.append(delimiter)
    return delimiters


def _extract_redirect_targets_from_line(line: str) -> list[str]:
    targets: list[str] = []
    index = 0
    while index < len(line):
        index = _next_unquoted_redirection(line, index)
        if index < 0:
            break
        index += 2 if index + 1 < len(line) and line[index + 1] == ">" else 1
        while index < len(line) and line[index].isspace():
            index += 1
        target, index = _read_shellish_word(line, index)
        if target:
            targets.append(target)
    return targets


def _next_unquoted_redirection(line: str, start: int) -> int:
    index = start
    quote: str | None = None
    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == ">" and _looks_like_redirect_position(line, index):
            return index
        index += 1
    return -1


def _next_unquoted_operator(line: str, operator: str, start: int) -> int:
    index = start
    quote: str | None = None
    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if line.startswith(operator, index):
            return index
        index += 1
    return -1


def _looks_like_redirect_position(line: str, index: int) -> bool:
    if index == 0 or line[index - 1].isspace():
        return True
    previous = index - 1
    while previous >= 0 and line[previous].isspace():
        previous -= 1
    if previous < 0 or line[previous] in {"|", "&", ";", "("}:
        return True
    if line[previous].isdigit():
        before_fd = previous - 1
        while before_fd >= 0 and line[before_fd].isdigit():
            before_fd -= 1
        return before_fd < 0 or line[before_fd].isspace() or line[before_fd] in {"|", "&", ";", "("}
    return False


def _shellish_words_and_operators(line: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(line):
        while index < len(line) and line[index].isspace():
            index += 1
        if index >= len(line):
            break
        if line.startswith("&&", index) or line.startswith("||", index) or line.startswith(">>", index) or line.startswith(
            "<<", index
        ):
            tokens.append(line[index : index + 2])
            index += 2
            continue
        if line[index] in {"|", ";", ">", "<"}:
            tokens.append(line[index])
            index += 1
            continue
        word, index = _read_shellish_word(line, index)
        if word:
            tokens.append(word)
        else:
            index += 1
    return tokens


def _read_shellish_word(line: str, start: int) -> tuple[str, int]:
    chars: list[str] = []
    index = start
    quote: str | None = None
    while index < len(line):
        char = line[index]
        if char == "\\":
            if index + 1 < len(line):
                chars.append(line[index + 1])
                index += 2
            else:
                index += 1
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                chars.append(char)
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char.isspace() or char in {"|", ";", ">", "<", "&"}:
            break
        chars.append(char)
        index += 1
    return "".join(chars), index


def _ignored_bash_write_target(target: str) -> bool:
    return not target or target.startswith("-") or target.startswith("&") or target in {"/dev/null", "&1", "&2"}


def _read_bounded_text_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(DEFAULT_MAX_BYTES)
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _mark_full_output_path(details, content: str, ctx) -> None:
    if not isinstance(details, dict):
        return
    full_output_path = details.get("fullOutputPath")
    if not isinstance(full_output_path, str) or not full_output_path:
        return
    mark_agent_written_file(full_output_path, content, _ctx_trust_state(ctx))


def _ctx_trust_state(ctx) -> dict | None:
    trust_state = ctx.get("trust_state") if isinstance(ctx, dict) else getattr(ctx, "trust_state", None)
    return trust_state if isinstance(trust_state, dict) else None


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
