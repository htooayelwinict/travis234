"""Kernel-owned worker memory for retryable worker instances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas import ArtifactPayload, PlanStep, Result, Task, WorkerIssue


WRITE_TOOL_NAMES = {
    "apply_file_operations",
    "delete_file",
    "move_file",
    "replace_in_file",
    "write_file",
    "write_many_files",
}


@dataclass
class WorkerMemoryController:
    """Keeps compact, kernel-owned memory across worker instance respawns."""

    _attempts_by_step: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _write_operations_by_step: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _denials_by_step: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def record_attempt(
        self,
        *,
        step: PlanStep,
        attempt_id: str,
        result: Result,
        issues: list[WorkerIssue],
    ) -> dict[str, Any]:
        records = list(_iter_tool_records(result.artifacts))
        write_operations = [
            operation
            for record in records
            for operation in _write_operations_from_record(record)
            if operation.get("status") in {"applied", "already_done", "skipped"}
        ]
        denials = [
            {
                "tool_name": record.get("tool_name"),
                "arguments": record.get("arguments"),
                "denial": record.get("observation", {}).get("denial"),
            }
            for record in records
            if isinstance(record.get("observation"), dict)
            and record["observation"].get("denied") is True
        ]
        missing_required_write_paths = _missing_required_write_paths(
            issues=issues,
            result_metadata=result.metadata,
        )
        attempt = {
            "attempt_id": attempt_id,
            "step_id": step.step_id,
            "worker_type": step.worker_type,
            "status": result.status,
            "summary": result.summary,
            "usage": dict(result.usage),
            "artifact_ids": [artifact.id for artifact in result.artifacts],
            "issue_codes": [issue.code for issue in issues],
            "successful_write_count": len(
                [operation for operation in write_operations if operation.get("status") == "applied"]
            ),
            "already_done_count": len(
                [operation for operation in write_operations if operation.get("status") == "already_done"]
            ),
        }
        if missing_required_write_paths:
            attempt["missing_required_write_paths"] = missing_required_write_paths
        self._attempts_by_step.setdefault(step.step_id, []).append(attempt)
        if write_operations:
            self._write_operations_by_step.setdefault(step.step_id, []).extend(write_operations)
        if denials:
            self._denials_by_step.setdefault(step.step_id, []).extend(denials)
        return attempt

    def record_exception(
        self,
        *,
        step: PlanStep,
        attempt_id: str,
        exc: Exception,
        issue: WorkerIssue,
    ) -> dict[str, Any]:
        attempt = {
            "attempt_id": attempt_id,
            "step_id": step.step_id,
            "worker_type": step.worker_type,
            "status": "failed",
            "summary": str(exc),
            "usage": {},
            "artifact_ids": [],
            "issue_codes": [issue.code],
            "successful_write_count": 0,
            "already_done_count": 0,
        }
        self._attempts_by_step.setdefault(step.step_id, []).append(attempt)
        return attempt

    def inject_retry_memory(self, *, task: Task, step: PlanStep) -> tuple[Task, dict[str, Any] | None]:
        memory = self.memory_for_step(step.step_id)
        if memory is None:
            return task, None
        artifact = ArtifactPayload(
            id=f"kernel_memory_{step.step_id}",
            kind="kernel_memory",
            content=memory,
            producer="worker_kernel",
            step_id=step.step_id,
            metadata={"memory_controller": "worker_memory_v1"},
        )
        input_artifacts = [item for item in task.input_artifacts if item.id != artifact.id]
        input_artifacts.append(artifact)
        metadata = {
            **task.metadata,
            "kernel_memory": memory,
            "kernel_memory_artifact_id": artifact.id,
        }
        return task.model_copy(update={"input_artifacts": input_artifacts, "metadata": metadata}), memory

    def memory_for_step(self, step_id: str) -> dict[str, Any] | None:
        attempts = self._attempts_by_step.get(step_id) or []
        write_operations = self._write_operations_by_step.get(step_id) or []
        denials = self._denials_by_step.get(step_id) or []
        if not attempts and not write_operations and not denials:
            return None
        applied = [operation for operation in write_operations if operation.get("status") == "applied"]
        already_done = [
            operation for operation in write_operations if operation.get("status") == "already_done"
        ]
        written_paths = {
            _normalize_path(path)
            for operation in write_operations
            for path in operation.get("paths", [])
            if _normalize_path(path)
        }
        pending_required_write_paths = _pending_required_write_paths(
            attempts=attempts,
            written_paths=written_paths,
        )
        retry_guidance = [
            "Treat this memory as authoritative for the current step.",
            "Do not replay successful operations unless current filesystem state proves they are missing.",
            "Inspect current state, finish only the remaining work, then return all expected artifacts.",
        ]
        if pending_required_write_paths:
            retry_guidance.append(
                "Required write paths still missing from successful observations: "
                + ", ".join(pending_required_write_paths)
            )
        return {
            "step_id": step_id,
            "attempt_count": len(attempts),
            "attempts": attempts[-3:],
            "successful_write_operations": write_operations[-50:],
            "successful_write_count": len(applied),
            "already_done_count": len(already_done),
            "pending_required_write_paths": pending_required_write_paths,
            "denied_operations": denials[-10:],
            "retry_guidance": retry_guidance,
        }

    def has_successful_writes(self, step_id: str) -> bool:
        return any(
            operation.get("status") == "applied"
            for operation in self._write_operations_by_step.get(step_id, [])
        )

    def snapshot(self) -> dict[str, Any]:
        step_ids = sorted(set(self._attempts_by_step) | set(self._write_operations_by_step))
        return {
            step_id: self.memory_for_step(step_id)
            for step_id in step_ids
            if self.memory_for_step(step_id) is not None
        }


def _iter_tool_records(artifacts: list[ArtifactPayload]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for artifact in artifacts:
        content = artifact.content
        if artifact.kind == "tool_observation" and isinstance(content, dict):
            records.append(content)
        elif artifact.kind == "tool_observation_summary" and isinstance(content, dict):
            observations = content.get("observations")
            if isinstance(observations, list):
                records.extend(record for record in observations if isinstance(record, dict))
    return records


def _write_operations_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    tool_name = str(record.get("tool_name") or "")
    if tool_name not in WRITE_TOOL_NAMES:
        return []
    observation = record.get("observation")
    if not isinstance(observation, dict) or observation.get("denied") is True:
        return []
    if tool_name == "apply_file_operations":
        operations = observation.get("operations")
        if not isinstance(operations, list):
            return []
        return [
            {
                "tool_name": tool_name,
                "action": str(operation.get("action") or ""),
                "status": str(operation.get("status") or ""),
                "paths": [str(path) for path in operation.get("paths", []) if path],
                "summary": operation.get("summary"),
            }
            for operation in operations
            if isinstance(operation, dict)
        ]
    if tool_name == "write_many_files":
        return [
            {
                "tool_name": tool_name,
                "action": "write",
                "status": "applied",
                "paths": [str(item.get("path"))],
                "summary": "batch file written",
            }
            for item in observation.get("files_written", [])
            if isinstance(item, dict) and item.get("path")
        ]
    if tool_name == "move_file":
        status = "already_done" if observation.get("already_done") else "applied"
        return [
            {
                "tool_name": tool_name,
                "action": "move",
                "status": status,
                "paths": [
                    str(path)
                    for path in (observation.get("source"), observation.get("destination"))
                    if path
                ],
                "summary": observation.get("reason") or "file moved",
            }
        ]
    if tool_name in {"write_file", "replace_in_file", "delete_file"} and observation.get("path"):
        action = {"write_file": "write", "replace_in_file": "replace", "delete_file": "delete"}[tool_name]
        status = "already_done" if observation.get("deleted") is False else "applied"
        return [
            {
                "tool_name": tool_name,
                "action": action,
                "status": status,
                "paths": [str(observation["path"])],
                "summary": observation.get("reason") or f"{action} completed",
            }
        ]
    return []


def _missing_required_write_paths(
    *,
    issues: list[WorkerIssue],
    result_metadata: dict[str, Any],
) -> list[str]:
    paths: list[str] = []
    for source in [issue.metadata for issue in issues] + [result_metadata]:
        value = source.get("missing_required_write_paths") if isinstance(source, dict) else None
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, list):
            paths.extend(str(item) for item in value if item)
    return _dedupe_paths(paths)


def _pending_required_write_paths(
    *,
    attempts: list[dict[str, Any]],
    written_paths: set[str],
) -> list[str]:
    pending: list[str] = []
    for attempt in reversed(attempts):
        raw = attempt.get("missing_required_write_paths")
        if isinstance(raw, list) and raw:
            pending = [str(path) for path in raw if path]
            break
    return [path for path in _dedupe_paths(pending) if path not in written_paths]


def _dedupe_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        cleaned = _normalize_path(path)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def _normalize_path(path: Any) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
