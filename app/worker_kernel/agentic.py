"""LLM-backed worker groups and instance templates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.runtime_matrix import RuntimeMatrixLogger
from app.schemas import ArtifactPayload, PermissionSet, PlanStep, Result, Task, WorkerIssue
from app.worker_kernel.env_config import WorkerRuntimeConfig
from app.worker_kernel.registry import WorkerRegistry
from app.worker_kernel.tools import (
    ToolPermissionError,
    ToolUnavailableError,
    WorkerToolConfig,
    WorkerToolError,
    WorkerToolbox,
)
from app.worker_kernel.workers.agentic_templates import get_agentic_worker_templates
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


MUTATING_WORKER_TYPES = {"code_worker", "filesystem_worker"}
WRITE_TOOL_NAMES = {"write_file", "write_many_files", "replace_in_file", "move_file", "delete_file"}


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


class WorkerLLMController:
    def __init__(self, model_client: Any) -> None:
        self._model_client = model_client
        self.schema = WorkerLLMDecision.model_json_schema()

    def decide(self, *, stage: str, prompt: str) -> WorkerLLMDecision:
        response = self._model_client.complete_json(
            stage=stage,
            prompt=prompt,
            schema=self.schema,
        )
        return WorkerLLMDecision.model_validate(_normalize_worker_decision(json.loads(response)))


def _normalize_worker_decision(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    data = dict(value)
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
    function = call.pop("function", None)
    if isinstance(function, dict):
        call.pop("id", None)
        call.pop("type", None)
        if "tool_name" not in call:
            call["tool_name"] = function.get("name")
        if "arguments" not in call:
            call["arguments"] = function.get("arguments", {})
    return call


def _looks_like_final_result(data: dict[str, Any]) -> bool:
    return any(key in data for key in ("status", "summary", "reason", "message", "missing_artifacts", "artifacts"))


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
        return value
    if isinstance(value, str):
        return {
            "id": value,
            "content": None,
            "kind": "worker_declared_artifact_id",
            "metadata": {"worker_returned_bare_artifact_id": True, "artifact_index": index},
        }
    return {
        "id": f"worker_artifact_{index}",
        "content": value,
        "kind": "worker_unstructured_artifact",
    }


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
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


class AgenticWorkerGroupRunner:
    def __init__(
        self,
        *,
        worker_type: str,
        templates: list[WorkerInstanceTemplate],
        controller: WorkerLLMController,
        toolbox: WorkerToolbox,
        max_rounds_per_instance: int = 4,
    ) -> None:
        if not templates:
            raise ValueError("AgenticWorkerGroupRunner requires at least one template.")
        self.worker_type = worker_type
        self._templates = templates
        self._controller = controller
        self._toolbox = toolbox
        self._max_rounds_per_instance = max_rounds_per_instance

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
            if state.instance_results and self._expected_outputs_satisfied(task, state.artifacts):
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
        missing = self._missing_expected_outputs(task, artifacts)
        if missing:
            issue = WorkerIssue(
                issue_type="plan_failure",
                code="missing_expected_artifacts",
                message=f"worker group did not produce expected artifacts: {', '.join(missing)}",
                step_id=task.step_id,
                worker_type=self.worker_type,
                retryable=False,
                metadata={"missing_artifacts": missing},
            )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary=issue.message,
                artifacts=artifacts,
                usage=dict(usage),
                errors=[issue.message],
                metadata={
                    "issues": [issue.model_dump(mode="json")],
                    "recommended_action": "request a plan that produces the missing worker artifacts",
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
            },
        )

    def _preflight_write_scope(self, task: Task) -> Result | None:
        if not task.permissions.write_files:
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
        rounds = 0
        while rounds < self._max_rounds_per_instance:
            if usage["model_calls"] >= task.max_model_calls:
                return self._fallback_from_observations(task=task, template=template, state=state, usage=usage)

            rounds += 1
            usage["model_calls"] += 1
            self._trace(
                trace,
                task=task,
                template=template,
                event="worker_model_call_started",
                status="started",
                details={
                    "round": rounds,
                    "model_calls_used_including_this_turn": usage["model_calls"],
                    "remaining_tool_calls": max(0, task.max_tool_calls - usage["tool_calls"]),
                },
            )
            try:
                decision = self._controller.decide(
                    stage=f"{self.worker_type}_{template.name}",
                    prompt=self._prompt(task=task, template=template, state=state, usage=usage),
                )
            except Exception as exc:
                self._trace(
                    trace,
                    task=task,
                    template=template,
                    event="worker_model_call_failed",
                    status="failed",
                    details={"error": str(exc), "round": rounds},
                )
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="failed",
                    issue_type="instance_failure",
                    code="worker_llm_error",
                    message=str(exc),
                    retryable=True,
                )

            self._trace(
                trace,
                task=task,
                template=template,
                event="worker_model_call_completed",
                status="completed",
                details={
                    "round": rounds,
                    "tool_call_count": len(decision.tool_calls),
                    "has_final_result": decision.final_result is not None,
                    "final_status": decision.final_result.status if decision.final_result else None,
                },
            )
            if decision.tool_calls:
                tool_result = self._execute_tool_calls(
                    task=task,
                    template=template,
                    state=state,
                    usage=usage,
                    tool_calls=decision.tool_calls,
                    trace=trace,
                )
                if tool_result is not None:
                    return tool_result
                continue

            if decision.final_result is not None:
                if self._completed_mutation_without_write(
                    task=task,
                    final=decision.final_result,
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
                return self._final_result(task=task, template=template, usage=usage, final=decision.final_result)

            return self._issue_result(
                task=task,
                template=template,
                usage=usage,
                status="failed",
                issue_type="instance_failure",
                code="empty_worker_decision",
                message="worker model returned neither tool_calls nor final_result",
                retryable=True,
            )

        return self._fallback_from_observations(task=task, template=template, state=state, usage=usage)

    def minimum_model_calls(self, step: PlanStep) -> int:
        total = 0
        for template in self._templates:
            total += 2 if self._template_can_take_tool_turn(template, step.permissions, step.max_tool_calls) else 1
        return max(1, total)

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
                return self._issue_result(
                    task=task,
                    template=template,
                    usage=usage,
                    status="failed",
                    issue_type="instance_failure",
                    code="tool_not_allowed_for_instance",
                    message=f"tool {tool_call.tool_name} is not allowed for instance {template.name}",
                    retryable=True,
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
        return not self._missing_expected_outputs(task, self._dedupe_artifacts(artifacts))

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
    ) -> Result:
        issue = WorkerIssue(
            issue_type=issue_type,
            code=code,
            message=message,
            step_id=task.step_id,
            worker_type=self.worker_type,
            retryable=retryable,
            metadata={"worker_instance": template.name},
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
        return not self._write_observations(state)

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
            if observation.get("tool_name") == "run_readonly_command"
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

        changed_paths = sorted(
            {
                path
                for observation in write_observations
                for path in self._write_observation_paths(observation)
            }
        )
        diff = self._git_diff_for_paths(task=task, paths=changed_paths)
        synthesized = list(base_artifacts)
        for artifact_id in task.expected_outputs:
            synthesized.append(
                ArtifactPayload(
                    id=artifact_id,
                    kind="worker_output",
                    content=self._synthesized_mutation_artifact(
                        artifact_id=artifact_id,
                        changed_paths=changed_paths,
                        diff=diff,
                        write_observations=write_observations,
                    ),
                    producer=self.worker_type,
                    step_id=task.step_id,
                    metadata={
                        "worker_instance": template.name,
                        "synthesized_after_model_budget_exhaustion": True,
                    },
                )
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
    ) -> dict[str, Any]:
        if artifact_id == "change_summary":
            return {
                "changed_paths": changed_paths,
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
        if artifact_id == "rollback_patch":
            return {
                "changed_paths": changed_paths,
                "diff": diff,
                "rollback_hint": f"git checkout -- {' '.join(changed_paths)}",
            }
        return {
            "changed_paths": changed_paths,
            "diff": diff,
            "notes": f"Synthesized artifact for expected output {artifact_id}.",
        }

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
        payload = {
            "worker_type": self.worker_type,
            "instance": {
                "name": template.name,
                "role": template.role,
                "system_prompt": template.system_prompt,
            },
            "task": task.model_dump(mode="json"),
            "runtime_budget": {
                "tool_calls_used": usage["tool_calls"],
                "remaining_tool_calls": remaining_tool_calls,
                "model_calls_used_including_this_turn": usage["model_calls"],
                "remaining_model_calls_after_this_turn": max(0, task.max_model_calls - usage["model_calls"]),
            },
            "expected_output_contract": [
                {
                    "id": artifact_id,
                    "required": True,
                    "artifact_shape": {
                        "id": artifact_id,
                        "content": "structured evidence or result payload",
                        "kind": "short artifact kind",
                    },
                }
                for artifact_id in task.expected_outputs
            ],
            "final_result_example": {
                "final_result": {
                    "status": "completed",
                    "summary": "One concise sentence describing the completed worker output.",
                    "artifacts": [
                        {
                            "id": artifact_id,
                            "content": {"evidence": [], "notes": "replace with real scoped content"},
                            "kind": "worker_output",
                        }
                        for artifact_id in task.expected_outputs[:3]
                    ],
                }
            },
            "available_tools": available_tools,
            "group_artifacts": [artifact.model_dump(mode="json") for artifact in state.artifacts],
            "tool_observations": state.observations,
            "instructions": [
                "Return JSON matching the schema.",
                "available_tools are OpenAI-style function tool specs; request only those names.",
                "For tool use, return {'tool_calls': [{'tool_name': '<name>', 'arguments': {...}}]}.",
                "OpenAI/OpenRouter function-call shape {'function': {'name': '<name>', 'arguments': '{...}'}} is also accepted.",
                "A response with tool_calls is an action turn; after observations, return a separate final_result turn.",
                "Use only listed tools.",
                "If available_tools is empty or remaining_tool_calls is 0, return final_result from observations or needs_replan.",
                "Return final_result only when expected artifacts can be produced.",
                "final_result.artifacts must be objects with id and content; never return bare artifact-name strings.",
                "For planner-level gaps, return {'final_result': {'status': 'needs_replan', 'summary': '<why>', 'issues': [...]}}.",
                "Use failed with an instance_failure issue for transient model/tool mistakes.",
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True, default=str)

    def _missing_expected_outputs(self, task: Task, artifacts: list[ArtifactPayload]) -> list[str]:
        produced = {
            artifact.id
            for artifact in artifacts
            if not artifact.metadata.get("worker_returned_bare_artifact_id")
        }
        return [artifact_id for artifact_id in task.expected_outputs if artifact_id not in produced]

    def _dedupe_artifacts(self, artifacts: list[ArtifactPayload]) -> list[ArtifactPayload]:
        deduped: dict[str, ArtifactPayload] = {}
        for artifact in artifacts:
            deduped[artifact.id] = artifact
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
        tools.update({"runtime_capabilities", "run_readonly_command", "run_focused_tests"})
    if permissions.web_research:
        tools.update({"web_search", "web_fetch"})
    return tools
