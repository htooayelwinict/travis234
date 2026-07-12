"""Stable contracts for app-owned coding process sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Mapping


DEFAULT_PROCESS_POLL_DELAY_MS = 1000


class ProcessState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    DRAINING = "draining"
    EXITED = "exited"
    TIMED_OUT = "timed_out"
    TERMINATED = "terminated"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {
            ProcessState.EXITED,
            ProcessState.TIMED_OUT,
            ProcessState.TERMINATED,
            ProcessState.FAILED,
        }


class StopCause(StrEnum):
    TIMEOUT = "timeout"
    ABORT_BEFORE_YIELD = "abort_before_yield"
    TERMINATE = "terminate"
    KILL = "kill"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class ProcessOwner:
    app_instance_id: str
    workspace_key: str
    origin: Literal["agent", "user"] = "agent"

    @property
    def persistence_scope(self) -> tuple[str, str]:
        return (self.workspace_key, self.origin)


@dataclass(frozen=True)
class ProcessCompletionRecord:
    session_id: str
    state: ProcessState
    exit_code: int | None
    output_size: int
    elapsed_ms: int
    completed_at: float
    launch_session_id: str | None
    failure_code: str | None
    tty: bool = False


@dataclass(frozen=True)
class ProcessLaunchRequest:
    command: str
    cwd: str
    env: Mapping[str, str]
    shell_path: str
    tty: bool = False
    rows: int = 24
    cols: int = 80
    timeout_seconds: float | None = None
    launch_session_id: str | None = None


@dataclass(frozen=True)
class OutputSlice:
    text: str
    cursor: int
    next_cursor: int


@dataclass(frozen=True)
class ProcessSnapshot:
    session_id: str
    state: ProcessState
    output: str
    cursor: int
    next_cursor: int
    output_size: int
    exit_code: int | None
    tty: bool
    elapsed_ms: int
    command: str = ""
    cwd: str = ""
    suggested_poll_delay_ms: int = DEFAULT_PROCESS_POLL_DELAY_MS
    durable_output: bool = False
    full_output_path: str | None = None
    failure_code: str | None = None

    def as_details(self) -> dict[str, object]:
        details: dict[str, object] = {
            "status": self.state.value,
            "sessionId": self.session_id,
            "cursor": self.cursor,
            "nextCursor": self.next_cursor,
            "outputSize": self.output_size,
            "exitCode": self.exit_code,
            "tty": self.tty,
            "elapsedMs": self.elapsed_ms,
            "suggestedPollDelayMs": self.suggested_poll_delay_ms,
        }
        if self.durable_output:
            details["durableOutput"] = True
        if self.full_output_path is not None:
            details["fullOutputPath"] = self.full_output_path
        if self.failure_code is not None:
            details["failureCode"] = self.failure_code
        return details


@dataclass(frozen=True)
class ProcessEvent:
    session_id: str
    state: ProcessState
    exit_code: int | None
    owner: ProcessOwner


class ProcessSessionError(RuntimeError):
    """Base error for managed process operations."""


class ProcessWaitCancelledError(ProcessSessionError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Process wait cancelled: {session_id}")
        self.session_id = session_id


class ProcessNotFoundError(ProcessSessionError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Process not found: {session_id}")
        self.session_id = session_id


class ProcessStateError(ProcessSessionError):
    pass


class ProcessLimitError(ProcessSessionError):
    pass


class ProcessClosedError(ProcessSessionError):
    pass


class ProcessInputLimitError(ProcessSessionError):
    pass


class InvalidCursorError(ProcessSessionError):
    def __init__(self, cursor: int, output_size: int) -> None:
        super().__init__(f"Invalid output cursor {cursor}; current output size is {output_size}")
        self.cursor = cursor
        self.output_size = output_size


__all__ = [
    "DEFAULT_PROCESS_POLL_DELAY_MS",
    "InvalidCursorError",
    "OutputSlice",
    "ProcessClosedError",
    "ProcessCompletionRecord",
    "ProcessEvent",
    "ProcessInputLimitError",
    "ProcessLaunchRequest",
    "ProcessLimitError",
    "ProcessNotFoundError",
    "ProcessOwner",
    "ProcessSessionError",
    "ProcessSnapshot",
    "ProcessState",
    "ProcessStateError",
    "ProcessWaitCancelledError",
    "StopCause",
]
