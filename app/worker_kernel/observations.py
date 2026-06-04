"""Standard observations returned from worker tool calls."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ToolObservationStatus = Literal["completed", "denied", "failed", "blocked"]


class ToolObservation(BaseModel):
    """Agent-native observation envelope for one tool call."""

    model_config = ConfigDict(extra="forbid")

    status: ToolObservationStatus
    tool_name: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    repair_hint: str | None = None
    next_allowed_actions: list[str] = Field(default_factory=list)


def success_observation(*, tool_name: str, data: dict[str, Any]) -> ToolObservation:
    return ToolObservation(
        status="completed",
        tool_name=tool_name,
        summary=_success_summary(tool_name=tool_name, data=data),
        data=data,
    )


def denial_observation(*, tool_name: str, denial: Any) -> ToolObservation:
    payload = denial.model_dump(mode="json") if hasattr(denial, "model_dump") else dict(denial or {})
    return ToolObservation(
        status="denied",
        tool_name=tool_name,
        summary=str(payload.get("message") or f"{tool_name} was denied by write policy"),
        data={"denial": payload},
        error_code=str(payload.get("code") or "tool_operation_denied"),
        repair_hint="Revise the tool call to satisfy the write policy, then continue the same task.",
        next_allowed_actions=["narrow_paths", "split_batch", "use_allowed_path", "return_failed_if_impossible"],
    )


def error_observation(
    *,
    tool_name: str,
    status: ToolObservationStatus,
    code: str,
    message: str,
    repair_hint: str | None = None,
) -> ToolObservation:
    return ToolObservation(
        status=status,
        tool_name=tool_name,
        summary=message,
        error_code=code,
        repair_hint=repair_hint,
        next_allowed_actions=["retry_with_correct_tool_args", "return_failed_if_impossible"],
    )


def _success_summary(*, tool_name: str, data: dict[str, Any]) -> str:
    if "returncode" in data:
        return f"{tool_name} returned code {data.get('returncode')}"
    if "path" in data:
        return f"{tool_name} completed for {data.get('path')}"
    if "paths" in data and isinstance(data["paths"], list):
        return f"{tool_name} completed for {len(data['paths'])} path(s)"
    return f"{tool_name} completed"
