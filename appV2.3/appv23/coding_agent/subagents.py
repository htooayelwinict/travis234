"""Subagent orchestration primitives for appv23.

The supervisor keeps noisy child-agent execution out of the parent context and
returns structured summaries. Backends are intentionally small adapters: appv23
can run internal sessions, Codex can run through ``codex exec --json``, and
future coding agents can implement the same ``run(task)`` contract.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence

SubagentStatus = Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]
SubagentSandbox = Literal["read_only", "workspace_write", "full_access"]

_READ_ONLY_TOOLS = ("read", "grep", "find", "ls")
_SANDBOX_FLAGS: dict[str, str] = {
    "read_only": "read-only",
    "workspace_write": "workspace-write",
    "full_access": "danger-full-access",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return f"subagent-{uuid.uuid4().hex[:12]}"


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
    return_contract: str = "Return a concise summary, key findings, changed files, and blockers."
    parent_session_id: str | None = None
    parent_turn_id: str | None = None
    depth: int = 1

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("Subagent role is required")
        if not self.goal.strip():
            raise ValueError("Subagent goal is required")
        if not self.cwd.strip():
            raise ValueError("Subagent cwd is required")
        if self.sandbox not in _SANDBOX_FLAGS:
            raise ValueError(f"Unsupported subagent sandbox: {self.sandbox}")
        if self.timeout_seconds <= 0:
            raise ValueError("Subagent timeout_seconds must be positive")
        if self.depth < 1:
            raise ValueError("Subagent depth must be at least 1")
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools or ()))

    def prompt(self) -> str:
        parts = [
            f"Role: {self.role}",
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
        }


class SubagentBackend(Protocol):
    name: str

    def run(self, task: SubagentTask) -> SubagentResult:
        ...


class CallableSubagentBackend:
    def __init__(self, name: str, handler: Callable[[SubagentTask], str | SubagentResult]) -> None:
        if not name.strip():
            raise ValueError("Backend name is required")
        self.name = name
        self._handler = handler

    def run(self, task: SubagentTask) -> SubagentResult:
        started = _now_ms()
        output = self._handler(task)
        if isinstance(output, SubagentResult):
            return output
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
        self.codex_bin = codex_bin
        self._runner = runner or subprocess.run
        self._log_dir = Path(log_dir) if log_dir is not None else None

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

    def run(self, task: SubagentTask) -> SubagentResult:
        started = _now_ms()
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
            completed = self._runner(
                args,
                cwd=task.cwd,
                timeout=task.timeout_seconds,
                text=True,
                capture_output=True,
            )
        except TimeoutError:
            ended = _now_ms()
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="timeout",
                summary="Codex subagent timed out.",
                errors=[f"Timed out after {task.timeout_seconds}s"],
                raw_log_path=self._write_raw_log(
                    task,
                    stdout="",
                    stderr=f"Timed out after {task.timeout_seconds}s",
                    returncode=None,
                    started_at_ms=started,
                    ended_at_ms=ended,
                ),
                started_at_ms=started,
                ended_at_ms=ended,
            )
        except subprocess.TimeoutExpired as error:
            ended = _now_ms()
            stdout = str(error.output or "")
            stderr = str(error.stderr or str(error))
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="timeout",
                summary="Codex subagent timed out.",
                errors=[str(error)],
                raw_log_path=self._write_raw_log(
                    task,
                    stdout=stdout,
                    stderr=stderr,
                    returncode=None,
                    started_at_ms=started,
                    ended_at_ms=ended,
                ),
                started_at_ms=started,
                ended_at_ms=ended,
            )
        except FileNotFoundError as error:
            ended = _now_ms()
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="failed",
                summary="Codex executable was not found.",
                errors=[str(error)],
                raw_log_path=self._write_raw_log(
                    task,
                    stdout="",
                    stderr=str(error),
                    returncode=None,
                    started_at_ms=started,
                    ended_at_ms=ended,
                ),
                started_at_ms=started,
                ended_at_ms=ended,
            )

        stdout = str(getattr(completed, "stdout", "") or "")
        stderr = str(getattr(completed, "stderr", "") or "")
        returncode = int(getattr(completed, "returncode", 1))
        final_text, usage = parse_codex_jsonl(stdout)
        ended = _now_ms()
        raw_log_path = self._write_raw_log(
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
                errors=[error_text],
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
        if max_threads < 1:
            raise ValueError("max_threads must be at least 1")
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        self.max_threads = max_threads
        self.max_depth = max_depth
        self._event_sink = event_sink
        self._executor = ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="appv23-subagent")
        self._backends: dict[str, SubagentBackend] = {}
        self._tasks: dict[str, SubagentTask] = {}
        self._futures: dict[str, Future[SubagentResult]] = {}
        self._results: dict[str, SubagentResult] = {}
        self._statuses: dict[str, SubagentStatus] = {}
        self._started_at_ms: dict[str, int] = {}
        self._shutdown = False

    def register_backend(self, backend: SubagentBackend) -> None:
        self._backends[backend.name] = backend

    def spawn(self, task: SubagentTask) -> str:
        if self._shutdown:
            raise RuntimeError("Subagent supervisor has been shut down")
        if task.backend not in self._backends:
            raise ValueError(f"No subagent backend registered for '{task.backend}'")
        if task.depth > self.max_depth:
            raise ValueError(f"Subagent depth {task.depth} exceeds max_depth {self.max_depth}")
        if task.id in self._tasks:
            raise ValueError(f"Duplicate subagent task id: {task.id}")
        running = sum(1 for status in self._statuses.values() if status in {"queued", "running"})
        if running >= self.max_threads:
            raise RuntimeError(f"Subagent thread limit reached ({self.max_threads})")
        self._tasks[task.id] = task
        self._statuses[task.id] = "queued"
        self._started_at_ms[task.id] = _now_ms()
        self._emit_start(task)
        future = self._executor.submit(self._run_backend, task)
        self._futures[task.id] = future
        return task.id

    def wait(self, task_id: str, timeout: float | None = None) -> SubagentResult:
        if task_id in self._results:
            return self._results[task_id]
        future = self._futures.get(task_id)
        if future is None:
            raise KeyError(f"Unknown subagent task: {task_id}")
        try:
            result = future.result(timeout=timeout)
        except TimeoutError:
            task = self._tasks[task_id]
            ended = _now_ms()
            timeout_text = f"Timed out after {timeout}s" if timeout is not None else "Timed out"
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
        self._results[task_id] = result
        return result

    def cancel(self, task_id: str, reason: str = "Cancelled by user.") -> SubagentResult:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Unknown subagent task: {task_id}")
        existing = self._results.get(task_id)
        if existing is not None:
            return existing
        future = self._futures.get(task_id)
        if future is not None:
            future.cancel()
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
        self._emit_stop(task, result)
        return result

    def shutdown(self, *, wait: bool = True, reason: str = "Supervisor shutdown.") -> list[SubagentResult]:
        if self._shutdown:
            return []
        results: list[SubagentResult] = []
        for task_id, status in list(self._statuses.items()):
            if status in {"queued", "running"} and task_id not in self._results:
                results.append(self.cancel(task_id, reason=reason))
        self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=True)
        return results

    def wait_all(self, task_ids: Sequence[str] | None = None, timeout: float | None = None) -> list[SubagentResult]:
        ids = list(task_ids or self._tasks.keys())
        return [self.wait(task_id, timeout=timeout) for task_id in ids]

    def list_tasks(self) -> list[dict[str, object]]:
        return [
            {
                "taskId": task_id,
                "role": task.role,
                "goal": task.goal,
                "backend": task.backend,
                "status": self._status_for(task_id),
            }
            for task_id, task in self._tasks.items()
        ]

    def list_results(self) -> list[SubagentResult]:
        for task_id, future in list(self._futures.items()):
            if future.done() and task_id not in self._results:
                self._results[task_id] = future.result()
        return list(self._results.values())

    def get_result(self, task_id: str) -> SubagentResult | None:
        if task_id in self._results:
            return self._results[task_id]
        future = self._futures.get(task_id)
        if future and future.done():
            self._results[task_id] = future.result()
            return self._results[task_id]
        return None

    def _run_backend(self, task: SubagentTask) -> SubagentResult:
        self._statuses[task.id] = "running"
        backend = self._backends[task.backend]
        started = self._started_at_ms.get(task.id, _now_ms())
        try:
            result = backend.run(task)
        except Exception as error:  # noqa: BLE001 - child failures must be data, not parent crashes.
            ended = _now_ms()
            result = SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status="failed",
                summary=str(error),
                errors=[str(error)],
                started_at_ms=started,
                ended_at_ms=ended,
            )
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
            self._event_sink(event)
