"""bash tool. Port of pi/packages/coding-agent/src/core/tools/bash.ts."""

from __future__ import annotations

import os
import shlex
import shutil
import signal as signal_module
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from appv231.agent.types import AgentTool, AgentToolResult
from appv231.ai.types import TextContent
from appv231.coding_agent.artifacts import ArtifactRegistry
from appv231.coding_agent.config import get_bin_dir
from appv231.coding_agent.execution_backend import ExecutionBackend, TrustedLocalBackend
from appv231.coding_agent.processes.service import ProcessSessionService, ProcessTransportFactory
from appv231.coding_agent.processes.types import ProcessLaunchRequest, ProcessOwner, ProcessSnapshot, ProcessState
from appv231.coding_agent.tools.output_spool import OutputSnapshot, OutputSpool
from appv231.coding_agent.tools.process import format_process_wait_instruction
from appv231.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncation_to_details,
)
from appv231.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Bash command to execute"},
        "timeout": {"type": "number", "description": "Timeout in seconds (optional, no default timeout)"},
        "yield_time_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30000,
            "description": "Initial wait before returning a running process handle; this is not a timeout",
        },
        "tty": {"type": "boolean", "description": "Allocate a POSIX PTY for interactive commands"},
        "rows": {"type": "integer", "minimum": 2, "maximum": 200},
        "cols": {"type": "integer", "minimum": 20, "maximum": 500},
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


def create_local_bash_operations(
    shell_path: str | None = None,
    backend: ExecutionBackend | None = None,
) -> BashOperations:
    backend = backend or TrustedLocalBackend()

    def exec_command(command: str, cwd: str, options: BashExecOptions | dict) -> dict[str, int | None]:
        options = _coerce_exec_options(options)
        if not os.path.exists(cwd):
            raise RuntimeError(f"Working directory does not exist: {cwd}\nCannot execute bash commands.")
        if shell_path is not None and not os.path.exists(shell_path):
            raise RuntimeError(f"Custom shell path not found: {shell_path}")
        if _is_aborted(options.signal):
            raise RuntimeError("aborted")

        shell = shell_path or os.environ.get("SHELL") or "/bin/bash"
        process = backend.spawn(
            command,
            cwd,
            get_shell_env(options.env),
            {"shell_path": shell},
        )
        stdout_thread = _reader_thread(process.stdout, options.on_data) if process.stdout else None
        stderr_thread = _reader_thread(process.stderr, options.on_data) if process.stderr else None
        started_at = time.monotonic()
        try:
            while process.poll() is None:
                if _is_aborted(options.signal):
                    _kill_process_tree(process)
                    raise RuntimeError("aborted")
                if options.timeout is not None and options.timeout > 0 and time.monotonic() - started_at >= options.timeout:
                    _kill_process_tree(process)
                    raise RuntimeError(f"timeout:{options.timeout:g}")
                time.sleep(0.01)
            return {"exit_code": process.returncode}
        finally:
            if process.poll() is None:
                _kill_process_tree(process)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
            for thread in (stdout_thread, stderr_thread):
                if thread:
                    thread.join(timeout=0.5)
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except OSError:
                        pass

    return BashOperations(exec=exec_command)


def get_shell_env(env: dict[str, str] | None = None) -> dict[str, str]:
    shell_env = dict(env or os.environ)
    _strip_runtime_pythonpath(shell_env)
    path_key = next((key for key in shell_env if key.lower() == "path"), "PATH")
    current_path = shell_env.get(path_key, "")
    path_entries = [entry for entry in current_path.split(os.pathsep) if entry]
    path_entries = _without_runtime_python_bin(path_entries)
    bin_dir = get_bin_dir()
    _ensure_python_command_shim(bin_dir, path_entries)
    if bin_dir and bin_dir not in path_entries:
        path_entries.insert(0, bin_dir)
    shell_env[path_key] = os.pathsep.join(path_entries)
    return shell_env


getShellEnv = get_shell_env


def _strip_runtime_pythonpath(env: dict[str, str]) -> None:
    pythonpath = env.get("PYTHONPATH")
    if not pythonpath:
        return
    runtime_roots = _runtime_pythonpath_roots()
    kept_entries: list[str] = []
    for entry in pythonpath.split(os.pathsep):
        if not entry:
            continue
        if _resolve_pythonpath_entry(entry) in runtime_roots:
            continue
        kept_entries.append(entry)
    if kept_entries:
        env["PYTHONPATH"] = os.pathsep.join(kept_entries)
    else:
        env.pop("PYTHONPATH", None)


def _runtime_pythonpath_roots() -> set[Path]:
    return {Path(__file__).resolve().parents[3]}


def _without_runtime_python_bin(path_entries: list[str]) -> list[str]:
    if sys.prefix == sys.base_prefix:
        return path_entries
    runtime_python_bins = {Path(sys.executable).expanduser().parent, Path(sys.executable).resolve().parent}
    return [entry for entry in path_entries if _resolve_path_entry(entry) not in runtime_python_bins]


