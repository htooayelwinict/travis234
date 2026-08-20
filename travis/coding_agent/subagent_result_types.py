"""Immutable public result values for delegated subagents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SubagentStatus = Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]
_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "timeout"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_identifier(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip() or not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"Unsupported subagent {field_name}: {value}")


def _validate_optional_string(field_name: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Subagent {field_name} must be a string when set")


def _validate_timestamps(started_at_ms: object, ended_at_ms: object) -> None:
    for value in (started_at_ms, ended_at_ms):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Subagent timestamps must be non-negative integers")
    if started_at_ms and ended_at_ms and ended_at_ms < started_at_ms:
        raise ValueError("Subagent ended_at_ms cannot be before started_at_ms")


def _validate_string_list(field_name: str, value: object) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Subagent {field_name} must be a list of strings")


def _validate_dict_list(field_name: str, value: object) -> None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Subagent {field_name} must be a list of dicts")


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
    structured_output: object | None = None
    validation_errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("task id", self.task_id),
            ("backend", self.backend),
            ("role", self.role),
        ):
            _validate_identifier(field_name, value)
        if not isinstance(self.status, str) or self.status not in _STATUSES:
            raise ValueError(f"Unsupported subagent status: {self.status}")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("Subagent summary is required")
        if not isinstance(self.final_response, str):
            raise ValueError("Subagent final_response must be a string")
        _validate_optional_string("child_session_id", self.child_session_id)
        _validate_optional_string("raw_log_path", self.raw_log_path)
        _validate_timestamps(self.started_at_ms, self.ended_at_ms)
        for field_name in ("files_changed", "artifacts", "errors"):
            _validate_string_list(field_name, getattr(self, field_name))
        if not isinstance(self.usage, dict):
            raise ValueError("Subagent usage must be a dict")
        _validate_dict_list("tool_trace", self.tool_trace)
        _validate_string_list("validation_errors", self.validation_errors)

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
            "structuredOutput": self.structured_output,
            "validationErrors": list(self.validation_errors),
        }


__all__ = ["SubagentResult", "SubagentStatus"]
