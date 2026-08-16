"""Immutable public result values for delegated subagents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SubagentStatus = Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]
_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "timeout"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


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
            if not isinstance(value, str) or not value.strip() or not _ID_PATTERN.fullmatch(value):
                raise ValueError(f"Unsupported subagent {field_name}: {value}")
        if not isinstance(self.status, str) or self.status not in _STATUSES:
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
        if not isinstance(self.validation_errors, list) or any(
            not isinstance(item, str) for item in self.validation_errors
        ):
            raise ValueError("Subagent validation_errors must be a list of strings")

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