def _ensure_python_command_shim(bin_dir: str, path_entries: list[str]) -> None:
    if os.name == "nt":
        return
    search_path = os.pathsep.join(path_entries)
    if shutil.which("python", path=search_path):
        return
    python3 = shutil.which("python3", path=search_path)
    if not python3:
        return
    shim = Path(bin_dir) / "python"
    if shim.exists():
        return
    try:
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(python3)} \"$@\"\n", encoding="utf-8")
        shim.chmod(0o755)
    except OSError:
        return


def _resolve_pythonpath_entry(entry: str) -> Path:
    return _resolve_path_entry(entry)


def _resolve_path_entry(entry: str) -> Path:
    path = Path(entry).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _resolve_spawn_context(command: str, cwd: str, spawn_hook: BashSpawnHook | None = None) -> BashSpawnContext:
    context = BashSpawnContext(command=command, cwd=cwd, env=get_shell_env())
    return spawn_hook(context) if spawn_hook else context


def _format_output(output: OutputSpool, snapshot: OutputSnapshot, empty_text: str = "(no output)") -> tuple[str, dict | None]:
    truncation = snapshot.truncation
    text = snapshot.content if snapshot.content else empty_text
    details = None
    if truncation.truncated:
        details = {
            "truncation": truncation_to_details(truncation),
            "fullOutputPath": snapshot.full_output_path,
            "artifactId": snapshot.artifact_id,
        }
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
    artifacts: ArtifactRegistry | None,
    shell_path: str | None,
    process_service: ProcessSessionService | None,
    process_owner: ProcessOwner | None,
    transport_factory: ProcessTransportFactory | None,
    launch_session_id: str | None,
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
    if process_service is not None and process_owner is not None and transport_factory is not None:
        return _execute_managed_bash(
            process_service,
            process_owner,
            transport_factory,
            spawn_context,
            shell_path,
            artifacts,
            launch_session_id,
            args,
            signal,
            on_update,
        )
    output = OutputSpool(
        temp_file_prefix="pi-bash",
        artifact_registry=artifacts,
        artifact_kind="bash-output",
    )
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
                    "artifactId": snapshot.artifact_id,
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
        output.close()
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


