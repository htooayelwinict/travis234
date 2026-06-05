"""LLM-backed worker groups and instance templates."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.artifact_aliases import canonical_artifact_id
from app.runtime_matrix import RuntimeMatrixLogger
from app.schemas import ArtifactPayload, PermissionSet, PlanStep, Result, Task, WorkerIssue
from app.worker_kernel.agent_loop import AgentRunLoop
from app.worker_kernel.artifact_contracts import artifact_contract, evaluate_artifact_quality
from app.worker_kernel.env_config import WorkerRuntimeConfig
from app.worker_kernel.model_adapter import JSONDecisionAdapter
from app.worker_kernel.observations import denial_observation, success_observation
from app.repair_policy import AGENT_INSTANCE_MAX_ROUNDS, WRITE_OPERATION_REPAIR_ATTEMPTS
from app.worker_kernel.prompting import build_agentic_prompt
from app.worker_kernel.registry import WorkerRegistry
from app.worker_kernel.tools import (
    MutationOperationDeniedError,
    ToolPermissionError,
    ToolUnavailableError,
    WorkerToolConfig,
    WorkerToolError,
    WorkerToolbox,
)
from app.worker_kernel.workers.agentic_templates import get_agentic_worker_templates
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


MUTATING_WORKER_TYPES = {"code_worker", "filesystem_worker"}
WRITE_TOOL_NAMES = {
    "apply_file_operations",
    "delete_file",
    "move_file",
    "replace_in_file",
    "write_file",
    "write_many_files",
    "write_json_manifest",
}
DOMAIN_MUTATION_ARTIFACT_IDS = {
    "manifest_file",
    "manifest_update_record",
    "manifest_validation",
    "moved_items_evidence",
    "moved_items_record",
}
REPO_READER_REQUIRED_OUTPUT_IDS = {
    "api_surface_map",
    "candidate_paths",
    "source_file_inventory",
    "source_files",
    "target_files",
    "test_command",
    "test_command_candidates",
    "test_files",
}


class WorkerToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class WorkerFinalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "blocked", "needs_replan"] = "completed"
    summary: str
    artifacts: list[ArtifactPayload] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[WorkerIssue] = Field(default_factory=list)
    recommended_action: str | None = None


class WorkerLLMDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[WorkerToolCall] = Field(default_factory=list)
    final_result: WorkerFinalResult | None = None


@dataclass
class WorkerGroupState:
    artifacts: list[ArtifactPayload] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    instance_results: list[dict[str, Any]] = field(default_factory=list)
    operation_denials: dict[str, int] = field(default_factory=dict)


class WorkerLLMController:
    def __init__(self, model_client: Any) -> None:
        self._model_client = model_client
        self.schema = WorkerLLMDecision.model_json_schema()
        self._adapter = JSONDecisionAdapter(
            model_client=model_client,
            schema=self.schema,
            normalizer=_normalize_worker_decision,
            validator=WorkerLLMDecision.model_validate,
        )

    def decide(self, *, stage: str, prompt: str) -> WorkerLLMDecision:
        return self._adapter.decide(stage=stage, prompt=prompt)


def _normalize_worker_decision(value: Any) -> Any:
    value = _parse_jsonish(value)
    if not isinstance(value, dict):
        return value

    data = dict(value)
    for noisy_key in ("analysis", "commentary", "explanation", "reasoning", "thought", "thoughts"):
        data.pop(noisy_key, None)
    if "final_result" in data:
        data["final_result"] = _parse_jsonish(data["final_result"])
    if "tool_calls" in data:
        data["tool_calls"] = _parse_jsonish(data["tool_calls"])
    if "function_call" in data:
        data["function_call"] = _parse_jsonish(data["function_call"])
    if "tool_call" in data:
        data["tool_call"] = _parse_jsonish(data["tool_call"])

    if "final_result" in data and isinstance(data["final_result"], dict):
        data["final_result"] = _normalize_final_result(data["final_result"])
    elif _looks_like_final_result(data):
        data = {"final_result": _normalize_final_result(data)}
    elif "function_call" in data and "tool_calls" not in data:
        data = {"tool_calls": [data["function_call"]]}
    elif "tool_call" in data and "tool_calls" not in data:
        data = {"tool_calls": [data["tool_call"]]}
    elif "name" in data and "tool_calls" not in data:
        data = {"tool_calls": [data]}

    tool_calls = data.get("tool_calls")
    if isinstance(tool_calls, dict):
        tool_calls = [tool_calls]
    if isinstance(tool_calls, list):
        normalized_calls = []
        for raw_call in tool_calls:
            raw_call = _parse_jsonish(raw_call)
            if not isinstance(raw_call, dict):
                normalized_calls.append(raw_call)
                continue
            call = _normalize_tool_call(raw_call)
            if "tool_name" not in call and "name" in call:
                call["tool_name"] = call.pop("name")
            elif "name" in call:
                call.pop("name")
            if "arguments" not in call and "args" in call:
                call["arguments"] = call.pop("args")
            elif "args" in call:
                call.pop("args")
            if isinstance(call.get("arguments"), str):
                call["arguments"] = _parse_arguments(call["arguments"])
            normalized_calls.append(call)
        data["tool_calls"] = normalized_calls
    return data


def _normalize_tool_call(raw_call: dict[str, Any]) -> dict[str, Any]:
    call = dict(raw_call)
    function = _parse_jsonish(call.pop("function", None))
    if isinstance(function, dict):
        if "tool_name" not in call:
            call["tool_name"] = function.get("name") or function.get("tool_name")
        if "arguments" not in call:
            call["arguments"] = _first_present(
                function,
                ("arguments", "args", "tool_args", "input", "parameters", "params", "kwargs"),
                default={},
            )

    for metadata_key in ("id", "call_id", "tool_call_id", "type"):
        call.pop(metadata_key, None)

    if "tool_name" not in call:
        for name_key in ("name", "tool", "toolName", "function_name"):
            if name_key in call:
                call["tool_name"] = call.pop(name_key)
                break
    for name_key in ("name", "tool", "toolName", "function_name"):
        call.pop(name_key, None)

    if "arguments" not in call:
        for argument_key in (
            "args",
            "tool_args",
            "input",
            "parameters",
            "params",
            "kwargs",
            "arguments_json",
            "input_json",
        ):
            if argument_key in call:
                call["arguments"] = call.pop(argument_key)
                break
    for argument_key in (
        "args",
        "tool_args",
        "input",
        "parameters",
        "params",
        "kwargs",
        "arguments_json",
        "input_json",
    ):
        call.pop(argument_key, None)
    if call.get("arguments") is None:
        call["arguments"] = {}

    if isinstance(call.get("tool_name"), str):
        repaired = _repair_embedded_tool_call(call["tool_name"])
        if repaired:
            call["tool_name"] = repaired["tool_name"]
            if not call.get("arguments") and isinstance(repaired.get("arguments"), dict):
                call["arguments"] = repaired["arguments"]
    return call


def _first_present(data: dict[str, Any], keys: tuple[str, ...], *, default: Any = None) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return default


def _looks_like_final_result(data: dict[str, Any]) -> bool:
    return any(key in data for key in ("status", "summary", "reason", "message", "missing_artifacts", "artifacts"))


def _kernel_memory_has_successful_writes(task: Task) -> bool:
    memory = task.metadata.get("kernel_memory")
    if not isinstance(memory, dict):
        return False
    if int(memory.get("successful_write_count") or 0) > 0:
        return True
    operations = memory.get("successful_write_operations")
    return isinstance(operations, list) and any(
        isinstance(operation, dict) and operation.get("status") == "applied"
        for operation in operations
    )


def _normalize_final_result(raw_final: dict[str, Any]) -> dict[str, Any]:
    final = dict(raw_final)
    allowed_keys = {
        "status",
        "summary",
        "artifacts",
        "errors",
        "warnings",
        "issues",
        "recommended_action",
        "reason",
        "message",
        "missing_artifacts",
    }
    artifact_fields = {key: final.pop(key) for key in list(final) if key not in allowed_keys}
    reason = final.pop("reason", None)
    message = final.pop("message", None)
    missing_artifacts = final.pop("missing_artifacts", None)
    if "status" in final:
        final["status"] = _normalize_status(final["status"])
    if "summary" not in final:
        final["summary"] = str(reason or message or final.get("status") or "Worker produced a final result.")
    if "errors" not in final and final.get("status") in {"failed", "blocked", "needs_replan"}:
        final["errors"] = [final["summary"]]

    artifacts = [
        _normalize_artifact(artifact, index=index)
        for index, artifact in enumerate(list(final.get("artifacts") or []), start=1)
    ]
    artifacts.extend(
        {"id": key, "content": content, "kind": "worker_field_artifact"}
        for key, content in artifact_fields.items()
    )
    if artifacts:
        final["artifacts"] = artifacts

    status = str(final.get("status") or "completed")
    issues = [_normalize_issue(issue, status=status) for issue in list(final.get("issues") or [])]
    if missing_artifacts:
        missing = missing_artifacts if isinstance(missing_artifacts, list) else [missing_artifacts]
        issues.append(
            {
                "issue_type": "plan_failure",
                "code": "missing_required_artifacts",
                "message": final["summary"],
                "retryable": False,
                "metadata": {"missing_artifacts": missing},
            }
        )
        final.setdefault(
            "recommended_action",
            "request a revised plan that produces the missing artifacts before this worker step",
        )
    if issues:
        final["issues"] = issues
    return final


def _normalize_artifact(value: Any, *, index: int) -> Any:
    if isinstance(value, ArtifactPayload):
        return value
    if isinstance(value, dict):
        artifact = dict(value)
        artifact = _canonicalize_artifact_dict(artifact)
        metadata = artifact.get("metadata")
        if _content_is_empty(artifact.get("content")) and _metadata_has_payload(metadata):
            artifact["content"] = dict(metadata)
            repaired_metadata = dict(metadata)
            repaired_metadata["content_repaired_from_metadata"] = True
            artifact["metadata"] = repaired_metadata
        return artifact
    if isinstance(value, str):
        canonical_id = canonical_artifact_id(value)
        metadata = {"worker_returned_bare_artifact_id": True, "artifact_index": index}
        if canonical_id != value:
            metadata["original_artifact_id"] = value
            metadata["canonical_artifact_id"] = canonical_id
        return {
            "id": canonical_id,
            "content": None,
            "kind": "worker_declared_artifact_id",
            "metadata": metadata,
        }
    return {
        "id": f"worker_artifact_{index}",
        "content": value,
        "kind": "worker_unstructured_artifact",
    }


def _canonicalize_artifact_dict(artifact: dict[str, Any]) -> dict[str, Any]:
    artifact_id = artifact.get("id")
    if not isinstance(artifact_id, str):
        return artifact
    canonical_id = canonical_artifact_id(artifact_id)
    if canonical_id == artifact_id:
        return artifact
    metadata = dict(artifact.get("metadata") or {})
    metadata.setdefault("original_artifact_id", artifact_id)
    metadata["canonical_artifact_id"] = canonical_id
    artifact["id"] = canonical_id
    artifact["metadata"] = metadata
    return artifact


def _content_is_empty(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, (list, tuple, set, dict)):
        return len(content) == 0
    return False


def _metadata_has_payload(metadata: Any) -> bool:
    if not isinstance(metadata, dict) or not metadata:
        return False
    internal_keys = {"worker_returned_bare_artifact_id", "artifact_index", "content_repaired_from_metadata"}
    return any(key not in internal_keys for key in metadata)


def _normalize_status(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower()
    return {
        "success": "completed",
        "succeeded": "completed",
        "complete": "completed",
        "done": "completed",
        "error": "failed",
        "failure": "failed",
        "replan": "needs_replan",
    }.get(normalized, value)


def _normalize_issue(value: Any, *, status: str) -> Any:
    if isinstance(value, WorkerIssue):
        return value
    issue_type = "plan_failure" if status in {"blocked", "needs_replan"} else "instance_failure"
    if isinstance(value, str):
        return {
            "issue_type": issue_type,
            "code": "worker_reported_issue",
            "message": value,
            "retryable": False,
        }
    if isinstance(value, dict):
        issue = dict(value)
        raw_issue_type = issue.pop("type", None)
        issue["issue_type"] = _normalize_issue_type(raw_issue_type or issue.get("issue_type") or issue_type)
        issue.setdefault("code", "worker_reported_issue")
        issue.setdefault(
            "message",
            str(issue.get("detail") or issue.get("reason") or issue.get("summary") or issue["code"]),
        )
        issue.setdefault("retryable", False)
        metadata = dict(issue.get("metadata") or {})
        allowed_keys = {"issue_type", "code", "message", "step_id", "worker_type", "attempt_id", "retryable", "metadata"}
        for key in list(issue):
            if key not in allowed_keys:
                metadata[key] = issue.pop(key)
        if metadata:
            issue["metadata"] = metadata
        issue.pop("reason", None)
        issue.pop("summary", None)
        issue.pop("detail", None)
        return issue
    return value


def _normalize_issue_type(value: Any) -> str:
    if not isinstance(value, str):
        return "instance_failure"
    normalized = value.strip().lower()
    return {
        "planner_failure": "plan_failure",
        "planning_failure": "plan_failure",
        "plan_level": "plan_failure",
        "planner_level": "plan_failure",
        "kernel_error": "kernel_failure",
        "tool_error": "instance_failure",
        "model_error": "instance_failure",
        "implementation_failure": "instance_failure",
        "verification_failure": "instance_failure",
        "worker_failure": "instance_failure",
    }.get(normalized, normalized)


def _parse_arguments(value: str) -> dict[str, Any]:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return {}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw or raw[0] not in {"{", "["}:
        return value
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return value


_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _repair_embedded_tool_call(value: str) -> dict[str, Any] | None:
    raw = value.strip()
    if _TOOL_NAME_RE.fullmatch(raw):
        return None

    tool_name = _extract_embedded_tool_name(raw)
    if not tool_name:
        return None

    repaired: dict[str, Any] = {"tool_name": tool_name}
    arguments_text = _extract_embedded_arguments_text(raw)
    if arguments_text:
        repaired["arguments"] = _parse_arguments(arguments_text)
    return repaired


def _extract_embedded_tool_name(value: str) -> str | None:
    patterns = (
        r"\btool_name['\"]?\s*[:=]\s*['\"]?([A-Za-z][A-Za-z0-9_-]{0,63})",
        r"\bname['\"]?\s*[:=]\s*['\"]?([A-Za-z][A-Za-z0-9_-]{0,63})",
        r"^tool\s+([A-Za-z][A-Za-z0-9_-]{0,63})",
        r"^([A-Za-z][A-Za-z0-9_-]{0,63})",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return None


def _extract_embedded_arguments_text(value: str) -> str | None:
    marker = re.search(r"\barguments['\"]?\s*[:=]\s*", value)
    if not marker:
        return None
    start = value.find("{", marker.end())
    if start < 0:
        return None

    depth = 0
    in_string: str | None = None
    escape = False
    for index, char in enumerate(value[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            continue
        if char in {"'", '"'}:
            in_string = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return value[start:]


def _looks_like_malformed_tool_name(value: str) -> bool:
    if _TOOL_NAME_RE.fullmatch(value):
        return False
    return any(fragment in value for fragment in ("arguments", "{", "}", "'", '"', ",", ":"))


class AgenticWorkerGroupRunner:
    def __init__(
        self,
        *,
        worker_type: str,
        templates: list[WorkerInstanceTemplate],
        controller: WorkerLLMController,
        toolbox: WorkerToolbox,
        max_rounds_per_instance: int = AGENT_INSTANCE_MAX_ROUNDS,
    ) -> None:
        if not templates:
            raise ValueError("AgenticWorkerGroupRunner requires at least one template.")
        self.worker_type = worker_type
        self._templates = templates
        self._controller = controller
        self._toolbox = toolbox
        self._agent_loop = AgentRunLoop(max_rounds=max_rounds_per_instance)

    def run(self, task: Task, trace: RuntimeMatrixLogger | None = None) -> Result:
        preflight_result = self._preflight_write_scope(task)
        if preflight_result is not None:
            return preflight_result

        state = WorkerGroupState(artifacts=list(task.input_artifacts))
        usage = {"tool_calls": 0, "model_calls": 0}
        last_summary = "Worker group completed."
        last_metadata: dict[str, Any] = {}
        skipped_templates: list[str] = []

        self._trace(
            trace,
            task=task,
            event="worker_group_started",
            status="started",
            details={"template_count": len(self._templates)},
        )

        for template in self._templates:
            if (
                state.instance_results
                and self._expected_outputs_satisfied(task, state.artifacts)
                and self._can_skip_template(task=task, template=template)
            ):
                skipped_templates.append(template.name)
                self._trace(
                    trace,
                    task=task,
                    template=template,
                    event="worker_instance_skipped",
                    status="skipped",
                    details={"reason": "expected_outputs_already_produced"},
                )
                continue

            self._trace(
                trace,
                task=task,
                template=template,
                event="worker_instance_started",
                status="started",
                details={"role": template.role},
            )
            result = self._run_template(
                task=task,
                template=template,
                state=state,
                usage=usage,
                trace=trace,
            )
            state.instance_results.append(result.model_dump(mode="json"))
            state.artifacts.extend(result.artifacts)
            last_summary = result.summary
            last_metadata = dict(result.metadata)
            self._trace(
                trace,
                task=task,
                template=template,
                event="worker_instance_completed",
                status=result.status,
                details={
                    "artifact_count": len(result.artifacts),
                    "tool_calls_used": usage["tool_calls"],
                    "model_calls_used": usage["model_calls"],
                },
            )
            if result.status != "completed":
                return result.model_copy(
                    update={
                        "producer": self.worker_type,
                        "artifacts": self._dedupe_artifacts(state.artifacts),
                        "usage": dict(usage),
                        "metadata": {
                            **result.metadata,
                            "worker_group_results": state.instance_results,
                            "worker_type": self.worker_type,
                            "skipped_worker_instances": skipped_templates,
                        },
                    }
                )

        artifacts = self._dedupe_artifacts(state.artifacts)
        quality = self._artifact_quality(task, artifacts)
        self._trace(
            trace,
            task=task,
            event="worker_artifact_quality_checked",
            status="failed" if quality["missing_count"] or quality["empty_count"] or quality["invalid_count"] else "completed",
            details={
                "expected_count": quality["expected_count"],
                "missing_count": quality["missing_count"],
                "empty_count": quality["empty_count"],
                "invalid_count": quality["invalid_count"],
                "synthesized_count": quality["synthesized_count"],
                "missing_artifacts": quality["missing_artifacts"],
                "empty_artifacts": quality["empty_artifacts"],
                "invalid_artifacts": quality["invalid_artifacts"],
                "synthesized_artifacts": quality["synthesized_artifacts"],
            },
        )
        if quality["missing_count"] or quality["empty_count"] or quality["invalid_count"]:
            synthesis_result = self._completed_mutation_fallback(
                task=task,
                template=self._templates[-1],
                state=state,
                usage=usage,
                base_artifacts=[
                    artifact
                    for artifact in artifacts
                    if canonical_artifact_id(artifact.id) not in set(task.expected_outputs)
                ],
            )
            if synthesis_result is not None and synthesis_result.status == "completed":
                synthesis_quality = self._artifact_quality(task, synthesis_result.artifacts)
                self._trace(
                    trace,
                    task=task,
                    event="worker_artifact_quality_repaired",
                    status=(
                        "completed"
                        if not (
                            synthesis_quality["missing_count"]
                            or synthesis_quality["empty_count"]
                            or synthesis_quality["invalid_count"]
                        )
                        else "failed"
                    ),
                    details={
                        "missing_count": synthesis_quality["missing_count"],
                        "empty_count": synthesis_quality["empty_count"],
                        "invalid_count": synthesis_quality["invalid_count"],
                        "synthesized_artifacts": synthesis_quality["synthesized_artifacts"],
                    },
                )
                if not (
                    synthesis_quality["missing_count"]
                    or synthesis_quality["empty_count"]
                    or synthesis_quality["invalid_count"]
                ):
                    metadata = dict(synthesis_result.metadata)
                    metadata["artifact_quality"] = synthesis_quality
                    metadata["worker_group_results"] = state.instance_results
                    metadata["worker_type"] = self.worker_type
                    metadata["skipped_worker_instances"] = skipped_templates
                    metadata["artifact_quality_repaired_after_model_output"] = True
                    return synthesis_result.model_copy(update={"metadata": metadata})
        missing = list(quality["missing_artifacts"])
        if missing:
            issue = WorkerIssue(
                issue_type="instance_failure",
                code="worker_output_contract_miss",
                message=f"worker group did not produce expected artifacts: {', '.join(missing)}",
                step_id=task.step_id,
                worker_type=self.worker_type,
                retryable=True,
                metadata={
                    "missing_artifacts": missing,
                    "produced_artifacts": [artifact.id for artifact in artifacts],
                    "expected_artifacts": list(task.expected_outputs),
                    "artifact_contract": self._expected_artifact_contract(task),
                },
            )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="failed",
                summary=issue.message,
                artifacts=artifacts,
                usage=dict(usage),
                errors=[issue.message],
                metadata={
                    "issues": [issue.model_dump(mode="json")],
                    "issue_type": issue.issue_type,
                    "issue_code": issue.code,
                    "retryable": issue.retryable,
                    "recommended_action": "retry the worker with a final-result-only artifact repair contract",
                    "expected_artifacts": list(task.expected_outputs),
                    "produced_artifacts": [artifact.id for artifact in artifacts],
                    "missing_artifacts": missing,
                    "artifact_contract": self._expected_artifact_contract(task),
                    "worker_group_results": state.instance_results,
                    "worker_type": self.worker_type,
                    "skipped_worker_instances": skipped_templates,
                    "artifact_quality": quality,
                },
            )
        empty = list(quality["empty_artifacts"])
        if empty:
            issue = WorkerIssue(
                issue_type="instance_failure",
                code="worker_artifact_content_empty",
                message=f"worker group produced empty expected artifacts: {', '.join(empty)}",
                step_id=task.step_id,
                worker_type=self.worker_type,
                retryable=True,
                metadata={
                    "empty_artifacts": empty,
                    "produced_artifacts": [artifact.id for artifact in artifacts],
                    "expected_artifacts": list(task.expected_outputs),
                    "artifact_contract": self._expected_artifact_contract(task),
                },
            )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="failed",
                summary=issue.message,
                artifacts=artifacts,
                usage=dict(usage),
                errors=[issue.message],
                metadata={
                    "issues": [issue.model_dump(mode="json")],
                    "issue_type": issue.issue_type,
                    "issue_code": issue.code,
                    "retryable": issue.retryable,
                    "recommended_action": "retry the worker with a final-result-only artifact repair contract",
                    "expected_artifacts": list(task.expected_outputs),
                    "produced_artifacts": [artifact.id for artifact in artifacts],
                    "empty_artifacts": empty,
                    "artifact_contract": self._expected_artifact_contract(task),
                    "artifact_quality": quality,
                    "worker_group_results": state.instance_results,
                    "worker_type": self.worker_type,
                    "skipped_worker_instances": skipped_templates,
                },
            )
        invalid = list(quality["invalid_artifacts"])
        if invalid:
            invalid_ids = sorted(
                {
                    str(item.get("artifact_id"))
                    for item in invalid
                    if isinstance(item, dict) and item.get("artifact_id")
                }
            )
            issue = WorkerIssue(
                issue_type="instance_failure",
                code="worker_artifact_contract_invalid",
                message=f"worker group produced invalid expected artifacts: {', '.join(invalid_ids)}",
                step_id=task.step_id,
                worker_type=self.worker_type,
                retryable=True,
                metadata={
                    "invalid_artifacts": invalid,
                    "produced_artifacts": [artifact.id for artifact in artifacts],
                    "expected_artifacts": list(task.expected_outputs),
                    "artifact_contract": self._expected_artifact_contract(task),
                },
            )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="failed",
                summary=issue.message,
                artifacts=artifacts,
                usage=dict(usage),
                errors=[issue.message],
                metadata={
                    "issues": [issue.model_dump(mode="json")],
                    "issue_type": issue.issue_type,
                    "issue_code": issue.code,
                    "retryable": issue.retryable,
                    "recommended_action": "retry the worker with a final-result-only artifact repair contract",
                    "expected_artifacts": list(task.expected_outputs),
                    "produced_artifacts": [artifact.id for artifact in artifacts],
                    "invalid_artifacts": invalid,
                    "artifact_contract": self._expected_artifact_contract(task),
                    "artifact_quality": quality,
                    "worker_group_results": state.instance_results,
                    "worker_type": self.worker_type,
                    "skipped_worker_instances": skipped_templates,
                },
            )

        self._trace(
            trace,
            task=task,
            event="worker_group_completed",
            status="completed",
            details={
                "artifact_count": len(artifacts),
                "skipped_worker_instances": skipped_templates,
            },
        )
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary=last_summary,
            artifacts=artifacts,
            usage=dict(usage),
            metadata={
                **last_metadata,
                "worker_group_results": state.instance_results,
                "worker_type": self.worker_type,
                "skipped_worker_instances": skipped_templates,
                "artifact_quality": quality,
            },
        )

    def _preflight_write_scope(self, task: Task) -> Result | None:
        if not task.permissions.write_files:
            return None
        if task.metadata.get("mode") == "bounded_mutation" or task.metadata.get("phase") == "MUTATE":
            return None
        try:
            scope = self._toolbox.validate_write_scope(task)
        except WorkerToolError as exc:
            return self._issue_result(
                task=task,
                template=self._templates[0],
                usage={"tool_calls": 0, "model_calls": 0},
                status="blocked",
                issue_type="kernel_failure",
                code="invalid_write_scope",
                message=str(exc),
                retryable=False,
            )
        if not scope["write_scope_paths"]:
            return self._issue_result(
                task=task,
                template=self._templates[0],
                usage={"tool_calls": 0, "model_calls": 0},
                status="blocked",
                issue_type="kernel_failure",
                code="invalid_write_scope",
                message="write_files was allowed but no write scope paths were provided",
                retryable=False,
            )
        return None

    def _run_template(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
        trace: RuntimeMatrixLogger | None = None,
    ) -> Result:
        def issue_result(
            status: Literal["failed", "blocked", "budget_exceeded", "needs_replan"],
            issue_type: str,
            code: str,
            message: str,
            retryable: bool,
        ) -> Result:
            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status=status,
                issue_type=issue_type,
                code=code,
                message=message,
                retryable=retryable,
            )

        return self._agent_loop.run(
            worker_type=self.worker_type,
            task=task,
            template=template,
            state=state,
            usage=usage,
            controller=self._controller,
            prompt_builder=self._prompt,
            execute_tool_calls=self._execute_tool_calls,
            handle_final_result=self._handle_final_decision,
            fallback_from_observations=self._fallback_from_observations,
            issue_result=issue_result,
            trace_event=self._trace,
            trace=trace,
        ).result

    def _handle_final_decision(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
        final: WorkerFinalResult,
    ) -> Result:
        if self._completed_mutation_without_write(
            task=task,
            final=final,
            state=state,
        ):
            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status="failed",
                issue_type="instance_failure",
                code="mutation_completed_without_write",
                message="bounded mutation returned completed without any successful write tool observation",
                retryable=True,
            )
        missing_required_writes = self._missing_required_write_paths(task=task, state=state)
        if final.status == "completed" and missing_required_writes:
            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status="failed",
                issue_type="instance_failure",
                code="mutation_completed_missing_required_writes",
                message=(
                    "bounded mutation returned completed without touching required write paths: "
                    + ", ".join(missing_required_writes)
                ),
                retryable=True,
                issue_metadata={"missing_required_write_paths": missing_required_writes},
            )
        return self._final_result(task=task, template=template, usage=usage, final=final)

    def minimum_model_calls(self, step: PlanStep) -> int:
        total = 0
        for template in self._templates:
            total += 2 if self._template_can_take_tool_turn(template, step.permissions, step.max_tool_calls) else 1
        minimum = max(1, total)
        phase = str(step.phase or "").upper()
        if self.worker_type == "verify_worker" and step.max_tool_calls > 0 and self._step_can_use_tools(step):
            minimum = max(minimum, 3)
        if (
            self.worker_type in MUTATING_WORKER_TYPES
            and (phase == "MUTATE" or step.mode == "bounded_mutation")
            and step.max_tool_calls > 0
            and step.permissions.write_files
        ):
            minimum = max(minimum, 3)
        return minimum

    def _step_can_use_tools(self, step: PlanStep) -> bool:
        return any(
            [
                step.permissions.read_files,
                step.permissions.write_files,
                step.permissions.run_commands,
                step.permissions.web_research,
            ]
        )

    def _template_can_take_tool_turn(
        self,
        template: WorkerInstanceTemplate,
        permissions: PermissionSet,
        max_tool_calls: int,
    ) -> bool:
        if max_tool_calls <= 0:
            return False
        permitted_tools = _permitted_tool_names(permissions)
        return any(tool_name in permitted_tools for tool_name in template.allowed_tools)

    def _execute_tool_calls(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
        tool_calls: list[WorkerToolCall],
        trace: RuntimeMatrixLogger | None = None,
    ) -> Result | None:
        for tool_index, tool_call in enumerate(tool_calls, start=1):
            if usage["tool_calls"] >= task.max_tool_calls:
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="budget_exceeded",
                    issue_type="instance_failure",
                    code="tool_budget_exceeded",
                    message="worker requested more tool calls than the task budget allows",
                    retryable=False,
                )
            if tool_call.tool_name not in template.allowed_tools:
                code = (
                    "tool_call_contract_error"
                    if _looks_like_malformed_tool_name(tool_call.tool_name)
                    else "tool_not_allowed_for_instance"
                )
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="failed",
                    issue_type="instance_failure",
                    code=code,
                    message=f"tool {tool_call.tool_name} is not allowed for instance {template.name}",
                    retryable=True,
                    issue_metadata={
                        "requested_tool_name": tool_call.tool_name,
                        "allowed_tools": list(template.allowed_tools),
                    },
                )
            usage["tool_calls"] += 1
            self._trace(
                trace,
                task=task,
                template=template,
                event="worker_tool_call_started",
                status="started",
                details={
                    "tool_name": tool_call.tool_name,
                    "tool_index": tool_index,
                    "reason": tool_call.reason,
                },
            )
            try:
                observation = self._toolbox.execute(
                    task=task,
                    tool_name=tool_call.tool_name,
                    arguments=tool_call.arguments,
                )
            except MutationOperationDeniedError as exc:
                denial = exc.denial
                denial_count = state.operation_denials.get(template.name, 0) + 1
                state.operation_denials[template.name] = denial_count
                repair_attempts = denial.policy.get("repair_attempts", WRITE_OPERATION_REPAIR_ATTEMPTS)
                if not isinstance(repair_attempts, int):
                    repair_attempts = WRITE_OPERATION_REPAIR_ATTEMPTS
                self._trace(
                    trace,
                    task=task,
                    template=template,
                    event="worker_tool_call_denied",
                    status="denied",
                    details={
                        "tool_name": tool_call.tool_name,
                        "tool_index": tool_index,
                        "code": denial.code,
                        "message": denial.message,
                        "denial_count": denial_count,
                        "repair_attempts": repair_attempts,
                        "rejected_paths": list(denial.rejected_paths),
                    },
                )
                if denial_count > repair_attempts:
                    return self._issue_result(
                        task=task,
                        template=template,
                        usage=usage,
                        status="failed",
                        issue_type="instance_failure",
                        code="write_operation_denied_after_repair",
                        message=(
                            "worker write operation was denied after repair attempts: "
                            f"{denial.message}"
                        ),
                        retryable=True,
                        issue_metadata={
                            "denial": denial.model_dump(mode="json"),
                            "denial_count": denial_count,
                            "repair_attempts": repair_attempts,
                        },
                    )
                record = {
                    "instance": template.name,
                    "tool_name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                    "observation": {
                        "denied": True,
                        "denial": denial.model_dump(mode="json"),
                        "instruction": "Revise the tool call to satisfy write_policy and continue this same task.",
                    },
                    "tool_observation": denial_observation(
                        tool_name=tool_call.tool_name,
                        denial=denial,
                    ).model_dump(mode="json"),
                }
                state.observations.append(record)
                state.artifacts.append(
                    ArtifactPayload(
                        id=f"{task.step_id}_{template.name}_tool_denial_{len(state.observations)}",
                        kind="tool_denial",
                        content=record,
                        producer=self.worker_type,
                        step_id=task.step_id,
                        metadata={"tool_name": tool_call.tool_name, "tool_index": tool_index},
                    )
                )
                return None
            except ToolUnavailableError as exc:
                self._trace(
                    trace,
                    task=task,
                    template=template,
                    event="worker_tool_call_failed",
                    status="blocked",
                    details={"tool_name": tool_call.tool_name, "error": str(exc), "code": exc.code},
                )
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="blocked",
                    issue_type=exc.issue_type,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    recommended_action="configure the worker runtime tool provider or run a kernel fallback path",
                )
            except ToolPermissionError as exc:
                self._trace(
                    trace,
                    task=task,
                    template=template,
                    event="worker_tool_call_failed",
                    status="failed",
                    details={"tool_name": tool_call.tool_name, "error": str(exc), "code": exc.code},
                )
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="failed",
                    issue_type=exc.issue_type,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                )
            except WorkerToolError as exc:
                self._trace(
                    trace,
                    task=task,
                    template=template,
                    event="worker_tool_call_failed",
                    status="failed",
                    details={"tool_name": tool_call.tool_name, "error": str(exc), "code": exc.code},
                )
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="failed",
                    issue_type=exc.issue_type,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                )

            self._trace(
                trace,
                task=task,
                template=template,
                event="worker_tool_call_completed",
                status="completed",
                details={
                    "tool_name": tool_call.tool_name,
                    "tool_index": tool_index,
                    "observation_keys": sorted(observation.keys()),
                    "returncode": observation.get("returncode"),
                },
            )
            record = {
                "instance": template.name,
                "tool_name": tool_call.tool_name,
                "arguments": tool_call.arguments,
                "observation": observation,
                "tool_observation": success_observation(
                    tool_name=tool_call.tool_name,
                    data=observation,
                ).model_dump(mode="json"),
            }
            state.observations.append(record)
            state.artifacts.append(
                ArtifactPayload(
                    id=f"{task.step_id}_{template.name}_tool_{len(state.observations)}",
                    kind="tool_observation",
                    content=record,
                    producer=self.worker_type,
                    step_id=task.step_id,
                    metadata={"tool_name": tool_call.tool_name, "tool_index": tool_index},
                )
            )
        return None

    def _expected_outputs_satisfied(self, task: Task, artifacts: list[ArtifactPayload]) -> bool:
        quality = self._artifact_quality(task, self._dedupe_artifacts(artifacts))
        return not quality["missing_artifacts"] and not quality["empty_artifacts"]

    def _can_skip_template(self, *, task: Task, template: WorkerInstanceTemplate) -> bool:
        if (
            self.worker_type == "repo_worker"
            and template.name == "repo_reader"
            and self._repo_task_needs_reader(task)
        ):
            return False
        return True

    def _repo_task_needs_reader(self, task: Task) -> bool:
        expected = {artifact_id.lower() for artifact_id in task.expected_outputs}
        if expected & REPO_READER_REQUIRED_OUTPUT_IDS:
            return True
        if task.metadata.get("plan_has_mutation_steps"):
            return True
        text = " ".join(
            str(part or "")
            for part in (
                task.instruction,
                task.metadata.get("objective"),
                task.metadata.get("strategy"),
                task.metadata.get("normalized_input"),
                task.metadata.get("user_goal"),
            )
        ).lower()
        return any(token in text for token in ("bug", "code", "fix", "source", "test"))

    def _trace(
        self,
        trace: RuntimeMatrixLogger | None,
        *,
        task: Task,
        event: str,
        status: str,
        template: WorkerInstanceTemplate | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if trace is None:
            return
        trace.record(
            component="worker_agentic_group",
            stage=str(task.metadata.get("phase") or "WORKER"),
            event=event,
            status=status,
            request_id=str(task.metadata.get("request_id") or ""),
            plan_id=str(task.metadata.get("plan_id") or ""),
            run_id=task.run_id,
            step_id=task.step_id,
            attempt_id=str(task.metadata.get("attempt_id") or ""),
            worker_type=self.worker_type,
            details={
                "worker_instance": template.name if template is not None else None,
                **(details or {}),
            },
        )

    def _final_result(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        usage: dict[str, int],
        final: WorkerFinalResult,
    ) -> Result:
        metadata: dict[str, Any] = {
            "worker_type": self.worker_type,
            "worker_instance": template.name,
        }
        if final.issues:
            metadata["issues"] = [issue.model_dump(mode="json") for issue in final.issues]
        if final.recommended_action:
            metadata["recommended_action"] = final.recommended_action
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status=final.status,
            summary=final.summary,
            artifacts=final.artifacts,
            usage=dict(usage),
            errors=final.errors,
            warnings=final.warnings,
            metadata=metadata,
        )

    def _issue_result(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        usage: dict[str, int],
        status: Literal["failed", "blocked", "budget_exceeded", "needs_replan"],
        issue_type: str,
        code: str,
        message: str,
        retryable: bool,
        recommended_action: str | None = None,
        issue_metadata: dict[str, Any] | None = None,
    ) -> Result:
        issue = WorkerIssue(
            issue_type=issue_type,
            code=code,
            message=message,
            step_id=task.step_id,
            worker_type=self.worker_type,
            retryable=retryable,
            metadata={"worker_instance": template.name, **(issue_metadata or {})},
        )
        metadata: dict[str, Any] = {
            "issues": [issue.model_dump(mode="json")],
            "issue_type": issue.issue_type,
            "issue_code": issue.code,
            "retryable": issue.retryable,
            "worker_type": self.worker_type,
            "worker_instance": template.name,
        }
        if recommended_action:
            metadata["recommended_action"] = recommended_action
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status=status,
            summary=message,
            usage=dict(usage),
            errors=[message],
            metadata=metadata,
        )

    def _fallback_from_observations(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
    ) -> Result:
        if not state.observations:
            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status="budget_exceeded",
                issue_type="instance_failure",
                code="model_budget_exceeded",
                message="worker model call budget was exhausted before completion",
                retryable=False,
            )

        artifacts = [
            ArtifactPayload(
                id=f"{task.step_id}_{template.name}_observations",
                kind="tool_observation_summary",
                content={"observations": state.observations, "fallback_reason": "model_budget_exhausted"},
                producer=self.worker_type,
                step_id=task.step_id,
                metadata={"worker_instance": template.name},
            )
        ]
        verification_result = self._verification_fallback_from_observations(
            task=task,
            template=template,
            state=state,
            usage=usage,
            base_artifacts=artifacts,
        )
        if verification_result is not None:
            return verification_result

        mutation_result = self._completed_mutation_fallback(
            task=task,
            template=template,
            state=state,
            usage=usage,
            base_artifacts=artifacts,
        )
        if mutation_result is not None:
            return mutation_result

        missing = list(task.expected_outputs)
        issue = WorkerIssue(
            issue_type="instance_failure",
            code="model_budget_exhausted_before_final_result",
            message=(
                "Worker collected tool observations but exhausted model budget before producing "
                f"expected artifacts: {', '.join(missing)}"
            ),
            step_id=task.step_id,
            worker_type=self.worker_type,
            retryable=False,
            metadata={"missing_artifacts": missing, "worker_instance": template.name},
        )
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="budget_exceeded",
            summary=issue.message,
            artifacts=artifacts,
            usage=dict(usage),
            errors=[issue.message],
            warnings=["model budget exhausted after tool observations"],
            metadata={
                "issues": [issue.model_dump(mode="json")],
                "issue_type": issue.issue_type,
                "issue_code": issue.code,
                "retryable": issue.retryable,
                "worker_type": self.worker_type,
                "worker_instance": template.name,
                "fallback": "tool_observation_summary",
                "recommended_action": "kernel should retry with a replacement worker instance or stop on budget ceiling",
            },
        )

    def _completed_mutation_without_write(
        self,
        *,
        task: Task,
        final: WorkerFinalResult,
        state: WorkerGroupState,
    ) -> bool:
        if self.worker_type not in MUTATING_WORKER_TYPES or not task.permissions.write_files:
            return False
        if final.status != "completed":
            return False
        if _kernel_memory_has_successful_writes(task):
            return False
        return not self._write_observations(state)

    def _missing_required_write_paths(self, *, task: Task, state: WorkerGroupState) -> list[str]:
        if self.worker_type not in MUTATING_WORKER_TYPES or not task.permissions.write_files:
            return []
        required = self._required_write_paths(task)
        if not required:
            return []
        observed = self._observed_write_path_set(task=task, state=state)
        return sorted(path for path in required if path not in observed)

    def _required_write_paths(self, task: Task) -> set[str]:
        write_scope = task.metadata.get("write_scope")
        if not isinstance(write_scope, dict):
            return set()
        raw_paths: list[Any] = []
        for key in ("create_paths", "update_paths", "manifest_paths"):
            value = write_scope.get(key)
            if isinstance(value, list):
                raw_paths.extend(value)
            elif value:
                raw_paths.append(value)
        return {path for path in (self._normalize_observed_path(value) for value in raw_paths) if path}

    def _observed_write_path_set(self, *, task: Task, state: WorkerGroupState) -> set[str]:
        paths = {
            self._normalize_observed_path(path)
            for observation in self._write_observations(state)
            for path in self._write_observation_paths(observation)
        }
        memory = task.metadata.get("kernel_memory")
        if isinstance(memory, dict):
            for operation in memory.get("successful_write_operations") or []:
                if not isinstance(operation, dict):
                    continue
                for path in operation.get("paths") or []:
                    paths.add(self._normalize_observed_path(path))
        return {path for path in paths if path}

    def _normalize_observed_path(self, value: Any) -> str:
        path = str(value or "").strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        return path

    def _write_observations(self, state: WorkerGroupState) -> list[dict[str, Any]]:
        return [
            observation
            for observation in state.observations
            if observation.get("tool_name") in WRITE_TOOL_NAMES
            and isinstance(observation.get("observation"), dict)
            and self._write_observation_paths(observation)
        ]

    def _write_observation_paths(self, observation: dict[str, Any]) -> list[str]:
        payload = observation.get("observation")
        if not isinstance(payload, dict):
            return []
        tool_name = observation.get("tool_name")
        if tool_name == "write_many_files":
            return [
                str(item.get("path"))
                for item in payload.get("files_written", [])
                if isinstance(item, dict) and item.get("path")
            ]
        if tool_name == "write_json_manifest":
            return [str(payload["path"])] if payload.get("path") else []
        if tool_name == "apply_file_operations":
            return [
                str(path)
                for item in payload.get("operations", [])
                if isinstance(item, dict) and item.get("status") in {"applied", "already_done", "skipped"}
                for path in item.get("paths", [])
                if path
            ]
        if tool_name == "move_file":
            return [
                str(path)
                for path in (payload.get("source"), payload.get("destination"))
                if path
            ]
        if tool_name in {"write_file", "replace_in_file", "delete_file"} and payload.get("path"):
            return [str(payload["path"])]
        return []

    def _verification_fallback_from_observations(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
        base_artifacts: list[ArtifactPayload],
    ) -> Result | None:
        if self.worker_type != "verify_worker":
            return None
        command_observations = [
            observation
            for observation in state.observations
            if observation.get("tool_name") in {"run_readonly_command", "run_focused_tests", "run_project_tests"}
            and isinstance(observation.get("observation"), dict)
        ]
        if not command_observations:
            return None

        failed_commands = [
            observation
            for observation in command_observations
            if int(observation["observation"].get("returncode", 0) or 0) != 0
        ]
        status = "failed" if failed_commands else "completed"
        summary = "Verification commands failed." if failed_commands else "Verification commands passed."
        artifacts = list(base_artifacts)
        for artifact_id in task.expected_outputs:
            artifacts.append(
                ArtifactPayload(
                    id=artifact_id,
                    kind="worker_output",
                    content={
                        "status": status,
                        "commands": [observation["observation"] for observation in command_observations],
                        "failed_commands": [observation["observation"] for observation in failed_commands],
                        "notes": "Synthesized by worker runtime after verification model budget exhaustion.",
                    },
                    producer=self.worker_type,
                    step_id=task.step_id,
                    metadata={
                        "worker_instance": template.name,
                        "synthesized_after_model_budget_exhaustion": True,
                    },
                )
            )

        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status=status,
            summary=summary,
            artifacts=artifacts,
            usage=dict(usage),
            errors=[summary] if failed_commands else [],
            warnings=["model budget exhausted after verification observations; artifacts synthesized deterministically"],
            metadata={
                "worker_type": self.worker_type,
                "worker_instance": template.name,
                "fallback": "verification_observation_synthesis",
                "synthesized_after_model_budget_exhaustion": True,
            },
        )

    def _completed_mutation_fallback(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
        base_artifacts: list[ArtifactPayload],
    ) -> Result | None:
        if self.worker_type not in MUTATING_WORKER_TYPES or not task.permissions.write_files:
            return None

        write_observations = self._write_observations(state)
        if not write_observations:
            return None
        missing_required_writes = self._missing_required_write_paths(task=task, state=state)
        if missing_required_writes:
            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status="failed",
                issue_type="instance_failure",
                code="mutation_completed_missing_required_writes",
                message=(
                    "mutation observations are missing required write paths: "
                    + ", ".join(missing_required_writes)
                ),
                retryable=True,
                issue_metadata={"missing_required_write_paths": missing_required_writes},
            )

        changed_paths = sorted(
            {
                path
                for observation in write_observations
                for path in self._write_observation_paths(observation)
            }
        )
        diff = self._git_diff_for_paths(task=task, paths=changed_paths)
        synthesized = list(base_artifacts)
        unsynthesizable_artifacts: list[str] = []
        for expected_artifact_id in task.expected_outputs:
            artifact_id = canonical_artifact_id(expected_artifact_id)
            content = self._synthesized_mutation_artifact(
                artifact_id=artifact_id,
                changed_paths=changed_paths,
                diff=diff,
                write_observations=write_observations,
            )
            if content is None:
                unsynthesizable_artifacts.append(artifact_id)
                continue
            synthesized.append(
                ArtifactPayload(
                    id=artifact_id,
                    kind="worker_output",
                    content=content,
                    producer=self.worker_type,
                    step_id=task.step_id,
                    metadata={
                        "worker_instance": template.name,
                        "synthesized_after_model_budget_exhaustion": True,
                    },
                )
            )
        if unsynthesizable_artifacts:
            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status="failed",
                issue_type="instance_failure",
                code="artifact_synthesis_incomplete",
                message=(
                    "mutation fallback cannot synthesize domain artifacts from generic write evidence: "
                    + ", ".join(unsynthesizable_artifacts)
                ),
                retryable=True,
                issue_metadata={
                    "unsynthesizable_artifacts": unsynthesizable_artifacts,
                    "available_write_tools": sorted(
                        {
                            str(observation.get("tool_name"))
                            for observation in write_observations
                            if observation.get("tool_name")
                        }
                    ),
                },
            )
        if "patch_diff" not in task.expected_outputs:
            synthesized.append(
                ArtifactPayload(
                    id="patch_diff",
                    kind="worker_output",
                    content={"paths": changed_paths, "diff": diff},
                    producer=self.worker_type,
                    step_id=task.step_id,
                    metadata={
                        "worker_instance": template.name,
                        "synthesized_after_model_budget_exhaustion": True,
                    },
                )
            )

        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary=(
                "Mutation write tools completed; required mutation artifacts were "
                "synthesized from tool observations after model budget exhaustion."
            ),
            artifacts=synthesized,
            usage=dict(usage),
            warnings=["model budget exhausted after write observations; artifacts synthesized deterministically"],
            metadata={
                "worker_type": self.worker_type,
                "worker_instance": template.name,
                "fallback": "mutation_observation_synthesis",
                "changed_paths": changed_paths,
                "synthesized_after_model_budget_exhaustion": True,
            },
        )

    def _synthesized_mutation_artifact(
        self,
        *,
        artifact_id: str,
        changed_paths: list[str],
        diff: str,
        write_observations: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        canonical_id = canonical_artifact_id(artifact_id)
        if canonical_id == "change_summary":
            return {
                "changed_paths": changed_paths,
                "summary": (
                    "Applied bounded mutation writes for "
                    f"{len(changed_paths)} changed path{'s' if len(changed_paths) != 1 else ''}."
                ),
                "risk_notes": [],
                "write_tools": [
                    {
                        "tool_name": observation.get("tool_name"),
                        "paths": self._write_observation_paths(observation),
                        "replacements": observation.get("observation", {}).get("replacements"),
                        "bytes_written": observation.get("observation", {}).get("bytes_written"),
                        "count": observation.get("observation", {}).get("count"),
                        "deleted": observation.get("observation", {}).get("deleted"),
                    }
                    for observation in write_observations
                ],
                "notes": "Synthesized by worker runtime after successful write observations.",
            }
        if canonical_id == "rollback_patch":
            return {
                "changed_paths": changed_paths,
                "diff": diff,
                "rollback_hint": f"git checkout -- {' '.join(changed_paths)}",
            }
        if canonical_id in DOMAIN_MUTATION_ARTIFACT_IDS:
            manifest = self._manifest_observation_payload(write_observations)
            if manifest is None:
                return None
            if canonical_id == "moved_items_record":
                payload = manifest.get("payload")
                return dict(payload) if isinstance(payload, dict) else None
            if canonical_id == "moved_items_evidence":
                return {
                    "move_pairs": self._move_pairs_from_write_observations(write_observations),
                    "total_moved": manifest.get("total_value") or manifest.get("total_artifacts"),
                    "manifest_path": manifest.get("manifest_path") or manifest.get("path"),
                }
            if canonical_id == "manifest_update_record":
                return {
                    "manifest_path": manifest.get("manifest_path") or manifest.get("path"),
                    "payload": manifest.get("payload"),
                    "fields_present": manifest.get("fields_present") or [],
                    "missing_fields": manifest.get("missing_fields") or [],
                    "counts_match": bool(manifest.get("counts_match")),
                    "total_artifacts": manifest.get("total_artifacts"),
                }
            if canonical_id == "manifest_file":
                return {
                    "manifest_path": manifest.get("manifest_path") or manifest.get("path"),
                    "payload": manifest.get("payload"),
                }
            return {
                "manifest_exists": True,
                "fields_present": manifest.get("fields_present") or [],
                "counts_match": bool(manifest.get("counts_match")),
                "total_artifacts": manifest.get("total_artifacts"),
            }
        return {
            "changed_paths": changed_paths,
            "diff": diff,
            "notes": f"Synthesized artifact for expected output {artifact_id}.",
        }

    def _manifest_observation_payload(self, write_observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        for observation in reversed(write_observations):
            if observation.get("tool_name") != "write_json_manifest":
                continue
            payload = observation.get("observation")
            if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
                return payload
        return None

    def _move_pairs_from_write_observations(self, write_observations: list[dict[str, Any]]) -> list[dict[str, str]]:
        move_pairs: list[dict[str, str]] = []
        for observation in write_observations:
            if observation.get("tool_name") != "apply_file_operations":
                continue
            payload = observation.get("observation")
            if not isinstance(payload, dict):
                continue
            for operation in payload.get("operations") or []:
                if not isinstance(operation, dict):
                    continue
                if operation.get("action") != "move":
                    continue
                if operation.get("status") not in {"applied", "already_done", "skipped"}:
                    continue
                paths = [str(path) for path in operation.get("paths") or [] if path]
                if len(paths) >= 2:
                    move_pairs.append({"source": paths[0], "destination": paths[1]})
        return move_pairs

    def _git_diff_for_paths(self, *, task: Task, paths: list[str]) -> str:
        if not task.permissions.read_files:
            return ""
        diffs: list[str] = []
        for path in paths:
            try:
                result = self._toolbox.execute(task=task, tool_name="diff_summary", arguments={"path": path})
            except WorkerToolError:
                continue
            diff = result.get("diff")
            if isinstance(diff, str) and diff:
                diffs.append(diff)
        return "\n".join(diffs)

    def _prompt(
        self,
        *,
        task: Task,
        template: WorkerInstanceTemplate,
        state: WorkerGroupState,
        usage: dict[str, int],
    ) -> str:
        remaining_tool_calls = max(0, task.max_tool_calls - usage["tool_calls"])
        available_tools = []
        if remaining_tool_calls > 0:
            available_tools = [
                tool for tool in self._toolbox.available_tools(task) if _tool_spec_name(tool) in template.allowed_tools
            ]
        return build_agentic_prompt(
            worker_type=self.worker_type,
            template=template,
            task=task,
            usage=usage,
            available_tools=available_tools,
            group_artifacts=state.artifacts,
            tool_observations=state.observations,
            expected_output_contract=self._expected_artifact_contract(task),
        )

    def _expected_artifact_contract(self, task: Task) -> list[dict[str, Any]]:
        return [self._artifact_contract(artifact_id, task=task) for artifact_id in task.expected_outputs]

    def _artifact_contract(self, artifact_id: str, *, task: Task | None = None) -> dict[str, Any]:
        return artifact_contract(artifact_id, contract_context=self._artifact_contract_context(task))

    def _artifact_quality(self, task: Task, artifacts: list[ArtifactPayload]) -> dict[str, Any]:
        return evaluate_artifact_quality(
            expected_outputs=list(task.expected_outputs),
            artifacts=artifacts,
            contract_context=self._artifact_contract_context(task),
        )

    def _artifact_contract_context(self, task: Task | None) -> dict[str, Any]:
        if task is None:
            return {}
        return {
            "required_json_keys": list(task.metadata.get("required_json_keys") or []),
            "literal_contract": list(task.metadata.get("literal_contract") or []),
            "phase": task.metadata.get("phase"),
            "mode": task.metadata.get("mode"),
            "worker_type": task.worker_type,
        }

    def _dedupe_artifacts(self, artifacts: list[ArtifactPayload]) -> list[ArtifactPayload]:
        deduped: dict[str, ArtifactPayload] = {}
        for artifact in artifacts:
            canonical_id = canonical_artifact_id(artifact.id)
            if canonical_id != artifact.id:
                metadata = dict(artifact.metadata)
                metadata.setdefault("original_artifact_id", artifact.id)
                metadata["canonical_artifact_id"] = canonical_id
                artifact = artifact.model_copy(update={"id": canonical_id, "metadata": metadata})
            deduped[canonical_id] = artifact
        return list(deduped.values())


def build_agentic_worker_registry(
    *,
    model_client: Any,
    config: WorkerRuntimeConfig,
    root_path: str | Path = ".",
) -> WorkerRegistry:
    controller = WorkerLLMController(model_client)
    toolbox = WorkerToolbox(
        WorkerToolConfig(
            root_path=Path(root_path),
            timeout_seconds=config.tool_timeout_seconds,
            max_file_bytes=config.max_file_bytes,
            web_search_provider=config.web_search_provider,
            web_search_api_key=config.web_search_api_key,
            web_search_max_results=config.web_search_max_results,
        )
    )
    registry = WorkerRegistry()
    for worker_type, templates in _worker_templates().items():
        registry.register_group(
            AgenticWorkerGroupRunner(
                worker_type=worker_type,
                templates=templates[: config.max_parallel_instances],
                controller=controller,
                toolbox=toolbox,
            )
        )
    return registry


def _worker_templates() -> dict[str, list[WorkerInstanceTemplate]]:
    return get_agentic_worker_templates()


def _tool_spec_name(tool: dict[str, Any]) -> str | None:
    name = tool.get("name")
    if isinstance(name, str):
        return name
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    return None


def _permitted_tool_names(permissions: PermissionSet) -> set[str]:
    tools: set[str] = set()
    if permissions.read_files:
        tools.update(
            {
                "repo_snapshot",
                "list_dir",
                "read_file",
                "read_many_files",
                "file_search",
                "text_search",
                "json_query",
                "git_status",
                "git_diff",
                "diff_summary",
                "mutation_scope_check",
            }
        )
    if permissions.write_files:
        tools.update(WRITE_TOOL_NAMES)
    if permissions.run_commands:
        tools.update({"runtime_capabilities", "run_readonly_command", "run_focused_tests", "run_project_tests"})
    if permissions.web_research:
        tools.update({"web_search", "web_fetch"})
    return tools
