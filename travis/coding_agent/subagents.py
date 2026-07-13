"""Subagent orchestration primitives for travis.

The supervisor keeps noisy child-agent execution out of the parent context and
returns structured summaries. Backends are intentionally small adapters: travis
can run internal sessions, Codex can run through ``codex exec --json``, and
future coding agents can implement the same ``run(task)`` contract.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal as signal_module
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence

SubagentStatus = Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]
SubagentSandbox = Literal["read_only", "workspace_write", "full_access"]

_READ_ONLY_TOOLS = ("read", "grep", "find", "ls")
_SUBAGENT_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "timeout"}
_SANDBOX_FLAGS: dict[str, str] = {
    "read_only": "read-only",
    "workspace_write": "workspace-write",
    "full_access": "danger-full-access",
}
_REASONING_EFFORTS = {"off", "low", "medium", "high"}
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SUBPROCESS_RUN = subprocess.run


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return f"subagent-{uuid.uuid4().hex[:12]}"


def _validate_wait_timeout(timeout: float | None) -> None:
    if timeout is None:
        return
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0 or not math.isfinite(timeout):
        raise ValueError("timeout must be non-negative and finite")


def _validate_task_id_reference(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id.strip() or not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError(f"Unsupported subagent task id: {task_id}")


@dataclass(frozen=True)
class SubagentTask:
    role: str
    goal: str
    cwd: str
    backend: str = "internal"
    id: str = field(default_factory=_new_id)
    sandbox: SubagentSandbox = "read_only"
    model: str | None = None
    reasoning: str | None = None
    allowed_tools: tuple[str, ...] = _READ_ONLY_TOOLS
    context_pack: str = ""
    timeout_seconds: int = 1800
    return_contract: str = (
        "Return a concise summary, key findings, evidence, changed files, and blockers. "
        "Every factual claim in your summary must be backed by observed evidence. "
        "If evidence is missing or ambiguous, mark the claim as uncertain."
    )
    parent_session_id: str | None = None
    parent_turn_id: str | None = None
    depth: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("Subagent role is required")
        if not _TASK_ID_PATTERN.fullmatch(self.role):
            raise ValueError(f"Unsupported subagent role: {self.role}")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("Subagent goal is required")
        if not isinstance(self.cwd, str) or not self.cwd.strip():
            raise ValueError("Subagent cwd is required")
        if not Path(self.cwd).is_dir():
            raise ValueError(f"Subagent cwd must be an existing directory: {self.cwd}")
        if not isinstance(self.backend, str) or not self.backend.strip() or not _TASK_ID_PATTERN.fullmatch(self.backend):
            raise ValueError(f"Unsupported subagent backend: {self.backend}")
        if not isinstance(self.id, str) or not self.id.strip() or not _TASK_ID_PATTERN.fullmatch(self.id):
            raise ValueError(f"Unsupported subagent task id: {self.id}")
        if not isinstance(self.sandbox, str) or self.sandbox not in _SANDBOX_FLAGS:
            raise ValueError(f"Unsupported subagent sandbox: {self.sandbox}")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise ValueError("Subagent timeout_seconds must be positive")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 1:
            raise ValueError("Subagent depth must be at least 1")
        if self.model is not None:
            if not isinstance(self.model, str) or not self.model.strip():
                raise ValueError("Subagent model must be a non-empty string when set")
        if self.reasoning is not None:
            if not isinstance(self.reasoning, str):
                raise ValueError(f"Unsupported subagent reasoning effort: {self.reasoning}")
            reasoning = self.reasoning.strip().lower()
            if reasoning not in _REASONING_EFFORTS:
                raise ValueError(f"Unsupported subagent reasoning effort: {self.reasoning}")
            object.__setattr__(self, "reasoning", reasoning)
        if isinstance(self.allowed_tools, str):
            raise ValueError("Subagent allowed_tools must be a sequence of strings")
        try:
            allowed_tools = tuple(self.allowed_tools or ())
        except TypeError as error:
            raise ValueError("Subagent allowed_tools must be a sequence of strings") from error
        for tool in allowed_tools:
            if not isinstance(tool, str) or not tool.strip() or not _TASK_ID_PATTERN.fullmatch(tool):
                raise ValueError(f"Unsupported subagent allowed tool: {tool}")
        object.__setattr__(self, "allowed_tools", allowed_tools)
        if not isinstance(self.context_pack, str):
            raise ValueError("Subagent context_pack must be a string")
        if not isinstance(self.return_contract, str) or not self.return_contract.strip():
            raise ValueError("Subagent return_contract is required")
        if self.parent_session_id is not None and not isinstance(self.parent_session_id, str):
            raise ValueError("Subagent parent_session_id must be a string when set")
        if self.parent_turn_id is not None and not isinstance(self.parent_turn_id, str):
            raise ValueError("Subagent parent_turn_id must be a string when set")

    def prompt(self) -> str:
        parts = [
            "Subagent system contract:\n"
            f"- Current working directory: {self.cwd}\n"
            "- Use paths relative to the current working directory unless the Goal gives an absolute path.\n"
            "- Do not drop leading project directories from paths in the Goal; preserve prefixes such as travis/.\n"
            "- Allowed tools are the complete tool catalog for this child. Do not use any tool names outside Allowed tools.\n"
            "- For file discovery, use find or ls.\n"
            "- After two failed attempts for the same path or unavailable tool, stop retrying, summarize the blocker, "
            "and return the best evidence gathered so far.\n"
            "- Every factual claim in your summary must be backed by observed evidence from the available tools or context pack.\n"
            "- If evidence is missing or ambiguous, mark the claim as uncertain and state what evidence is missing.\n"
            "- Do not infer behavior from filenames, conventions, or expectations alone.\n"
            "- Include an Evidence: section with path/command references for the claims that matter.",
            f"Role: {self.role}",
            "Delegation boundary: You are already the delegated child subagent. Execute the Goal directly with "
            "the available tools. Do not evaluate whether the parent has subagent tools. Do not answer "
            "`subagent tool unavailable` unless the Goal explicitly asks you to test nested-subagent tooling.",
            f"Goal: {self.goal}",
            f"Sandbox: {self.sandbox}",
            f"Allowed tools: {', '.join(self.allowed_tools) if self.allowed_tools else 'none'}",
            f"Return contract: {self.return_contract}",
        ]
        if self.context_pack.strip():
            parts.append(f"Context pack:\n{self.context_pack.strip()}")
        return "\n\n".join(parts)


@dataclass(frozen=True)
class SubagentResult:
    task_id: str
    backend: str
    role: str
    status: SubagentStatus
    summary: str
    final_response: str = ""
    files_changed: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    usage: dict[str, object] = field(default_factory=dict)
    child_session_id: str | None = None
    raw_log_path: str | None = None
    started_at_ms: int = 0
    ended_at_ms: int = 0
    tool_trace: list[dict[str, object]] = field(default_factory=list)
    guardrail: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip() or not _TASK_ID_PATTERN.fullmatch(self.task_id):
            raise ValueError(f"Unsupported subagent task id: {self.task_id}")
        if not isinstance(self.backend, str) or not self.backend.strip() or not _TASK_ID_PATTERN.fullmatch(self.backend):
            raise ValueError(f"Unsupported subagent backend: {self.backend}")
        if not isinstance(self.role, str) or not self.role.strip() or not _TASK_ID_PATTERN.fullmatch(self.role):
            raise ValueError(f"Unsupported subagent role: {self.role}")
        if not isinstance(self.status, str) or self.status not in _SUBAGENT_STATUSES:
            raise ValueError(f"Unsupported subagent status: {self.status}")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("Subagent summary is required")
        if not isinstance(self.final_response, str):
            raise ValueError("Subagent final_response must be a string")
        if self.child_session_id is not None and not isinstance(self.child_session_id, str):
            raise ValueError("Subagent child_session_id must be a string when set")
        if self.raw_log_path is not None and not isinstance(self.raw_log_path, str):
            raise ValueError("Subagent raw_log_path must be a string when set")
        for field_name in ("started_at_ms", "ended_at_ms"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Subagent timestamps must be non-negative integers")
        if self.started_at_ms and self.ended_at_ms and self.ended_at_ms < self.started_at_ms:
            raise ValueError("Subagent ended_at_ms cannot be before started_at_ms")
        for field_name in ("files_changed", "artifacts", "errors"):
            value = getattr(self, field_name)
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"Subagent {field_name} must be a list of strings")
        if not isinstance(self.usage, dict):
            raise ValueError("Subagent usage must be a dict")
        if not isinstance(self.tool_trace, list) or any(not isinstance(item, dict) for item in self.tool_trace):
            raise ValueError("Subagent tool_trace must be a list of dicts")
        if self.guardrail is not None and not isinstance(self.guardrail, dict):
            raise ValueError("Subagent guardrail must be a dict when set")

    @property
    def duration_ms(self) -> int:
        if not self.started_at_ms or not self.ended_at_ms:
            return 0
        return max(0, self.ended_at_ms - self.started_at_ms)

    def as_dict(self) -> dict[str, object]:
        return {
            "taskId": self.task_id,
            "backend": self.backend,
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "finalResponse": self.final_response,
            "filesChanged": list(self.files_changed),
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
            "usage": dict(self.usage),
            "childSessionId": self.child_session_id,
            "rawLogPath": self.raw_log_path,
            "startedAtMs": self.started_at_ms,
            "endedAtMs": self.ended_at_ms,
            "durationMs": self.duration_ms,
            "toolTrace": [dict(item) for item in self.tool_trace],
            "guardrail": dict(self.guardrail) if self.guardrail is not None else None,
        }


class SubagentBackend(Protocol):
    name: str

    def run(self, task: SubagentTask) -> SubagentResult:
        ...


class CallableSubagentBackend:
    def __init__(self, name: str, handler: Callable[[SubagentTask], str | SubagentResult]) -> None:
        if not isinstance(name, str) or not name.strip() or not _TASK_ID_PATTERN.fullmatch(name):
            raise ValueError(f"Unsupported subagent backend: {name}")
        if not callable(handler):
            raise ValueError("Subagent backend handler must be callable")
        self.name = name
        self._handler = handler

    def run(self, task: SubagentTask) -> SubagentResult:
        started = _now_ms()
        output = self._handler(task)
        if isinstance(output, SubagentResult):
            return output
        if not isinstance(output, str) or not output.strip():
            raise ValueError("Subagent backend handler must return a non-empty string or SubagentResult")
        ended = _now_ms()
        return SubagentResult(
            task_id=task.id,
            backend=self.name,
            role=task.role,
            status="completed",
            summary=str(output),
            final_response=str(output),
            started_at_ms=started,
            ended_at_ms=ended,
        )


class CodexExecBackend:
    name = "codex"

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        runner: Callable[..., object] | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        if not isinstance(codex_bin, str) or not codex_bin.strip():
            raise ValueError("codex_bin must be a non-empty string")
        if runner is not None and not callable(runner):
            raise ValueError("runner must be callable")
        if log_dir is not None and not isinstance(log_dir, (str, Path)):
            raise ValueError("log_dir must be a path string or Path")
        self.codex_bin = codex_bin
        self._runner = runner if runner is not None else subprocess.run
        self._uses_default_runner = runner is None and self._runner is _SUBPROCESS_RUN
        self._log_dir = Path(log_dir) if log_dir is not None else None
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled_tasks: set[str] = set()
        self._process_lock = threading.RLock()

    def cancel(self, task_id: str) -> None:
        _validate_task_id_reference(task_id)
        with self._process_lock:
            self._cancelled_tasks.add(task_id)
            process = self._processes.get(task_id)
        if process is None or process.poll() is not None:
            return
        self._terminate_process(process)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self._kill_process(process)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal_module.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return

    @staticmethod
    def _kill_process(process: subprocess.Popen[str]) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal_module.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return

    def _run_command(self, task: SubagentTask, args: list[str]) -> subprocess.CompletedProcess[str]:
        if not self._uses_default_runner:
            return self._runner(
                args,
                cwd=task.cwd,
                timeout=task.timeout_seconds,
                text=True,
                capture_output=True,
            )
        process = subprocess.Popen(
            args,
            cwd=task.cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )
        with self._process_lock:
            self._processes[task.id] = process
            cancelled = task.id in self._cancelled_tasks
        if cancelled:
            self._terminate_process(process)
        try:
            stdout, stderr = process.communicate(timeout=task.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            self._kill_process(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                error.cmd,
                error.timeout,
                output=stdout,
                stderr=stderr,
            ) from error
        finally:
            with self._process_lock:
                if self._processes.get(task.id) is process:
                    del self._processes[task.id]
                self._cancelled_tasks.discard(task.id)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)

    def _write_raw_log(
        self,
        task: SubagentTask,
        *,
        stdout: str,
        stderr: str,
        returncode: int | None,
        started_at_ms: int,
        ended_at_ms: int,
    ) -> str | None:
        if self._log_dir is None:
            return None
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / f"{task.id}.json"
        path.write_text(
            json.dumps(
                {
                    "taskId": task.id,
                    "backend": self.name,
                    "role": task.role,
                    "goal": task.goal,
                    "cwd": task.cwd,
                    "sandbox": task.sandbox,
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "startedAtMs": started_at_ms,
                    "endedAtMs": ended_at_ms,
                    "durationMs": max(0, ended_at_ms - started_at_ms),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(path)

    def _safe_write_raw_log(
        self,
        task: SubagentTask,
        *,
        stdout: str,
        stderr: str,
        returncode: int | None,
        started_at_ms: int,
        ended_at_ms: int,
    ) -> tuple[str | None, list[str]]:
        try:
            return (
                self._write_raw_log(
                    task,
                    stdout=stdout,
                    stderr=stderr,
                    returncode=returncode,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                ),
                [],
            )
        except (OSError, TypeError, ValueError) as error:
            return None, [f"Failed to write raw subagent log: {error}"]

    def run(self, task: SubagentTask) -> SubagentResult:
        started = _now_ms()
        if task.allowed_tools != _READ_ONLY_TOOLS:
            ended = _now_ms()
            error_text = "Codex backend does not enforce custom allowed tools."
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="failed",
                summary=error_text,
                errors=[error_text],
                started_at_ms=started,
                ended_at_ms=ended,
            )
        args = [
            self.codex_bin,
            "exec",
            "--json",
            "--sandbox",
            _SANDBOX_FLAGS[task.sandbox],
            "--ephemeral",
            task.prompt(),
        ]
        if task.model:
            args[2:2] = ["--model", task.model]
        if task.reasoning and task.reasoning != "off":
            args[2:2] = ["-c", f'model_reasoning_effort="{task.reasoning}"']
        try:
            completed = self._run_command(task, args)
        except TimeoutError:
            ended = _now_ms()
            raw_log_path, log_errors = self._safe_write_raw_log(
                task,
                stdout="",
                stderr=f"Timed out after {task.timeout_seconds}s",
                returncode=None,
                started_at_ms=started,
                ended_at_ms=ended,
            )
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="timeout",
                summary="Codex subagent timed out.",
                errors=[f"Timed out after {task.timeout_seconds}s", *log_errors],
                raw_log_path=raw_log_path,
                started_at_ms=started,
                ended_at_ms=ended,
            )
        except subprocess.TimeoutExpired as error:
            ended = _now_ms()
            stdout = str(error.output or "")
            stderr = str(error.stderr or str(error))
            raw_log_path, log_errors = self._safe_write_raw_log(
                task,
                stdout=stdout,
                stderr=stderr,
                returncode=None,
                started_at_ms=started,
                ended_at_ms=ended,
            )
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="timeout",
                summary="Codex subagent timed out.",
                errors=[str(error), *log_errors],
                raw_log_path=raw_log_path,
                started_at_ms=started,
                ended_at_ms=ended,
            )
        except FileNotFoundError as error:
            ended = _now_ms()
            raw_log_path, log_errors = self._safe_write_raw_log(
                task,
                stdout="",
                stderr=str(error),
                returncode=None,
                started_at_ms=started,
                ended_at_ms=ended,
            )
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="failed",
                summary="Codex executable was not found.",
                errors=[str(error), *log_errors],
                raw_log_path=raw_log_path,
                started_at_ms=started,
                ended_at_ms=ended,
            )

        stdout = str(getattr(completed, "stdout", "") or "")
        stderr = str(getattr(completed, "stderr", "") or "")
        returncode = int(getattr(completed, "returncode", 1))
        final_text, usage = parse_codex_jsonl(stdout)
        ended = _now_ms()
        raw_log_path, log_errors = self._safe_write_raw_log(
            task,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            started_at_ms=started,
            ended_at_ms=ended,
        )
        if returncode != 0:
            error_text = stderr.strip() or final_text.strip() or f"codex exited with code {returncode}"
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="failed",
                summary=error_text,
                final_response=final_text,
                errors=[error_text, *log_errors],
                usage=usage,
                raw_log_path=raw_log_path,
                started_at_ms=started,
                ended_at_ms=ended,
            )
        summary = final_text.strip() or stdout.strip() or "Codex subagent completed without a final message."
        return SubagentResult(
            task_id=task.id,
            backend=self.name,
            role=task.role,
            status="completed",
            summary=summary,
            final_response=final_text,
            errors=log_errors,
            usage=usage,
            raw_log_path=raw_log_path,
            started_at_ms=started,
            ended_at_ms=ended,
        )


def parse_codex_jsonl(text: str) -> tuple[str, dict[str, object]]:
    final_messages: list[str] = []
    usage: dict[str, object] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = dict(event["usage"])
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message":
                text_value = item.get("text") or item.get("content")
                if isinstance(text_value, str) and text_value.strip():
                    final_messages.append(text_value.strip())
    return "\n".join(final_messages), usage


class SubagentSupervisor:
    def __init__(
        self,
        *,
        max_threads: int = 3,
        max_depth: int = 1,
        event_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if isinstance(max_threads, bool) or not isinstance(max_threads, int) or max_threads < 1:
            raise ValueError("max_threads must be at least 1")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable")
        self.max_threads = max_threads
        self.max_depth = max_depth
        self._event_sink = event_sink
        self._observer_errors: list[str] = []
        self._executor = ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="travis-subagent")
        self._backends: dict[str, SubagentBackend] = {}
        self._tasks: dict[str, SubagentTask] = {}
        self._futures: dict[str, Future[SubagentResult]] = {}
        self._results: dict[str, SubagentResult] = {}
        self._statuses: dict[str, SubagentStatus] = {}
        self._started_at_ms: dict[str, int] = {}
        self._shutdown = False
        self._lock = threading.RLock()

    def register_backend(self, backend: SubagentBackend) -> None:
        with self._lock:
            backend_name = getattr(backend, "name", None)
            if not isinstance(backend_name, str) or not backend_name.strip() or not _TASK_ID_PATTERN.fullmatch(backend_name):
                raise ValueError(f"Unsupported subagent backend: {backend_name}")
            if not callable(getattr(backend, "run", None)):
                raise ValueError("Subagent backend must define a callable run method")
            self._backends[backend_name] = backend

    def _unfinished_future_count_locked(self) -> int:
        return sum(1 for future in self._futures.values() if not future.done())

    def spawn(self, task: SubagentTask) -> str:
        if not isinstance(task, SubagentTask):
            raise ValueError("Subagent task must be a SubagentTask")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Subagent supervisor has been shut down")
            if task.backend not in self._backends:
                raise ValueError(f"No subagent backend registered for '{task.backend}'")
            if task.depth > self.max_depth:
                raise ValueError(f"Subagent depth {task.depth} exceeds max_depth {self.max_depth}")
            if task.id in self._tasks:
                raise ValueError(f"Duplicate subagent task id: {task.id}")
            if self._unfinished_future_count_locked() >= self.max_threads:
                raise RuntimeError(f"Subagent thread limit reached ({self.max_threads})")
            self._tasks[task.id] = task
            self._statuses[task.id] = "queued"
            self._started_at_ms[task.id] = _now_ms()
        self._emit_start(task)
        with self._lock:
            if task.id in self._results:
                return task.id
            future = self._executor.submit(self._run_backend, task)
            self._futures[task.id] = future
        return task.id

    def wait(
        self,
        task_id: str,
        timeout: float | None = None,
        *,
        signal: object | None = None,
        cancel_reason: str = "Cancelled by parent abort.",
    ) -> SubagentResult:
        _validate_task_id_reference(task_id)
        _validate_wait_timeout(timeout)
        if not isinstance(cancel_reason, str):
            raise ValueError("cancel reason must be a string")
        with self._lock:
            if task_id in self._results:
                return self._results[task_id]
            future = self._futures.get(task_id)
            if future is None:
                raise KeyError(f"Unknown subagent task: {task_id}")
        started_wait = time.monotonic()
        try:
            while True:
                with self._lock:
                    if task_id in self._results:
                        return self._results[task_id]
                if future.done():
                    result = future.result()
                    break
                if signal is not None and getattr(signal, "aborted", False):
                    return self.cancel(task_id, reason=cancel_reason)
                wait_timeout = timeout
                if signal is not None:
                    if timeout is None:
                        wait_timeout = 0.05
                    else:
                        remaining = timeout - (time.monotonic() - started_wait)
                        if remaining <= 0:
                            raise TimeoutError()
                        wait_timeout = min(0.05, remaining)
                try:
                    result = future.result(timeout=wait_timeout)
                    break
                except TimeoutError:
                    if signal is not None:
                        continue
                    raise
        except TimeoutError:
            ended = _now_ms()
            timeout_text = f"Timed out after {timeout}s" if timeout is not None else "Timed out"
            with self._lock:
                if task_id in self._results:
                    return self._results[task_id]
                task = self._tasks[task_id]
                result = SubagentResult(
                    task_id=task.id,
                    backend=task.backend,
                    role=task.role,
                    status="timeout",
                    summary="Subagent timed out.",
                    errors=[timeout_text],
                    started_at_ms=self._started_at_ms.get(task_id, ended),
                    ended_at_ms=ended,
                )
                self._statuses[task_id] = "timeout"
                self._results[task_id] = result
            self._emit_stop(task, result)
            return result
        with self._lock:
            self._results[task_id] = result
        return result

    def cancel(self, task_id: str, reason: str = "Cancelled by user.") -> SubagentResult:
        _validate_task_id_reference(task_id)
        if not isinstance(reason, str):
            raise ValueError("cancel reason must be a string")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Unknown subagent task: {task_id}")
            existing = self._results.get(task_id)
            if existing is not None:
                return existing
            future = self._futures.get(task_id)
            if future is not None:
                future.cancel()
            backend_cancel = getattr(self._backends.get(task.backend), "cancel", None)
            ended = _now_ms()
            result = SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status="cancelled",
                summary="Subagent cancelled.",
                errors=[reason] if reason else [],
                started_at_ms=self._started_at_ms.get(task_id, ended),
                ended_at_ms=ended,
            )
            self._statuses[task_id] = "cancelled"
            self._results[task_id] = result
        if callable(backend_cancel):
            try:
                backend_cancel(task_id)
            except Exception as error:  # noqa: BLE001 - cancellation should not crash the parent.
                self._observer_errors.append(f"backend cancel failed for {task_id}: {error}")
        self._emit_stop(task, result)
        return result

    def shutdown(self, *, wait: bool = True, reason: str = "Supervisor shutdown.") -> list[SubagentResult]:
        if not isinstance(wait, bool):
            raise ValueError("shutdown wait must be a bool")
        if not isinstance(reason, str):
            raise ValueError("shutdown reason must be a string")
        with self._lock:
            if self._shutdown:
                return []
            task_statuses = list(self._statuses.items())
            self._shutdown = True
        results: list[SubagentResult] = []
        for task_id, status in task_statuses:
            if status in {"queued", "running"} and task_id not in self._results:
                results.append(self.cancel(task_id, reason=reason))
        self._executor.shutdown(wait=wait, cancel_futures=True)
        return results

    def wait_all(self, task_ids: Sequence[str] | None = None, timeout: float | None = None) -> list[SubagentResult]:
        _validate_wait_timeout(timeout)
        if task_ids is None:
            ids = list(self._tasks.keys())
        else:
            if isinstance(task_ids, (str, bytes)) or not isinstance(task_ids, Sequence):
                raise ValueError("task_ids must be a sequence of subagent task ids")
            ids = list(task_ids)
            for task_id in ids:
                _validate_task_id_reference(task_id)
        return [self.wait(task_id, timeout=timeout) for task_id in ids]

    def list_tasks(self) -> list[dict[str, object]]:
        with self._lock:
            items = list(self._tasks.items())
        return [
            {
                "taskId": task_id,
                "role": task.role,
                "goal": task.goal,
                "backend": task.backend,
                "status": self._status_for(task_id),
            }
            for task_id, task in items
        ]

    def list_results(self) -> list[SubagentResult]:
        with self._lock:
            futures = list(self._futures.items())
        for task_id, future in futures:
            if future.done() and task_id not in self._results:
                with self._lock:
                    if task_id not in self._results:
                        self._results[task_id] = future.result()
        with self._lock:
            return list(self._results.values())

    def get_result(self, task_id: str) -> SubagentResult | None:
        _validate_task_id_reference(task_id)
        with self._lock:
            if task_id in self._results:
                return self._results[task_id]
            future = self._futures.get(task_id)
        if future and future.done():
            with self._lock:
                if task_id not in self._results:
                    self._results[task_id] = future.result()
                return self._results[task_id]
        return None

    def _run_backend(self, task: SubagentTask) -> SubagentResult:
        with self._lock:
            existing = self._results.get(task.id)
            if existing is not None and self._statuses.get(task.id) in {"cancelled", "timeout"}:
                return existing
            self._statuses[task.id] = "running"
            backend = self._backends[task.backend]
            started = self._started_at_ms.get(task.id, _now_ms())
        try:
            result = backend.run(task)
            if result.task_id != task.id:
                raise ValueError(f"Subagent backend returned mismatched task_id: {result.task_id}")
            if result.backend != task.backend:
                raise ValueError(f"Subagent backend returned mismatched backend: {result.backend}")
            if result.role != task.role:
                raise ValueError(f"Subagent backend returned mismatched role: {result.role}")
        except Exception as error:  # noqa: BLE001 - child failures must be data, not parent crashes.
            ended = _now_ms()
            error_text = f"Subagent backend failed: {error}"
            result = SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status="failed",
                summary=error_text,
                errors=[error_text],
                started_at_ms=started,
                ended_at_ms=ended,
            )
        with self._lock:
            if task.id in self._results and self._statuses.get(task.id) in {"cancelled", "timeout"}:
                return self._results[task.id]
            self._statuses[task.id] = result.status
            self._results[task.id] = result
        self._emit_stop(task, result)
        return result

    def _status_for(self, task_id: str) -> SubagentStatus:
        result = self.get_result(task_id)
        if result is not None:
            return result.status
        return self._statuses.get(task_id, "queued")

    def _emit_start(self, task: SubagentTask) -> None:
        self._emit(
            {
                "type": "subagent_start",
                "parent_session_id": task.parent_session_id,
                "parent_turn_id": task.parent_turn_id,
                "child_session_id": None,
                "child_subagent_id": task.id,
                "child_role": task.role,
                "child_goal": task.goal,
                "backend": task.backend,
            }
        )

    def _emit_stop(self, task: SubagentTask, result: SubagentResult) -> None:
        self._emit(
            {
                "type": "subagent_stop",
                "parent_session_id": task.parent_session_id,
                "parent_turn_id": task.parent_turn_id,
                "child_session_id": result.child_session_id,
                "child_subagent_id": task.id,
                "child_role": task.role,
                "status": result.status,
                "child_summary": result.summary,
                "duration_ms": result.duration_ms,
                "started_at_ms": result.started_at_ms,
                "ended_at_ms": result.ended_at_ms,
                "raw_log_path": result.raw_log_path,
                "files_changed": list(result.files_changed),
                "artifacts": list(result.artifacts),
                "errors": list(result.errors),
                "usage": dict(result.usage),
                "backend": task.backend,
            }
        )

    def _emit(self, event: dict[str, object]) -> None:
        if self._event_sink is not None:
            try:
                self._event_sink(event)
            except Exception as error:
                self._observer_errors.append(f"event sink failed for {event.get('type', 'unknown')}: {error}")

    def observer_errors(self) -> list[str]:
        return list(self._observer_errors)
