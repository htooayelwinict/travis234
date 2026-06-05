"""Worker-kernel loop decisions.

This module owns control-plane choices after compile, worker attempt, and
runtime failure events. Execution remains in the kernel, worker groups, and
toolbox.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import PlanStep, Result, ResultStatus, Task, WorkerIssue


FailureOwnership = Literal["instance", "kernel", "plan", "verification", "none"]
LoopAction = Literal[
    "continue_step",
    "retry_step",
    "request_replan",
    "block",
    "fail",
    "budget_exceeded",
    "kernel_error",
    "finalize",
]


class RetryInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str
    next_instance_instruction: str
    required_first_action: str | None = None
    prohibited_actions: list[str] = Field(default_factory=list)
    output_contract_reminder: str | None = None
    budget_hint: str | None = None
    diagnostic_source: str = "deterministic"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_prompt_text(self) -> str:
        parts = [self.next_instance_instruction]
        if self.required_first_action:
            parts.append(f"Required first action: {self.required_first_action}")
        if self.prohibited_actions:
            parts.append("Do not: " + "; ".join(self.prohibited_actions))
        if self.output_contract_reminder:
            parts.append(self.output_contract_reminder)
        if self.budget_hint:
            parts.append(self.budget_hint)
        return " ".join(part.strip() for part in parts if part and part.strip())


class LoopDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: LoopAction
    ownership: FailureOwnership = "none"
    status: ResultStatus | None = None
    reason_code: str
    summary: str
    retryable: bool = False
    should_record_retry: bool = False
    terminal_status: ResultStatus | None = None
    retry_instruction: RetryInstruction | None = None
    retry_adjustments: list[dict[str, Any]] = Field(default_factory=list)
    replan_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerRetryAdvisor:
    """Optional LLM retry advisor that can only emit retry instructions."""

    def __init__(self, model_client: Any) -> None:
        self._model_client = model_client
        self._schema = RetryInstruction.model_json_schema()

    def advise(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: PlanStep,
        reason_code: str,
    ) -> RetryInstruction:
        prompt = {
            "role": "worker_retry_advisor",
            "instruction": (
                "Return one RetryInstruction JSON object. Do not request tools, "
                "do not produce a plan, and do not change the task objective."
            ),
            "step": step.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "reason_code": reason_code,
        }
        response = self._model_client.complete_json(
            stage="worker_retry_advisor",
            prompt=json.dumps(prompt, indent=2, sort_keys=True),
            schema=self._schema,
        )
        instruction = RetryInstruction.model_validate(json.loads(response))
        return instruction.model_copy(update={"diagnostic_source": "llm_retry_advisor"})


class WorkerLoopController:
    """Deterministic control policy for worker-kernel loops."""

    def __init__(self, retry_advisor: WorkerRetryAdvisor | None = None) -> None:
        self._retry_advisor = retry_advisor

    runtime_issue_codes = {
        "budget_exceeded",
        "empty_worker_decision",
        "insufficient_tool_budget",
        "model_budget_exceeded",
        "model_budget_exhausted_before_final_result",
        "model_behavior_error",
        "tool_budget_exceeded",
        "tool_call_contract_error",
        "tool_execution_error",
        "tool_not_allowed_for_instance",
        "tool_permission_denied",
        "worker_output_contract_miss",
        "worker_artifact_content_empty",
        "worker_llm_error",
        "worker_exception",
        "write_operation_denied_after_repair",
        "mutation_completed_without_write",
        "mutation_completed_missing_required_writes",
    }
    non_retryable_kernel_codes = {
        "invalid_write_scope",
        "tool_unavailable",
        "unknown_worker_group",
    }

    def decide_after_missing_input(
        self,
        *,
        step: PlanStep,
        missing_artifacts: list[str],
        can_replan: bool,
    ) -> LoopDecision:
        action: LoopAction = "request_replan" if can_replan else "block"
        status: ResultStatus = "needs_replan" if can_replan else "blocked"
        return LoopDecision(
            action=action,
            ownership="plan",
            status=status,
            terminal_status=status,
            reason_code="missing_input_artifacts",
            summary=f"step {step.step_id} is missing required input artifacts",
            replan_reason="missing required input artifacts" if can_replan else None,
            metadata={"missing_artifacts": missing_artifacts},
        )

    def decide_after_invalid_write_scope(
        self,
        *,
        step: PlanStep,
        message: str,
        metadata: dict[str, Any],
    ) -> LoopDecision:
        return LoopDecision(
            action="block",
            ownership="kernel",
            status="blocked",
            terminal_status="blocked",
            reason_code="invalid_write_scope",
            summary=message,
            metadata={"step_id": step.step_id, **metadata},
        )

    def decide_after_exception(
        self,
        *,
        step: PlanStep,
        exc: Exception,
        retry_available: bool,
    ) -> LoopDecision:
        if isinstance(exc, ValueError) and "Unknown worker_type" in str(exc):
            return LoopDecision(
                action="kernel_error",
                ownership="kernel",
                status="kernel_error",
                terminal_status="kernel_error",
                reason_code="unknown_worker_group",
                summary=str(exc),
            )
        if retry_available:
            return LoopDecision(
                action="retry_step",
                ownership="instance",
                status="failed",
                reason_code="worker_exception",
                summary=str(exc),
                retryable=True,
                should_record_retry=True,
                retry_instruction=RetryInstruction(
                    cause="worker_exception",
                    next_instance_instruction=(
                        "This is a replacement worker instance after a runtime exception. "
                        "Use the same task scope, avoid repeating the failed behavior, and "
                        "return a valid final_result or permitted tool_calls."
                    ),
                ),
                metadata={"step_id": step.step_id},
            )
        return LoopDecision(
            action="fail",
            ownership="instance",
            status="failed",
            terminal_status="failed",
            reason_code="worker_exception",
            summary=str(exc),
            metadata={"step_id": step.step_id},
        )

    def decide_after_attempt(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: PlanStep,
        retry_available: bool,
        mutation_already_completed: bool = False,
    ) -> LoopDecision:
        if (
            result.status == "completed"
            and self.is_verification_step(step)
            and self.has_failed_verification_payload(result)
        ):
            terminal_status: ResultStatus = (
                "completed_with_failed_verification" if mutation_already_completed else "failed"
            )
            return LoopDecision(
                action="fail",
                ownership="verification",
                status=result.status,
                terminal_status=terminal_status,
                reason_code="verification_artifact_failed",
                summary=result.summary or "verification artifacts report failure",
                metadata={"verification_artifact_failed": True},
            )

        if result.status == "completed":
            return LoopDecision(
                action="continue_step",
                ownership="none",
                status="completed",
                reason_code="worker_completed",
                summary=result.summary,
            )

        retryable_instance_failure = any(
            issue.issue_type == "instance_failure" and issue.retryable
            for issue in issues
        )
        worker_runtime_failure = self.is_worker_runtime_owned_failure(
            result=result,
            issues=issues,
            step=step,
        )
        reason_code = self.reason_code(result=result, issues=issues, step=step)
        ownership = self.failure_ownership(
            result=result,
            issues=issues,
            step=step,
            worker_runtime_failure=worker_runtime_failure,
        )

        if (
            result.status in {"failed", "needs_replan", "budget_exceeded", "blocked"}
            and (retryable_instance_failure or worker_runtime_failure)
            and retry_available
        ):
            return LoopDecision(
                action="retry_step",
                ownership=ownership,
                status=result.status,
                reason_code=reason_code,
                summary=result.summary,
                retryable=True,
                should_record_retry=True,
                retry_instruction=self.retry_instruction_for(
                    result=result,
                    issues=issues,
                    step=step,
                    reason_code=reason_code,
                ),
                metadata={
                    "worker_runtime_failure": worker_runtime_failure,
                    "retryable_instance_failure": retryable_instance_failure,
                },
            )

        if result.status == "needs_replan" and worker_runtime_failure:
            return LoopDecision(
                action="fail",
                ownership=ownership,
                status="needs_replan",
                terminal_status="failed",
                reason_code=reason_code,
                summary=result.summary,
                metadata={"worker_runtime_failure": worker_runtime_failure},
            )

        if result.status == "needs_replan":
            return LoopDecision(
                action="request_replan",
                ownership="plan",
                status="needs_replan",
                terminal_status="needs_replan",
                reason_code=reason_code,
                summary=result.summary,
                replan_reason=result.summary,
                metadata={"worker_runtime_failure": worker_runtime_failure},
            )

        if result.status == "budget_exceeded":
            return LoopDecision(
                action="budget_exceeded",
                ownership=ownership,
                status="budget_exceeded",
                terminal_status="budget_exceeded",
                reason_code=reason_code,
                summary=result.summary,
            )

        if result.status == "blocked":
            return LoopDecision(
                action="block",
                ownership=ownership,
                status="blocked",
                terminal_status="blocked",
                reason_code=reason_code,
                summary=result.summary,
            )

        if result.status == "kernel_error":
            return LoopDecision(
                action="kernel_error",
                ownership="kernel",
                status="kernel_error",
                terminal_status="kernel_error",
                reason_code=reason_code,
                summary=result.summary,
            )

        terminal_status: ResultStatus = result.status
        if (
            result.status == "failed"
            and self.is_verification_step(step)
            and mutation_already_completed
            and self.has_verification_command_evidence(result)
        ):
            terminal_status = "completed_with_failed_verification"
        return LoopDecision(
            action="fail",
            ownership=ownership,
            status=result.status,
            terminal_status=terminal_status,
            reason_code=reason_code,
            summary=result.summary,
        )

    def build_retry_task(
        self,
        *,
        task: Task,
        result: Result,
        issues: list[WorkerIssue],
        decision: LoopDecision,
    ) -> tuple[Task, list[dict[str, Any]]]:
        adjustments: list[dict[str, Any]] = []
        usage = result.usage or {}
        text = " ".join(
            [
                result.summary or "",
                " ".join(result.errors or []),
                " ".join(issue.code for issue in issues),
            ]
        ).lower()
        verification_retry = self.is_verification_task(task) and not self.has_verification_command_evidence(result)

        max_tool_calls = task.max_tool_calls
        if (
            "tool" in text
            or "remaining_tool_calls" in text
            or verification_retry
            or int(usage.get("tool_calls", 0) or 0) >= task.max_tool_calls
        ):
            max_tool_calls = max(task.max_tool_calls + 2, task.max_tool_calls * 2, 2)
            adjustments.append(
                {
                    "field": "max_tool_calls",
                    "from": task.max_tool_calls,
                    "to": max_tool_calls,
                    "reason": "local retry after worker/tool budget or tool-call failure",
                }
            )

        max_model_calls = task.max_model_calls
        if (
            "model" in text
            or "final" in text
            or "budget" in text
            or "worker_artifact_content_empty" in text
            or "worker_output_contract_miss" in text
            or "workerllmdecision" in text
            or verification_retry
            or int(usage.get("model_calls", 0) or 0) >= task.max_model_calls
        ):
            max_model_calls = max(task.max_model_calls + 1, task.max_model_calls * 2, 2)
            adjustments.append(
                {
                    "field": "max_model_calls",
                    "from": task.max_model_calls,
                    "to": max_model_calls,
                    "reason": "local retry after worker/model/finalization failure",
                }
            )

        metadata = dict(task.metadata)
        if decision.retry_instruction is not None:
            metadata["runtime_retry_instruction"] = decision.retry_instruction.as_prompt_text()
            metadata["runtime_retry_reason_code"] = decision.reason_code

        if verification_retry:
            metadata["force_verification_command_first"] = True
            metadata["verification_retry_reason"] = "verification_failed_before_command"
        elif decision.reason_code in {"worker_output_contract_miss", "worker_artifact_content_empty"}:
            metadata["force_final_result_artifacts"] = True
        elif decision.reason_code in {"tool_call_contract_error", "tool_not_allowed_for_instance"}:
            metadata["force_strict_tool_call_shape"] = True

        retries = list(metadata.get("local_retry_adjustments") or [])
        if adjustments:
            retries.extend(adjustments)
        else:
            retries.append({"reason": "local retry without budget adjustment", "issue_code": decision.reason_code})
        metadata["local_retry_adjustments"] = retries

        return task.model_copy(
            update={
                "max_tool_calls": max_tool_calls,
                "max_model_calls": max_model_calls,
                "metadata": metadata,
            }
        ), adjustments

    def is_worker_runtime_owned_failure(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: PlanStep | None = None,
    ) -> bool:
        if step is not None and self.verification_failed_before_command(step=step, result=result, issues=issues):
            return True
        if result.status == "budget_exceeded":
            return True
        if any(issue.issue_type == "instance_failure" and issue.retryable for issue in issues):
            return True

        issue_codes = {issue.code for issue in issues}
        if issue_codes & self.non_retryable_kernel_codes:
            return False
        if issue_codes & self.runtime_issue_codes:
            return True

        text = self.result_issue_text(result=result, issues=issues)
        runtime_fragments = (
            "remaining_tool_calls",
            "remaining_model_calls",
            "tool budget",
            "model budget",
            "budget exhausted",
            "tool observations",
            "tool call",
            "worker model call budget",
            "validation errors for workerllmdecision",
            "worker output contract",
        )
        return any(fragment in text for fragment in runtime_fragments)

    def retry_instruction_for(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: PlanStep,
        reason_code: str,
    ) -> RetryInstruction:
        if self.verification_failed_before_command(step=step, result=result, issues=issues):
            return RetryInstruction(
                cause="verification_failed_before_command",
                next_instance_instruction=(
                    "This is a replacement VERIFY instance. Run run_project_tests, "
                    "run_focused_tests, or an explicit verification_plan command before final_result."
                ),
                required_first_action="Execute a permitted verification command before final_result.",
                prohibited_actions=["capability discovery unless command selection is impossible"],
                output_contract_reminder="Return exact command, returncode, stdout/stderr evidence.",
                budget_hint="Use the expanded retry budget for verification, not extra exploration.",
            )
        if reason_code in {"worker_output_contract_miss", "worker_artifact_content_empty"}:
            return RetryInstruction(
                cause=reason_code,
                next_instance_instruction=(
                    "This is a replacement worker instance after an output artifact quality failure. "
                    "Do not call tools unless essential. Return final_result with every expected artifact id exactly once."
                ),
                prohibited_actions=["returning bare artifact-name strings", "returning empty artifact content"],
                output_contract_reminder=(
                    "Each artifact content must be non-null and non-empty, using the expected_output_contract schemas."
                ),
            )
        if reason_code in {"tool_call_contract_error", "tool_not_allowed_for_instance"}:
            return RetryInstruction(
                cause=reason_code,
                next_instance_instruction=(
                    "This is a replacement worker instance after a malformed or disallowed tool-call envelope. "
                    "Use only exact names from available_tools."
                ),
                required_first_action="Return valid tool_calls JSON or a valid final_result.",
                prohibited_actions=["embedding JSON inside tool_name", "requesting tools not listed in available_tools"],
                output_contract_reminder=(
                    "Tool-call JSON shape is {'tool_calls':[{'tool_name':'name','arguments':{...}}]}."
                ),
            )
        if reason_code == "write_operation_denied_after_repair":
            return RetryInstruction(
                cause=reason_code,
                next_instance_instruction=(
                    "This is a replacement mutation instance after write operation denial. "
                    "Use task.metadata.write_policy as the hard boundary and propose narrower operations."
                ),
                required_first_action="Inspect write_policy and previous denial metadata before any write tool call.",
                prohibited_actions=["writing outside strict_allowed_paths", "oversized write_many_files batches"],
            )
        if reason_code == "mutation_completed_without_write":
            return RetryInstruction(
                cause=reason_code,
                next_instance_instruction=(
                    "This is a replacement mutation instance after a previous instance returned "
                    "completed without any successful write-tool evidence. Treat final_result as "
                    "forbidden until a permitted write tool observation or kernel_memory proves the change."
                ),
                required_first_action=(
                    "Inspect task.metadata.write_policy and current filesystem state, then call a permitted "
                    "write tool for the required change before final_result."
                ),
                prohibited_actions=[
                    "returning final_result before a successful write observation",
                    "claiming a file change from reasoning alone",
                ],
                output_contract_reminder=(
                    "Return expected artifacts only after tool evidence proves the mutation happened."
                ),
            )
        if reason_code == "mutation_completed_missing_required_writes":
            missing_paths = self._missing_required_write_paths(result=result, issues=issues)
            path_text = ", ".join(missing_paths) if missing_paths else "the missing required write paths"
            return RetryInstruction(
                cause=reason_code,
                next_instance_instruction=(
                    "This is a replacement mutation instance after a previous instance completed "
                    f"without writing required paths: {path_text}. Use kernel_memory to avoid replaying "
                    "successful write operations. Finish only the remaining required writes, then return "
                    "final_result with the expected artifacts."
                ),
                required_first_action=(
                    f"Inspect or create/update these missing paths before final_result: {path_text}."
                ),
                prohibited_actions=[
                    "returning final_result before the missing required paths have successful write observations",
                    "replaying successful move/write operations from kernel_memory unless filesystem state proves they are missing",
                ],
                output_contract_reminder=(
                    "For manifest/report outputs, write the exact required file path and include the artifact summary."
                ),
                metadata={"missing_required_write_paths": missing_paths},
            )
        advisor_instruction = self._retry_advisor_instruction(
            result=result,
            issues=issues,
            step=step,
            reason_code=reason_code,
        )
        if advisor_instruction is not None:
            return advisor_instruction
        return RetryInstruction(
            cause=reason_code,
            next_instance_instruction=(
                "This is a replacement worker instance after a runtime-owned failure. "
                "Continue the same step with the adjusted budget and return valid structured output."
            ),
        )

    def _missing_required_write_paths(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
    ) -> list[str]:
        paths: list[str] = []
        for source in [issue.metadata for issue in issues] + [result.metadata]:
            value = source.get("missing_required_write_paths") if isinstance(source, dict) else None
            if isinstance(value, str):
                paths.append(value)
            elif isinstance(value, list):
                paths.extend(str(item) for item in value if item)
        normalized: list[str] = []
        seen: set[str] = set()
        for path in paths:
            cleaned = path.strip().replace("\\", "/")
            while cleaned.startswith("./"):
                cleaned = cleaned[2:]
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized

    def _retry_advisor_instruction(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: PlanStep,
        reason_code: str,
    ) -> RetryInstruction | None:
        if self._retry_advisor is None:
            return None
        try:
            return self._retry_advisor.advise(
                result=result,
                issues=issues,
                step=step,
                reason_code=reason_code,
            )
        except Exception:
            return None

    def reason_code(self, *, result: Result, issues: list[WorkerIssue], step: PlanStep | None = None) -> str:
        if step is not None and self.verification_failed_before_command(step=step, result=result, issues=issues):
            return "verification_failed_before_command"
        for issue in issues:
            if issue.code:
                return issue.code
        issue_code = result.metadata.get("issue_code")
        if isinstance(issue_code, str) and issue_code:
            return issue_code
        return result.status

    def failure_ownership(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: PlanStep,
        worker_runtime_failure: bool,
    ) -> FailureOwnership:
        if self.verification_failed_before_command(step=step, result=result, issues=issues):
            return "verification"
        if any(issue.issue_type == "kernel_failure" for issue in issues):
            return "kernel"
        if worker_runtime_failure:
            return "instance"
        if any(issue.issue_type == "plan_failure" for issue in issues) or result.status == "needs_replan":
            return "plan"
        return "instance" if result.status in {"failed", "budget_exceeded"} else "kernel"

    def verification_failed_before_command(
        self,
        *,
        step: PlanStep,
        result: Result,
        issues: list[WorkerIssue],
    ) -> bool:
        if not self.is_verification_step(step):
            return False
        if result.status not in {"failed", "blocked", "budget_exceeded"}:
            return False
        if self.has_verification_command_evidence(result):
            return False

        text = self.result_issue_text(result=result, issues=issues)
        no_command_fragments = (
            "before test execution",
            "before verification",
            "could not execute verification",
            "could not be completed",
            "did not execute",
            "empty_worker_decision",
            "neither tool_calls nor final_result",
            "no verification command",
            "not executed",
            "test execution",
            "verification command",
            "verification could not",
        )
        budget_fragments = (
            "budget exhaustion",
            "budget exhausted",
            "instance budget",
            "model budget",
            "remaining_model_calls",
            "worker model call budget",
        )
        if any(fragment in text for fragment in no_command_fragments):
            return True
        if any(fragment in text for fragment in budget_fragments) and not self.has_verification_result_payload(result):
            return True
        return False

    def is_verification_step(self, step: PlanStep) -> bool:
        return step.phase == "VERIFY" or step.worker_type == "verify_worker"

    def is_verification_task(self, task: Task) -> bool:
        return task.worker_type == "verify_worker" or str(task.metadata.get("phase") or "").upper() == "VERIFY"

    def has_verification_command_evidence(self, result: Result) -> bool:
        command_tools = {"run_readonly_command", "run_focused_tests", "run_project_tests"}
        for artifact in result.artifacts:
            tool_name = artifact.metadata.get("tool_name")
            if tool_name in command_tools:
                return True
            content = artifact.content
            if isinstance(content, dict):
                if content.get("tool_name") in command_tools:
                    return True
                tool_observation = content.get("tool_observation")
                if isinstance(tool_observation, dict) and tool_observation.get("tool_name") in command_tools:
                    return True
                observation = content.get("observation")
                if isinstance(observation, dict) and content.get("tool_name") in command_tools:
                    return True
                observations = content.get("observations")
                if isinstance(observations, list):
                    for item in observations:
                        if isinstance(item, dict) and item.get("tool_name") in command_tools:
                            return True
                if self._command_payload_has_execution_evidence(content):
                    return True
                commands = content.get("commands")
                if isinstance(commands, list) and self._commands_have_execution_evidence(commands):
                    return True
        for group_result in result.metadata.get("worker_group_results") or []:
            if not isinstance(group_result, dict):
                continue
            try:
                nested = Result.model_validate(group_result)
            except Exception:
                continue
            if self.has_verification_command_evidence(nested):
                return True
        return False

    def has_failed_verification_command_evidence(self, result: Result) -> bool:
        command_tools = {"run_readonly_command", "run_focused_tests", "run_project_tests"}
        for artifact in result.artifacts:
            tool_name = artifact.metadata.get("tool_name")
            content = artifact.content
            if isinstance(content, dict):
                if tool_name in command_tools and self._payload_reports_failure(content):
                    return True
                if content.get("tool_name") in command_tools and self._payload_reports_failure(content):
                    return True
                tool_observation = content.get("tool_observation")
                if (
                    isinstance(tool_observation, dict)
                    and tool_observation.get("tool_name") in command_tools
                    and self._payload_reports_failure(tool_observation)
                ):
                    return True
                observation = content.get("observation")
                if (
                    isinstance(observation, dict)
                    and content.get("tool_name") in command_tools
                    and self._payload_reports_failure(observation)
                ):
                    return True
                observations = content.get("observations")
                if isinstance(observations, list):
                    for item in observations:
                        if (
                            isinstance(item, dict)
                            and item.get("tool_name") in command_tools
                            and self._payload_reports_failure(item)
                        ):
                            return True
                commands = content.get("commands")
                if isinstance(commands, list) and self._payload_reports_failure(commands):
                    return True
        for group_result in result.metadata.get("worker_group_results") or []:
            if not isinstance(group_result, dict):
                continue
            try:
                nested = Result.model_validate(group_result)
            except Exception:
                continue
            if self.has_failed_verification_command_evidence(nested):
                return True
        return False

    def _command_payload_has_execution_evidence(self, payload: dict[str, Any]) -> bool:
        if not payload.get("command"):
            return False
        return any(key in payload for key in ("returncode", "stdout", "stderr", "status", "passed", "failed"))

    def _commands_have_execution_evidence(self, commands: list[Any]) -> bool:
        for command in commands:
            if isinstance(command, dict) and self._command_payload_has_execution_evidence(command):
                return True
        return False

    def has_verification_result_payload(self, result: Result) -> bool:
        for artifact in result.artifacts:
            content = artifact.content
            if not isinstance(content, dict):
                continue
            if content.get("commands"):
                return True
            if content.get("returncode") is not None:
                return True
            if content.get("failed_commands"):
                return True
        return False

    def has_failed_verification_payload(self, result: Result) -> bool:
        for artifact in result.artifacts:
            if artifact.id not in {"test_results", "verification_results", "verification_result"}:
                continue
            if self._payload_reports_failure(artifact.content):
                return True
        for group_result in result.metadata.get("worker_group_results") or []:
            if not isinstance(group_result, dict):
                continue
            try:
                nested = Result.model_validate(group_result)
            except Exception:
                continue
            if self.has_failed_verification_payload(nested):
                return True
        return False

    def _payload_reports_failure(self, value: Any) -> bool:
        if isinstance(value, dict):
            status = str(value.get("status") or value.get("result") or "").lower()
            if status in {"failed", "fail", "error", "errored", "blocked"}:
                return True
            returncode = value.get("returncode")
            if isinstance(returncode, int) and returncode != 0:
                return True
            failed_commands = value.get("failed_commands")
            if isinstance(failed_commands, list) and failed_commands:
                return True
            if value.get("passed") is False:
                return True
            for key in ("scope_audit", "verification", "test_results"):
                nested = value.get(key)
                if self._payload_reports_failure(nested):
                    return True
        elif isinstance(value, list):
            return any(self._payload_reports_failure(item) for item in value)
        return False

    def result_issue_text(self, *, result: Result, issues: list[WorkerIssue]) -> str:
        parts = [
            result.summary or "",
            " ".join(result.errors or []),
            str(result.metadata.get("issue_code") or ""),
            str(result.metadata.get("recommended_action") or ""),
        ]
        for issue in issues:
            parts.extend([issue.code, issue.message, str(issue.metadata)])
        for artifact in result.artifacts:
            if artifact.id in {"test_results", "verification_results", "verification_result"}:
                parts.append(str(artifact.content))
        return " ".join(parts).lower()