def _execute_managed_bash(
    service: ProcessSessionService,
    owner: ProcessOwner,
    transport_factory: ProcessTransportFactory,
    spawn_context: BashSpawnContext,
    shell_path: str | None,
    artifacts: ArtifactRegistry | None,
    launch_session_id: str | None,
    args,
    signal,
    on_update,
) -> AgentToolResult:
    yield_time_ms = args.get("yield_time_ms", 10_000)
    timeout = args.get("timeout")
    tty = args.get("tty", False)
    rows = args.get("rows", 24)
    cols = args.get("cols", 80)
    if not isinstance(yield_time_ms, int) or isinstance(yield_time_ms, bool) or not 0 <= yield_time_ms <= 30_000:
        raise ValueError("yield_time_ms must be an integer between 0 and 30000")
    if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0):
        raise ValueError("timeout must be a positive number")
    if not isinstance(tty, bool):
        raise ValueError("tty must be a boolean")
    if not tty and ("rows" in args or "cols" in args):
        raise ValueError("rows and cols require tty=true")
    if tty:
        for field, value, lower, upper in (
            ("rows", rows, 2, 200),
            ("cols", cols, 20, 500),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
                raise ValueError(f"{field} must be an integer between {lower} and {upper}")
    if on_update:
        on_update(AgentToolResult(content=[], details=None))
    last_update_at = 0.0

    def handle_update(update: ProcessSnapshot) -> None:
        nonlocal last_update_at
        if on_update is None:
            return
        now = time.monotonic()
        if now - last_update_at < BASH_UPDATE_THROTTLE_SECONDS:
            return
        last_update_at = now
        on_update(
            AgentToolResult(
                content=[TextContent(text=update.output)],
                details=update.as_details(),
            )
        )

    snapshot = service.start(
        owner,
        ProcessLaunchRequest(
            command=spawn_context.command,
            cwd=spawn_context.cwd,
            env=spawn_context.env,
            shell_path=shell_path or os.environ.get("SHELL") or "/bin/bash",
            tty=tty,
            rows=rows,
            cols=cols,
            timeout_seconds=timeout,
            launch_session_id=launch_session_id,
        ),
        transport_factory,
        yield_time_ms=yield_time_ms,
        signal=signal,
        on_update=handle_update if on_update is not None else None,
    )
    return _managed_bash_result(service, owner, snapshot, signal, artifacts, timeout)


def _managed_bash_result(
    service: ProcessSessionService,
    owner: ProcessOwner,
    snapshot: ProcessSnapshot,
    signal,
    artifacts: ArtifactRegistry | None,
    timeout: float | None,
) -> AgentToolResult:
    details = snapshot.as_details()
    tail = service.tail_snapshot(owner, snapshot.session_id) if snapshot.state.terminal else None
    output = tail.content if tail is not None else snapshot.output
    output = output or "(no output)"
    if tail is not None and tail.truncated:
        borrowed = snapshot.durable_output and snapshot.full_output_path is not None
        exported = (
            Path(snapshot.full_output_path)
            if borrowed
            else service.export_output(owner, snapshot.session_id, tempfile.gettempdir())
        )
        artifact = (
            artifacts.register(
                exported,
                kind="bash-output",
                access="read",
                remove_on_close=not borrowed,
            )
            if artifacts is not None
            else None
        )
        details.update(
            {
                "truncation": truncation_to_details(tail),
                "fullOutputPath": str(exported),
                "artifactId": artifact.id if artifact is not None else None,
            }
        )
        start_line = tail.total_lines - tail.output_lines + 1
        output = _append_status(
            output,
            f"[Showing lines {start_line}-{tail.total_lines} of {tail.total_lines}. Full output: {exported}]",
        )
    if snapshot.state is ProcessState.EXITED:
        if snapshot.exit_code not in (None, 0):
            raise RuntimeError(_append_status(output, f"Command exited with code {snapshot.exit_code}"))
        return AgentToolResult(content=[TextContent(text=output)], details=details)
    if snapshot.state is ProcessState.TIMED_OUT:
        suffix = f" after {timeout:g} seconds" if timeout is not None else ""
        raise RuntimeError(_append_status(output, f"Command timed out{suffix}"))
    if snapshot.state is ProcessState.TERMINATED:
        status = "Command aborted" if _is_aborted(signal) else "Command terminated"
        raise RuntimeError(_append_status(output, status))
    if snapshot.state is ProcessState.FAILED:
        if snapshot.failure_code == "output_limit":
            raise RuntimeError(
                _append_status(output, "Command stopped after reaching the sanitized-output budget (not a timeout)")
            )
        raise RuntimeError(_append_status(output, "Command failed to execute"))
    footer = (
        f"Process {snapshot.session_id} is {snapshot.state.value}; command continues in the background. "
        f"{format_process_wait_instruction(snapshot.session_id, snapshot.next_cursor)} "
        f"Use the poll action for interactive or quick status checks. "
        f"Suggested poll delay: {snapshot.suggested_poll_delay_ms} ms."
    )
    return AgentToolResult(
        content=[TextContent(text=_append_status(snapshot.output, footer))],
        details=details,
    )


def create_bash_tool_definition(
    cwd: str,
    operations: BashOperations | None = None,
    command_prefix: str | None = None,
    shell_path: str | None = None,
    spawn_hook: BashSpawnHook | None = None,
    artifacts: ArtifactRegistry | None = None,
    backend: ExecutionBackend | None = None,
    process_service: ProcessSessionService | None = None,
    process_owner: ProcessOwner | None = None,
    transport_factory: ProcessTransportFactory | None = None,
    launch_session_id: str | None = None,
) -> ToolDefinition:
    use_managed_process = operations is None and process_service is not None
    ops = operations or create_local_bash_operations(shell_path=shell_path, backend=backend)
    return ToolDefinition(
        name="bash",
        label="bash",
        description=(
            f"Execute a bash command in the current working directory. Returns stdout and stderr. Output is "
            f"truncated to last {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
            "If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds. "
            "Managed sessions return a process handle after yield_time_ms (default 10000); this yield does not kill the command."
        ),
        parameters=BASH_SCHEMA,
        prompt_snippet="Execute bash commands (ls, grep, find, etc.)",
        prompt_guidelines=[],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_bash(
            cwd,
            ops,
            command_prefix,
            spawn_hook,
            artifacts,
            shell_path,
            process_service if use_managed_process else None,
            process_owner if use_managed_process else None,
            transport_factory if use_managed_process else None,
            launch_session_id if use_managed_process else None,
            tid,
            args,
            signal,
            on_update,
            ctx,
        ),
        render_call=lambda args, ctx=None: f"bash {args.get('command', '')}",
    )


def create_bash_tool(
    cwd: str,
    operations: BashOperations | None = None,
    command_prefix: str | None = None,
    shell_path: str | None = None,
    spawn_hook: BashSpawnHook | None = None,
    artifacts: ArtifactRegistry | None = None,
    backend: ExecutionBackend | None = None,
    process_service: ProcessSessionService | None = None,
    process_owner: ProcessOwner | None = None,
    transport_factory: ProcessTransportFactory | None = None,
    launch_session_id: str | None = None,
) -> AgentTool:
    return wrap_tool_definition(
        create_bash_tool_definition(
            cwd,
            operations=operations,
            command_prefix=command_prefix,
            shell_path=shell_path,
            spawn_hook=spawn_hook,
            artifacts=artifacts,
            backend=backend,
            process_service=process_service,
            process_owner=process_owner,
            transport_factory=transport_factory,
            launch_session_id=launch_session_id,
        ),
        lambda: ToolContext(cwd=cwd),
    )
