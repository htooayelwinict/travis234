"""Worker-kernel runtime for plan execution."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from app.planner.contracts import PlannerValidationError
from app.planner.validator import PlannerPlanValidator
from app.runtime_matrix import RuntimeMatrixLogger, attach_runtime_matrix, coerce_runtime_matrix
from app.schemas import ArtifactPayload, Envelope, Plan, ReplanRequest, Result, Task, WorkerIssue
from app.worker_kernel.artifact_store import build_artifact_store, promotable_completed_artifacts
from app.worker_kernel.budget import BudgetExceeded, BudgetGate
from app.worker_kernel.compiler import InvalidWriteScope, MissingInputArtifacts, TaskCompiler
from app.worker_kernel.control import LoopDecision, WorkerLoopController, WorkerRetryAdvisor
from app.worker_kernel.dispatcher import WorkerDispatcher
from app.worker_kernel.memory import WorkerMemoryController
from app.repair_policy import (
    VERIFICATION_FEEDBACK_REPAIR_ATTEMPTS,
    WORKER_STAGE_REPAIR_ATTEMPTS,
)
from app.worker_kernel.registry import WorkerRegistry, build_default_registry


@dataclass(frozen=True)
class VerificationRepairOutcome:
    repaired: bool
    instance_attempts_used: int


class WorkerKernelRuntime:
    def __init__(
        self,
        registry: WorkerRegistry | None = None,
        compiler: TaskCompiler | None = None,
        validator: PlannerPlanValidator | None = None,
        controller: WorkerLoopController | None = None,
        planner_runtime: Any | None = None,
        allow_replan: bool = True,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._compiler = compiler or TaskCompiler()
        self._validator = validator or PlannerPlanValidator()
        self._controller = controller or WorkerLoopController()
        self._dispatcher = WorkerDispatcher(self._registry)
        self._planner_runtime = planner_runtime
        self._allow_replan = allow_replan

    @classmethod
    def from_env(
        cls,
        dotenv_path: str = ".env",
        *,
        planner_runtime: Any | None = None,
        client_factory: Any | None = None,
        fallback_to_stub_workers: bool = True,
        root_path: str = ".",
        allow_replan: bool = True,
    ) -> "WorkerKernelRuntime":
        from app.worker_kernel.agentic import build_agentic_worker_registry
        from app.worker_kernel.env_config import build_worker_model_client, load_worker_runtime_config

        config = load_worker_runtime_config(dotenv_path)
        client_options = {"client_factory": client_factory} if client_factory is not None else {}
        model_client = build_worker_model_client(dotenv_path, **client_options)
        if model_client is None:
            if not fallback_to_stub_workers:
                raise ValueError("LLM worker runtime is not configured. Set WORKER_LLM_ENABLED=true.")
            registry = build_default_registry()
        else:
            registry = build_agentic_worker_registry(
                model_client=model_client,
                config=config,
                root_path=root_path,
            )
        controller = None
        if config.retry_advisor_enabled and model_client is not None:
            controller = WorkerLoopController(retry_advisor=WorkerRetryAdvisor(model_client))
        return cls(
            registry=registry,
            controller=controller,
            planner_runtime=planner_runtime,
            allow_replan=allow_replan,
        )

    def run(
        self,
        plan: Plan,
        *,
        envelope: Envelope | None = None,
        trace: RuntimeMatrixLogger | None = None,
        _replan_depth: int = 0,
        _initial_artifacts: list[ArtifactPayload] | None = None,
        _initial_completed_step_ids: list[str] | None = None,
        _initial_completed_mutation_step_ids: list[str] | None = None,
    ) -> Result:
        initial_artifacts = self._artifact_store(_initial_artifacts or [])
        initial_artifact_ids = set(initial_artifacts)
        initial_completed_step_ids = list(_initial_completed_step_ids or [])
        initial_completed_mutation_step_ids = list(_initial_completed_mutation_step_ids or [])
        trace = coerce_runtime_matrix(
            trace,
            plan.metadata,
            envelope.metadata if envelope is not None else None,
        )
        plan, control_plane_adjustments = self._normalize_execution_plan(plan)
        run_id = f"run_{plan.plan_id}"
        self._trace(
            trace,
            event="run_started",
            status="started",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            details={
                "planner": plan.planner,
                "step_count": len(plan.steps),
                "replan_depth": _replan_depth,
                "initial_artifact_count": len(initial_artifacts),
                "initial_completed_step_count": len(initial_completed_step_ids),
            },
        )
        if control_plane_adjustments:
            self._trace(
                trace,
                event="plan_normalized",
                status="completed",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                details={"adjustments": control_plane_adjustments},
            )
        try:
            budget_gate = BudgetGate(plan.budget)
            if envelope is not None:
                self._validator.validate(
                    envelope,
                    plan,
                    initial_artifact_ids=initial_artifact_ids,
                )
            budget_gate.check_plan(plan)
        except BudgetExceeded as exc:
            self._trace(
                trace,
                event="preflight_failed",
                status="budget_exceeded",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                details={"error": str(exc)},
            )
            return self._finalize_result(self._budget_result(run_id=run_id, exc=exc), trace)
        except (PlannerValidationError, ValueError) as exc:
            self._trace(
                trace,
                event="preflight_failed",
                status="kernel_error",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                details={"error": str(exc)},
            )
            return self._finalize_result(
                self._kernel_error_result(
                    run_id=run_id,
                    summary="Plan failed worker-kernel preflight validation.",
                    issue=self._kernel_issue(code="invalid_plan", message=str(exc)),
                ),
                trace,
            )

        self._trace(
            trace,
            event="preflight_completed",
            status="completed",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            details={"budget": dict(plan.budget)},
        )

        completed_artifacts: dict[str, ArtifactPayload] = dict(initial_artifacts)
        partial_artifacts: list[ArtifactPayload] = []
        failed_step_artifacts: list[ArtifactPayload] = []
        worker_results: list[Result] = []
        completed_step_ids: list[str] = list(initial_completed_step_ids)
        completed_mutation_step_ids: list[str] = list(initial_completed_mutation_step_ids)
        completed_mutation_contexts: list[dict[str, Any]] = []
        verification_repair_counts: dict[str, int] = {}
        issues: list[WorkerIssue] = []
        instance_attempts_used = 0
        loop_decisions: list[dict[str, Any]] = []
        memory_controller = WorkerMemoryController()

        for step in plan.steps:
            self._trace(
                trace,
                stage=step.phase or "EXECUTE",
                event="step_started",
                status="started",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=step.step_id,
                worker_type=step.worker_type,
                details={
                    "mode": step.mode,
                    "input_artifacts": list(step.input_artifacts),
                    "output_artifacts": list(step.output_artifacts),
                },
            )
            try:
                task = self._compiler.compile(
                    run_id=run_id,
                    step=step,
                    artifact_store=completed_artifacts,
                    plan=plan,
                    envelope=envelope,
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="task_compiled",
                    status="completed",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={
                        "input_count": len(task.input_artifacts),
                        "expected_outputs": list(task.expected_outputs),
                    },
                )
            except MissingInputArtifacts as exc:
                decision = self._controller.decide_after_missing_input(
                    step=step,
                    missing_artifacts=exc.missing_artifacts,
                    can_replan=self._can_replan(envelope, _replan_depth),
                )
                self._record_loop_decision(
                    trace,
                    loop_decisions=loop_decisions,
                    decision=decision,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    stage=step.phase or "EXECUTE",
                )
                issue = WorkerIssue(
                    issue_type="plan_failure",
                    code="missing_input_artifacts",
                    message=str(exc),
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    retryable=False,
                    metadata={"missing_artifacts": exc.missing_artifacts},
                )
                issues.append(issue)
                result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=decision.terminal_status or "blocked",
                    summary=str(exc),
                    errors=[str(exc)],
                    metadata={
                        "missing_artifacts": exc.missing_artifacts,
                        "issues": [issue.model_dump(mode="json")],
                        "recommended_action": "request a fresh plan that produces the missing artifacts first",
                    },
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="task_compile_failed",
                    status=result.status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"missing_artifacts": exc.missing_artifacts},
                )
                if result.status == "needs_replan":
                    return self._handle_replan(
                        envelope=envelope,
                        plan=plan,
                        run_id=run_id,
                        failed_step_id=step.step_id,
                        result=result,
                        budget_gate=budget_gate,
                        completed_artifacts=completed_artifacts,
                        completed_step_ids=completed_step_ids,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        worker_results=worker_results,
                        issues=issues,
                        instance_attempts_used=instance_attempts_used,
                        replan_depth=_replan_depth,
                        loop_decisions=loop_decisions,
                        trace=trace,
                    )
                finalized = result.model_copy(
                    update={
                        "artifacts": list(completed_artifacts.values()),
                        "metadata": self._metadata(
                            worker_results=worker_results,
                            issues=issues,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            budget_gate=budget_gate,
                            instance_attempts_used=instance_attempts_used,
                            loop_decisions=loop_decisions,
                            extra={**result.metadata, **self._control_plane_metadata(control_plane_adjustments)},
                        ),
                    }
                )
                return self._finalize_result(finalized, trace)
            except InvalidWriteScope as exc:
                decision = self._controller.decide_after_invalid_write_scope(
                    step=step,
                    message=str(exc),
                    metadata=dict(exc.metadata),
                )
                self._record_loop_decision(
                    trace,
                    loop_decisions=loop_decisions,
                    decision=decision,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    stage=step.phase or "EXECUTE",
                )
                issue = WorkerIssue(
                    issue_type="kernel_failure",
                    code="invalid_write_scope",
                    message=str(exc),
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    retryable=False,
                    metadata=dict(exc.metadata),
                )
                issues.append(issue)
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="task_compile_failed",
                    status="blocked",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"error": str(exc), "issue_code": issue.code, **dict(exc.metadata)},
                )
                terminal_result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="blocked",
                    summary=f"Execution stopped at step {step.step_id}: {exc}",
                    artifacts=list(completed_artifacts.values()),
                    errors=[str(exc)],
                    metadata=self._metadata(
                        worker_results=worker_results,
                        issues=issues,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        budget_gate=budget_gate,
                        instance_attempts_used=instance_attempts_used,
                        loop_decisions=loop_decisions,
                        extra=self._control_plane_metadata(control_plane_adjustments),
                    ),
                )
                return self._finalize_result(terminal_result, trace)

            try:
                budget_gate.before_task(task)
            except BudgetExceeded as exc:
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_budget_blocked",
                    status="budget_exceeded",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"error": str(exc)},
                )
                return self._finalize_result(
                    self._budget_result(
                        run_id=run_id,
                        exc=exc,
                        artifacts=list(completed_artifacts.values()),
                        worker_results=worker_results,
                        issues=issues,
                        budget_gate=budget_gate,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        instance_attempts_used=instance_attempts_used,
                        loop_decisions=loop_decisions,
                    ),
                    trace,
                )

            result: Result | None = None
            attempt_number = 0
            while True:
                attempt_number += 1
                instance_attempts_used += 1
                attempt_id = f"{step.step_id}_attempt_{attempt_number}"
                attempt_task = self._with_attempt_metadata(task, attempt_id=attempt_id)
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="attempt_started",
                    status="started",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                    worker_type=step.worker_type,
                    details={"attempt_number": attempt_number},
                )

                try:
                    result = self._dispatcher.dispatch(attempt_task, trace=trace)
                    result = self._with_attempt_metadata_on_result(result, attempt_id=attempt_id)
                    budget_gate.after_result(result)
                except BudgetExceeded as exc:
                    issue = WorkerIssue(
                        issue_type="instance_failure",
                        code="budget_exceeded",
                        message=str(exc),
                        step_id=step.step_id,
                        worker_type=step.worker_type,
                        attempt_id=attempt_id,
                        retryable=False,
                    )
                    issues.append(issue)
                    self._trace(
                        trace,
                        stage=step.phase or "EXECUTE",
                        event="attempt_failed",
                        status="budget_exceeded",
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        details={"error": str(exc)},
                    )
                    return self._finalize_result(
                        self._budget_result(
                            run_id=run_id,
                            exc=exc,
                            artifacts=list(completed_artifacts.values()),
                            worker_results=worker_results,
                            issues=issues,
                            budget_gate=budget_gate,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            instance_attempts_used=instance_attempts_used,
                            loop_decisions=loop_decisions,
                        ),
                        trace,
                    )
                except Exception as exc:
                    decision = self._controller.decide_after_exception(
                        step=step,
                        exc=exc,
                        retry_available=budget_gate.can_retry(step_retries_used=attempt_number - 1),
                    )
                    self._record_loop_decision(
                        trace,
                        loop_decisions=loop_decisions,
                        decision=decision,
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        stage=step.phase or "EXECUTE",
                    )
                    if decision.action == "kernel_error":
                        issue = self._kernel_issue(
                            code=decision.reason_code,
                            message=str(exc),
                            step_id=step.step_id,
                            worker_type=step.worker_type,
                        )
                        issues.append(issue)
                        self._trace(
                            trace,
                            stage=step.phase or "EXECUTE",
                            event="attempt_failed",
                            status="kernel_error",
                            request_id=plan.request_id,
                            plan_id=plan.plan_id,
                            run_id=run_id,
                            step_id=step.step_id,
                            attempt_id=attempt_id,
                            worker_type=step.worker_type,
                            details={"error": str(exc)},
                        )
                        return self._finalize_result(
                            self._kernel_error_result(
                                run_id=run_id,
                                summary="Worker kernel could not resolve worker group.",
                                issue=issue,
                                loop_decisions=loop_decisions,
                            ),
                            trace,
                        )
                    issue = WorkerIssue(
                        issue_type="instance_failure",
                        code=decision.reason_code,
                        message=str(exc),
                        step_id=step.step_id,
                        worker_type=step.worker_type,
                        attempt_id=attempt_id,
                        retryable=decision.retryable,
                    )
                    issues.append(issue)
                    memory_controller.record_exception(
                        step=step,
                        attempt_id=attempt_id,
                        exc=exc,
                        issue=issue,
                    )
                    will_retry = False
                    if decision.action == "retry_step":
                        will_retry = self._retry_instance_failure(
                            budget_gate,
                            step_retries_used=attempt_number - 1,
                        )
                        if will_retry and decision.retry_instruction is not None:
                            metadata = dict(task.metadata)
                            metadata["runtime_retry_instruction"] = decision.retry_instruction.as_prompt_text()
                            metadata["runtime_retry_reason_code"] = decision.reason_code
                            task = task.model_copy(update={"metadata": metadata})
                        if will_retry:
                            task, injected_memory = memory_controller.inject_retry_memory(task=task, step=step)
                            if injected_memory is not None:
                                self._trace(
                                    trace,
                                    stage=step.phase or "EXECUTE",
                                    event="worker_memory_injected",
                                    status="completed",
                                    request_id=plan.request_id,
                                    plan_id=plan.plan_id,
                                    run_id=run_id,
                                    step_id=step.step_id,
                                    attempt_id=attempt_id,
                                    worker_type=step.worker_type,
                                    details={
                                        "attempt_count": injected_memory.get("attempt_count"),
                                        "successful_write_count": injected_memory.get("successful_write_count"),
                                    },
                                )
                    self._trace(
                        trace,
                        stage=step.phase or "EXECUTE",
                        event="attempt_failed",
                        status="instance_failure",
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        details={"error": str(exc), "retrying": will_retry},
                    )
                    if will_retry:
                        continue
                    return self._finalize_result(
                        self._failed_instance_result(
                            run_id=run_id,
                            step_id=step.step_id,
                            summary=f"Worker instance failed at step {step.step_id}: {exc}",
                            artifacts=list(completed_artifacts.values()),
                            worker_results=worker_results,
                            issues=issues,
                            budget_gate=budget_gate,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            instance_attempts_used=instance_attempts_used,
                            loop_decisions=loop_decisions,
                        ),
                        trace,
                    )

                worker_results.append(result)
                result_issues = self._issues_from_result(
                    result,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                )
                issues.extend(result_issues)
                step_memory_attempt = memory_controller.record_attempt(
                    step=step,
                    attempt_id=attempt_id,
                    result=result,
                    issues=result_issues,
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="worker_memory_updated",
                    status="completed",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                    worker_type=step.worker_type,
                    details={
                        "successful_write_count": step_memory_attempt.get("successful_write_count"),
                        "already_done_count": step_memory_attempt.get("already_done_count"),
                        "issue_codes": step_memory_attempt.get("issue_codes"),
                    },
                )
                decision = self._controller.decide_after_attempt(
                    result=result,
                    issues=result_issues,
                    step=step,
                    retry_available=budget_gate.can_retry(step_retries_used=attempt_number - 1),
                    mutation_already_completed=bool(completed_mutation_step_ids),
                )
                self._record_loop_decision(
                    trace,
                    loop_decisions=loop_decisions,
                    decision=decision,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                    worker_type=result.producer,
                    stage=step.phase or "EXECUTE",
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="attempt_completed",
                    status=result.status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                    worker_type=result.producer,
                    details={
                        "artifact_count": len(result.artifacts),
                        "tool_calls": result.usage.get("tool_calls"),
                        "model_calls": result.usage.get("model_calls"),
                    },
                )
                verification_repair_result = self._maybe_run_verification_feedback_repair(
                    plan=plan,
                    run_id=run_id,
                    verify_step=step,
                    verify_result=result,
                    verify_attempt_id=attempt_id,
                    decision=decision,
                    completed_mutation_contexts=completed_mutation_contexts,
                    verification_repair_counts=verification_repair_counts,
                    completed_artifacts=completed_artifacts,
                    completed_step_ids=completed_step_ids,
                    completed_mutation_step_ids=completed_mutation_step_ids,
                    worker_results=worker_results,
                    issues=issues,
                    failed_step_artifacts=failed_step_artifacts,
                    budget_gate=budget_gate,
                    memory_controller=memory_controller,
                    trace=trace,
                    loop_decisions=loop_decisions,
                    instance_attempts_used=instance_attempts_used,
                    partial_artifacts=partial_artifacts,
                )
                if isinstance(verification_repair_result, VerificationRepairOutcome):
                    instance_attempts_used += verification_repair_result.instance_attempts_used
                    if verification_repair_result.repaired:
                        continue
                if isinstance(verification_repair_result, Result):
                    return self._finalize_result(verification_repair_result, trace)
                should_retry_attempt = (
                    decision.action == "retry_step"
                    and self._retry_instance_failure(
                        budget_gate,
                        step_retries_used=attempt_number - 1,
                    )
                )
                if should_retry_attempt:
                    previous_task = task
                    task, retry_adjustments = self._controller.build_retry_task(
                        task=task,
                        result=result,
                        issues=result_issues,
                        decision=decision,
                    )
                    task, injected_memory = memory_controller.inject_retry_memory(task=task, step=step)
                    self._trace(
                        trace,
                        stage=step.phase or "EXECUTE",
                        event="attempt_retry_scheduled",
                        status="retrying",
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        details={
                            "reason": "worker_runtime_failure"
                            if decision.metadata.get("worker_runtime_failure")
                            else "retryable_instance_failure",
                            "reason_code": decision.reason_code,
                            "ownership": decision.ownership,
                            "task_recompiled": task != previous_task,
                            "adjustments": retry_adjustments,
                            "memory_injected": injected_memory is not None,
                            "memory_successful_write_count": (
                                injected_memory.get("successful_write_count") if injected_memory else 0
                            ),
                        },
                    )
                    failed_step_artifacts.extend(
                        self._annotate_artifacts(
                            result.artifacts,
                            result=result,
                            step_id=step.step_id,
                            attempt_id=attempt_id,
                        )
                    )
                    continue
                break

            if result is None:
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_failed",
                    status="kernel_error",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"error": "missing worker result"},
                )
                return self._finalize_result(
                    self._kernel_error_result(
                        run_id=run_id,
                        summary=f"Step {step.step_id} did not produce a result.",
                        issue=self._kernel_issue(
                            code="missing_worker_result",
                            message=f"Step {step.step_id} did not produce a result.",
                            step_id=step.step_id,
                            worker_type=step.worker_type,
                        ),
                        loop_decisions=loop_decisions,
                    ),
                    trace,
                )

            annotated_artifacts = self._annotate_artifacts(
                result.artifacts,
                result=result,
                step_id=step.step_id,
                attempt_id=str(result.metadata.get("attempt_id") or f"{step.step_id}_attempt_{attempt_number}"),
            )

            if result.status == "completed" and decision.action in {
                "fail",
                "block",
                "budget_exceeded",
                "kernel_error",
            }:
                failed_step_artifacts.extend(annotated_artifacts)
                terminal_status = decision.terminal_status or result.status
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_terminal",
                    status=terminal_status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"summary": result.summary, "reason_code": decision.reason_code},
                )
                terminal_result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=terminal_status,
                    summary=f"Execution stopped at step {step.step_id}: {result.summary}",
                    artifacts=list(completed_artifacts.values()),
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata=self._metadata(
                        worker_results=worker_results,
                        issues=issues,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        budget_gate=budget_gate,
                        instance_attempts_used=instance_attempts_used,
                        loop_decisions=loop_decisions,
                        extra={
                            **result.metadata,
                            **self._control_plane_metadata(control_plane_adjustments),
                            "worker_memory": memory_controller.snapshot(),
                        },
                    ),
                )
                return self._finalize_result(terminal_result, trace)

            if result.status == "completed":
                promoted_artifacts = self._promotable_completed_artifacts(annotated_artifacts)
                for artifact in promoted_artifacts:
                    completed_artifacts[artifact.id] = artifact
                completed_step_ids.append(step.step_id)
                if self._is_mutation_step(step):
                    completed_mutation_step_ids.append(step.step_id)
                    completed_mutation_contexts.append(
                        {
                            "step": step,
                            "task": task,
                            "attempt_id": str(
                                result.metadata.get("attempt_id") or f"{step.step_id}_attempt_{attempt_number}"
                            ),
                        }
                    )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_completed",
                    status="completed",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"artifact_ids": [artifact.id for artifact in promoted_artifacts]},
                )
                continue

            if decision.action == "request_replan":
                partial_artifacts.extend(annotated_artifacts)
                failed_step_artifacts.extend(annotated_artifacts)
                return self._handle_replan(
                    envelope=envelope,
                    plan=plan,
                    run_id=run_id,
                    failed_step_id=step.step_id,
                    result=result,
                    budget_gate=budget_gate,
                    completed_artifacts=completed_artifacts,
                    completed_step_ids=completed_step_ids,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    worker_results=worker_results,
                    issues=issues,
                    instance_attempts_used=instance_attempts_used,
                    replan_depth=_replan_depth,
                    loop_decisions=loop_decisions,
                    trace=trace,
                )

            if decision.action in {"fail", "block", "budget_exceeded", "kernel_error"} or result.status in [
                "failed",
                "blocked",
                "budget_exceeded",
                "kernel_error",
                "needs_replan",
            ]:
                failed_step_artifacts.extend(annotated_artifacts)
                terminal_status = decision.terminal_status or result.status
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_terminal",
                    status=terminal_status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"summary": result.summary},
                )
                terminal_result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=terminal_status,
                    summary=f"Execution stopped at step {step.step_id}: {result.summary}",
                    artifacts=list(completed_artifacts.values()),
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata=self._metadata(
                        worker_results=worker_results,
                        issues=issues,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        budget_gate=budget_gate,
                        instance_attempts_used=instance_attempts_used,
                        loop_decisions=loop_decisions,
                        extra={
                            **result.metadata,
                            **self._control_plane_metadata(control_plane_adjustments),
                            "worker_memory": memory_controller.snapshot(),
                        },
                    ),
                )
                return self._finalize_result(terminal_result, trace)

        self._trace(
            trace,
            event="run_completed",
            status="completed",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            details={
                "completed_steps": list(completed_step_ids),
                "artifact_count": len(completed_artifacts),
            },
        )
        completed_result = Result(
            run_id=run_id,
            producer="worker_kernel",
            status="completed",
            summary="Plan executed successfully.",
            artifacts=list(completed_artifacts.values()),
            usage={
                "tool_calls": budget_gate.tool_calls_used,
                "model_calls": budget_gate.model_calls_used,
                "workers": budget_gate.workers_used,
                "retries": budget_gate.retries_used,
                "instance_attempts": instance_attempts_used,
            },
            metadata=self._metadata(
                worker_results=worker_results,
                issues=issues,
                partial_artifacts=partial_artifacts,
                failed_step_artifacts=failed_step_artifacts,
                budget_gate=budget_gate,
                instance_attempts_used=instance_attempts_used,
                loop_decisions=loop_decisions,
                extra={
                    **self._control_plane_metadata(control_plane_adjustments),
                    "worker_memory": memory_controller.snapshot(),
                },
            ),
        )
        return self._finalize_result(completed_result, trace)

    def _maybe_run_verification_feedback_repair(
        self,
        *,
        plan: Plan,
        run_id: str,
        verify_step: Any,
        verify_result: Result,
        verify_attempt_id: str,
        decision: LoopDecision,
        completed_mutation_contexts: list[dict[str, Any]],
        verification_repair_counts: dict[str, int],
        completed_artifacts: dict[str, ArtifactPayload],
        completed_step_ids: list[str],
        completed_mutation_step_ids: list[str],
        worker_results: list[Result],
        issues: list[WorkerIssue],
        failed_step_artifacts: list[ArtifactPayload],
        budget_gate: BudgetGate,
        memory_controller: WorkerMemoryController,
        trace: RuntimeMatrixLogger,
        loop_decisions: list[dict[str, Any]],
        instance_attempts_used: int,
        partial_artifacts: list[ArtifactPayload],
    ) -> VerificationRepairOutcome | Result | None:
        if not self._controller.is_verification_step(verify_step):
            return None
        if not completed_mutation_contexts:
            return None
        if not self._verification_feedback_is_implementation_repair(
            verify_step=verify_step,
            verify_result=verify_result,
            decision=decision,
        ):
            return None

        feedback = self._verification_feedback_payload(
            verify_step=verify_step,
            verify_result=verify_result,
            verify_attempt_id=verify_attempt_id,
        )
        mutation_context = completed_mutation_contexts[-1]
        mutation_step = mutation_context["step"]
        base_repair_task = mutation_context["task"]
        repair_attempts_used = 0
        retry_instruction = (
            "This is a targeted mutation repair after verification failed. "
            "Use verification_feedback as the primary failure evidence, keep the "
            "same write policy, change only what is needed, then return all expected artifacts."
        )
        retry_reason_code = "verification_feedback_repair"

        while True:
            repair_count = verification_repair_counts.get(verify_step.step_id, 0)
            if repair_count >= VERIFICATION_FEEDBACK_REPAIR_ATTEMPTS:
                return None
            if not self._retry_instance_failure(budget_gate, step_retries_used=repair_count):
                return None

            repair_number = repair_count + 1
            verification_repair_counts[verify_step.step_id] = repair_number
            repair_attempts_used += 1
            repair_attempt_id = f"{mutation_step.step_id}_verification_repair_{repair_number}"
            metadata = dict(base_repair_task.metadata)
            metadata["verification_feedback"] = feedback
            metadata["runtime_retry_instruction"] = retry_instruction
            metadata["runtime_retry_reason_code"] = retry_reason_code
            repair_task = base_repair_task.model_copy(
                update={
                    "metadata": metadata,
                    "max_tool_calls": max(base_repair_task.max_tool_calls, 2),
                    "max_model_calls": max(base_repair_task.max_model_calls, 2),
                }
            )
            repair_task, injected_memory = memory_controller.inject_retry_memory(
                task=repair_task,
                step=mutation_step,
            )
            repair_task = self._with_attempt_metadata(repair_task, attempt_id=repair_attempt_id)

            self._trace(
                trace,
                stage=mutation_step.phase or "MUTATE",
                event="verification_feedback_repair_started",
                status="started",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=mutation_step.step_id,
                attempt_id=repair_attempt_id,
                worker_type=mutation_step.worker_type,
                details={
                    "verify_step_id": verify_step.step_id,
                    "verify_attempt_id": verify_attempt_id,
                    "repair_attempt": repair_number,
                    "max_repair_attempts": VERIFICATION_FEEDBACK_REPAIR_ATTEMPTS,
                    "memory_injected": injected_memory is not None,
                },
            )
            try:
                repair_result = self._dispatcher.dispatch(repair_task, trace=trace)
                repair_result = self._with_attempt_metadata_on_result(repair_result, attempt_id=repair_attempt_id)
                budget_gate.after_result(repair_result)
            except BudgetExceeded as exc:
                return self._budget_result(
                    run_id=run_id,
                    exc=exc,
                    artifacts=list(completed_artifacts.values()),
                    worker_results=worker_results,
                    issues=issues,
                    budget_gate=budget_gate,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    instance_attempts_used=instance_attempts_used + repair_attempts_used,
                    loop_decisions=loop_decisions,
                )
            except Exception as exc:
                issue = WorkerIssue(
                    issue_type="instance_failure",
                    code="verification_feedback_repair_exception",
                    message=str(exc),
                    step_id=mutation_step.step_id,
                    worker_type=mutation_step.worker_type,
                    attempt_id=repair_attempt_id,
                    retryable=False,
                )
                issues.append(issue)
                return self._failed_instance_result(
                    run_id=run_id,
                    step_id=mutation_step.step_id,
                    summary=f"Verification feedback mutation repair failed: {exc}",
                    artifacts=list(completed_artifacts.values()),
                    worker_results=worker_results,
                    issues=issues,
                    budget_gate=budget_gate,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    instance_attempts_used=instance_attempts_used + repair_attempts_used,
                    loop_decisions=loop_decisions,
                )

            worker_results.append(repair_result)
            repair_issues = self._issues_from_result(
                repair_result,
                step_id=mutation_step.step_id,
                attempt_id=repair_attempt_id,
            )
            issues.extend(repair_issues)
            memory_controller.record_attempt(
                step=mutation_step,
                attempt_id=repair_attempt_id,
                result=repair_result,
                issues=repair_issues,
            )
            annotated_artifacts = self._annotate_artifacts(
                repair_result.artifacts,
                result=repair_result,
                step_id=mutation_step.step_id,
                attempt_id=repair_attempt_id,
            )
            if repair_result.status == "completed":
                for artifact in self._promotable_completed_artifacts(annotated_artifacts):
                    completed_artifacts[artifact.id] = artifact
                if mutation_step.step_id not in completed_step_ids:
                    completed_step_ids.append(mutation_step.step_id)
                if mutation_step.step_id not in completed_mutation_step_ids:
                    completed_mutation_step_ids.append(mutation_step.step_id)
                self._trace(
                    trace,
                    stage=mutation_step.phase or "MUTATE",
                    event="verification_feedback_repair_completed",
                    status="completed",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=mutation_step.step_id,
                    attempt_id=repair_attempt_id,
                    worker_type=mutation_step.worker_type,
                    details={
                        "verify_step_id": verify_step.step_id,
                        "artifact_ids": [artifact.id for artifact in annotated_artifacts],
                    },
                )
                return VerificationRepairOutcome(repaired=True, instance_attempts_used=repair_attempts_used)

            failed_step_artifacts.extend(annotated_artifacts)
            repair_decision = self._controller.decide_after_attempt(
                result=repair_result,
                issues=repair_issues,
                step=mutation_step,
                retry_available=(
                    repair_number < VERIFICATION_FEEDBACK_REPAIR_ATTEMPTS
                    and budget_gate.can_retry(step_retries_used=repair_number)
                ),
                mutation_already_completed=bool(completed_mutation_step_ids),
            )
            self._record_loop_decision(
                trace,
                loop_decisions=loop_decisions,
                decision=repair_decision,
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=mutation_step.step_id,
                attempt_id=repair_attempt_id,
                worker_type=mutation_step.worker_type,
                stage=mutation_step.phase or "MUTATE",
            )
            retrying = repair_decision.action == "retry_step" and repair_decision.ownership != "plan"
            self._trace(
                trace,
                stage=mutation_step.phase or "MUTATE",
                event="verification_feedback_repair_failed",
                status=repair_result.status,
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=mutation_step.step_id,
                attempt_id=repair_attempt_id,
                worker_type=mutation_step.worker_type,
                details={
                    "summary": repair_result.summary,
                    "retrying": retrying,
                    "next_repair_attempt": repair_number + 1 if retrying else None,
                },
            )
            if retrying:
                if repair_decision.retry_instruction is not None:
                    retry_instruction = repair_decision.retry_instruction.as_prompt_text()
                    retry_reason_code = repair_decision.reason_code
                continue

            return Result(
                run_id=run_id,
                producer="worker_kernel",
                status=repair_result.status,
                summary=(
                    f"Verification feedback repair failed at step {mutation_step.step_id}: "
                    f"{repair_result.summary}"
                ),
                artifacts=list(completed_artifacts.values()),
                errors=repair_result.errors,
                warnings=repair_result.warnings,
                metadata=self._metadata(
                    worker_results=worker_results,
                    issues=issues,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    budget_gate=budget_gate,
                    instance_attempts_used=instance_attempts_used + repair_attempts_used,
                    loop_decisions=loop_decisions,
                    extra={
                        **repair_result.metadata,
                        "verification_feedback_repair_failed": True,
                        "worker_memory": memory_controller.snapshot(),
                    },
                ),
            )

    def _verification_feedback_is_implementation_repair(
        self,
        *,
        verify_step: Any,
        verify_result: Result,
        decision: LoopDecision,
    ) -> bool:
        if verify_result.status == "needs_replan" or decision.action == "request_replan":
            return False
        if decision.ownership == "plan":
            return False
        if verify_result.status == "completed":
            return self._controller.has_failed_verification_payload(verify_result)
        if verify_result.status == "failed":
            return self._controller.has_verification_command_evidence(verify_result)
        return False

    def _verification_feedback_payload(
        self,
        *,
        verify_step: Any,
        verify_result: Result,
        verify_attempt_id: str,
    ) -> dict[str, Any]:
        return {
            "verify_step_id": verify_step.step_id,
            "verify_attempt_id": verify_attempt_id,
            "status": verify_result.status,
            "summary": verify_result.summary,
            "errors": list(verify_result.errors),
            "warnings": list(verify_result.warnings),
            "failure_observation": self._failure_observation(verify_result),
            "artifact_ids": [artifact.id for artifact in verify_result.artifacts],
            "instruction": (
                "Repair the implementation-level cause of this verification failure. "
                "Do not ask for replan unless the evidence proves user intent or plan ordering is wrong."
            ),
        }

    def _is_mutation_step(self, step: Any) -> bool:
        return step.phase == "MUTATE" or bool(step.permissions.write_files)

    def _promotable_completed_artifacts(self, artifacts: list[ArtifactPayload]) -> list[ArtifactPayload]:
        return promotable_completed_artifacts(artifacts)

    def _normalize_execution_plan(self, plan: Plan) -> tuple[Plan, list[dict[str, Any]]]:
        adjustments: list[dict[str, Any]] = []
        normalized_steps = []
        budget = dict(plan.budget)
        current_retry_budget = int(budget.get("max_retries", 0) or 0)
        if current_retry_budget < WORKER_STAGE_REPAIR_ATTEMPTS:
            adjustments.append(
                {
                    "field": "budget.max_retries",
                    "from": current_retry_budget,
                    "to": WORKER_STAGE_REPAIR_ATTEMPTS,
                    "reason": "worker runtime retries are capped per stage, with three repair attempts per stage",
                }
            )
            budget["max_retries"] = WORKER_STAGE_REPAIR_ATTEMPTS
        for step in plan.steps:
            minimum_model_calls = self._minimum_model_calls_for_step(step)
            if step.max_model_calls < minimum_model_calls:
                adjustments.append(
                    {
                        "step_id": step.step_id,
                        "worker_type": step.worker_type,
                        "field": "max_model_calls",
                        "from": step.max_model_calls,
                        "to": minimum_model_calls,
                        "reason": (
                            "agentic tool workers need a model action turn and a "
                            "post-observation final-result turn"
                        ),
                    }
                )
                step = step.model_copy(update={"max_model_calls": minimum_model_calls})
            normalized_steps.append(step)

        retry_limit = int(budget.get("max_retries", 0) or 0)
        required_tool_calls = sum(
            self._retry_envelope_call_budget(step.max_tool_calls, retry_limit, kind="tool")
            for step in normalized_steps
        )
        required_model_calls = sum(
            self._retry_envelope_call_budget(step.max_model_calls, retry_limit, kind="model")
            for step in normalized_steps
        )
        current_tool_budget = int(budget.get("max_tool_calls", 0) or 0)
        if current_tool_budget < required_tool_calls:
            adjustments.append(
                {
                    "field": "budget.max_tool_calls",
                    "from": current_tool_budget,
                    "to": required_tool_calls,
                    "reason": "budget must cover kernel-owned per-stage retry tool-call envelope",
                }
            )
            budget["max_tool_calls"] = required_tool_calls

        current_model_budget = int(budget.get("max_model_calls", 0) or 0)
        if current_model_budget < required_model_calls:
            adjustments.append(
                {
                    "field": "budget.max_model_calls",
                    "from": current_model_budget,
                    "to": required_model_calls,
                    "reason": "budget must cover kernel-owned per-stage retry model-call envelope",
                }
            )
            budget["max_model_calls"] = required_model_calls

        return plan.model_copy(update={"steps": normalized_steps, "budget": budget}), adjustments

    def _retry_envelope_call_budget(self, initial_limit: int, retry_limit: int, *, kind: str) -> int:
        if initial_limit <= 0:
            return 0

        total = initial_limit
        attempt_limit = initial_limit
        for _ in range(max(0, retry_limit)):
            if kind == "tool":
                attempt_limit = max(attempt_limit + 2, attempt_limit * 2, 2)
            else:
                attempt_limit = max(attempt_limit + 1, attempt_limit * 2, 2)
            total += attempt_limit
        return total

    def _minimum_model_calls_for_step(self, step: Any) -> int:
        try:
            group = self._registry.get(step.worker_type)
        except ValueError:
            return step.max_model_calls

        minimum_model_calls = getattr(group, "minimum_model_calls", None)
        if callable(minimum_model_calls):
            return int(minimum_model_calls(step))
        if step.max_tool_calls > 0 and self._step_uses_tools(step):
            return 2
        return step.max_model_calls

    def _step_uses_tools(self, step: Any) -> bool:
        permissions = step.permissions
        return any(
            [
                permissions.read_files,
                permissions.write_files,
                permissions.run_commands,
                permissions.web_research,
            ]
        )

    def _control_plane_metadata(self, adjustments: list[dict[str, Any]]) -> dict[str, Any]:
        if not adjustments:
            return {}
        return {"control_plane_adjustments": adjustments}

    def _handle_replan(
        self,
        *,
        envelope: Envelope | None,
        plan: Plan,
        run_id: str,
        failed_step_id: str,
        result: Result,
        budget_gate: BudgetGate,
        completed_artifacts: dict[str, ArtifactPayload],
        completed_step_ids: list[str],
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
        worker_results: list[Result],
        issues: list[WorkerIssue],
        instance_attempts_used: int,
        replan_depth: int,
        loop_decisions: list[dict[str, Any]],
        trace: RuntimeMatrixLogger,
    ) -> Result:
        self._trace(
            trace,
            event="replan_requested",
            status="needs_replan",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            step_id=failed_step_id,
            details={"reason": result.summary},
        )
        replan_request = self._build_replan_request(
            plan=plan,
            run_id=run_id,
            failed_step_id=failed_step_id,
            result=result,
            completed_artifacts=completed_artifacts,
            completed_step_ids=completed_step_ids,
            budget_gate=budget_gate,
            issues=issues,
            partial_artifacts=partial_artifacts,
            failed_step_artifacts=failed_step_artifacts,
        )
        if not self._can_replan(envelope, replan_depth):
            self._trace(
                trace,
                event="replan_deferred",
                status="needs_replan",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=failed_step_id,
            )
            deferred_result = Result(
                run_id=run_id,
                producer="worker_kernel",
                status="needs_replan",
                summary=f"Execution stopped at step {failed_step_id}: {result.summary}",
                artifacts=list(completed_artifacts.values()),
                errors=result.errors,
                warnings=result.warnings,
                metadata=self._metadata(
                    worker_results=worker_results,
                    issues=issues,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    budget_gate=budget_gate,
                    instance_attempts_used=instance_attempts_used,
                    loop_decisions=loop_decisions,
                    extra={"replan_request": replan_request.model_dump(mode="json")},
                ),
            )
            return self._finalize_result(deferred_result, trace)

        self._trace(
            trace,
            event="replan_started",
            status="started",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            step_id=failed_step_id,
        )
        replacement_plan = self._planner_replan(
            envelope=envelope,
            current_plan=plan,
            replan_request=replan_request,
            trace=trace,
        )
        carryover_artifacts = list(completed_artifacts.values())
        replacement_result = self.run(
            replacement_plan,
            envelope=envelope,
            trace=trace,
            _replan_depth=replan_depth + 1,
            _initial_artifacts=carryover_artifacts,
            _initial_completed_step_ids=list(completed_step_ids),
            _initial_completed_mutation_step_ids=self._completed_mutation_step_ids(
                plan=plan,
                completed_step_ids=completed_step_ids,
            ),
        )
        metadata = dict(replacement_result.metadata)
        metadata["replan"] = {
            "request": replan_request.model_dump(mode="json"),
            "replacement_plan": replacement_plan.model_dump(mode="json"),
            "carryover_artifacts": [artifact.model_dump(mode="json") for artifact in carryover_artifacts],
            "original_worker_results": [r.model_dump(mode="json") for r in worker_results],
            "original_issues": [issue.model_dump(mode="json") for issue in issues],
            "partial_artifacts": [artifact.model_dump(mode="json") for artifact in partial_artifacts],
            "failed_step_artifacts": [
                artifact.model_dump(mode="json") for artifact in failed_step_artifacts
            ],
            "original_loop_decisions": list(loop_decisions),
            "depth": replan_depth + 1,
        }
        self._trace(
            trace,
            event="replan_completed",
            status=replacement_result.status,
            request_id=plan.request_id,
            plan_id=replacement_plan.plan_id,
            run_id=run_id,
            step_id=failed_step_id,
        )
        finalized = replacement_result.model_copy(
            update={"metadata": attach_runtime_matrix(metadata, trace)}
        )
        return self._finalize_result(finalized, trace)

    def _build_replan_request(
        self,
        *,
        plan: Plan,
        run_id: str,
        failed_step_id: str,
        result: Result,
        completed_artifacts: dict[str, ArtifactPayload],
        completed_step_ids: list[str],
        budget_gate: BudgetGate,
        issues: list[WorkerIssue],
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
    ) -> ReplanRequest:
        reason = result.summary or "worker requested replan"
        if result.errors:
            reason = f"{reason}: {'; '.join(result.errors)}"

        completed = list(completed_artifacts.values())
        return ReplanRequest(
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            failed_step_id=failed_step_id,
            reason=reason,
            worker_result=result.model_dump(mode="json"),
            completed_artifacts=completed,
            carryover_artifacts=completed,
            completed_step_ids=list(completed_step_ids),
            remaining_budget={
                "max_tool_calls": max(0, budget_gate.max_tool_calls - budget_gate.tool_calls_used),
                "max_model_calls": max(0, budget_gate.max_model_calls - budget_gate.model_calls_used),
                "max_workers": max(0, budget_gate.max_workers - budget_gate.workers_used),
                "max_retries": budget_gate.max_retries,
                "max_retries_per_stage": budget_gate.max_retries,
                "retry_count_used": budget_gate.retries_used,
            },
            recommended_action=self._recommended_action(result),
            issues=issues,
            partial_artifacts=partial_artifacts,
            failed_step_artifacts=failed_step_artifacts,
            failed_step=self._failed_step_payload(plan=plan, failed_step_id=failed_step_id),
            failure_observation=self._failure_observation(result),
        )

    def _artifact_store(self, artifacts: list[ArtifactPayload]) -> dict[str, ArtifactPayload]:
        return build_artifact_store(artifacts)

    def _completed_mutation_step_ids(self, *, plan: Plan, completed_step_ids: list[str]) -> list[str]:
        completed = set(completed_step_ids)
        return [step.step_id for step in plan.steps if step.step_id in completed and self._is_mutation_step(step)]

    def _failed_step_payload(self, *, plan: Plan, failed_step_id: str) -> dict[str, Any]:
        for step in plan.steps:
            if step.step_id == failed_step_id:
                return step.model_dump(mode="json")
        return {}

    def _failure_observation(self, result: Result) -> dict[str, Any]:
        observations = []
        for artifact in result.artifacts:
            content = artifact.content if isinstance(artifact.content, dict) else {}
            if artifact.kind != "tool_observation" and not content.get("tool_name"):
                continue
            observation = content.get("observation") if isinstance(content.get("observation"), dict) else {}
            observations.append(
                {
                    "artifact_id": artifact.id,
                    "tool_name": content.get("tool_name"),
                    "command": observation.get("command"),
                    "returncode": observation.get("returncode"),
                    "stdout_tail": str(observation.get("stdout") or "")[-2000:],
                    "stderr_tail": str(observation.get("stderr") or "")[-2000:],
                }
            )
        return {
            "status": result.status,
            "summary": result.summary,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
            "usage": dict(result.usage),
            "artifact_ids": [artifact.id for artifact in result.artifacts],
            "tool_observations": observations,
            "issue_codes": [
                issue.get("code")
                for issue in result.metadata.get("issues", [])
                if isinstance(issue, dict) and issue.get("code")
            ],
            "expected_artifacts": result.metadata.get("expected_artifacts") or [],
            "produced_artifacts": result.metadata.get("produced_artifacts")
            or [artifact.id for artifact in result.artifacts],
            "missing_artifacts": result.metadata.get("missing_artifacts") or [],
            "artifact_contract": result.metadata.get("artifact_contract") or [],
            "worker_group_results": self._compact_worker_group_results(
                result.metadata.get("worker_group_results") or []
            ),
        }

    def _compact_worker_group_results(self, worker_group_results: Any) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        if not isinstance(worker_group_results, list):
            return compact
        for item in worker_group_results:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            compact.append(
                {
                    "status": item.get("status"),
                    "summary": item.get("summary"),
                    "producer": item.get("producer"),
                    "usage": item.get("usage"),
                    "artifact_ids": [
                        artifact.get("id")
                        for artifact in item.get("artifacts", [])
                        if isinstance(artifact, dict)
                    ],
                    "issue_codes": [
                        issue.get("code")
                        for issue in metadata.get("issues", [])
                        if isinstance(issue, dict) and issue.get("code")
                    ],
                }
            )
        return compact

    def _recommended_action(self, result: Result) -> str | None:
        value = (result.metadata or {}).get("recommended_action")
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _can_replan(self, envelope: Envelope | None, replan_depth: int) -> bool:
        return (
            self._allow_replan
            and self._planner_runtime is not None
            and envelope is not None
            and replan_depth < 1
        )

    def _retry_instance_failure(self, budget_gate: BudgetGate, *, step_retries_used: int) -> bool:
        if not budget_gate.can_retry(step_retries_used=step_retries_used):
            return False
        try:
            budget_gate.record_retry(step_retries_used=step_retries_used)
        except BudgetExceeded:
            return False
        return True

    def _with_attempt_metadata(self, task: Task, *, attempt_id: str) -> Task:
        metadata = dict(task.metadata)
        metadata["attempt_id"] = attempt_id
        return task.model_copy(update={"metadata": metadata})

    def _with_attempt_metadata_on_result(self, result: Result, *, attempt_id: str) -> Result:
        metadata = dict(result.metadata)
        metadata.setdefault("attempt_id", attempt_id)
        return result.model_copy(update={"metadata": metadata})

    def _annotate_artifacts(
        self,
        artifacts: list[ArtifactPayload],
        *,
        result: Result,
        step_id: str,
        attempt_id: str,
    ) -> list[ArtifactPayload]:
        annotated = []
        for artifact in artifacts:
            updates = {}
            if artifact.producer is None:
                updates["producer"] = result.producer
            if artifact.step_id is None:
                updates["step_id"] = step_id
            if artifact.attempt_id is None:
                updates["attempt_id"] = attempt_id
            annotated.append(artifact.model_copy(update=updates))
        return annotated

    def _issues_from_result(self, result: Result, *, step_id: str, attempt_id: str) -> list[WorkerIssue]:
        raw_issues = result.metadata.get("issues", [])
        issues: list[WorkerIssue] = []
        if isinstance(raw_issues, list):
            for raw_issue in raw_issues:
                if isinstance(raw_issue, WorkerIssue):
                    issues.append(raw_issue)
                elif isinstance(raw_issue, dict):
                    issues.append(WorkerIssue.model_validate(raw_issue))

        issue_type = result.metadata.get("issue_type")
        if isinstance(issue_type, str):
            issues.append(
                WorkerIssue(
                    issue_type=issue_type,
                    code=str(result.metadata.get("issue_code") or result.status),
                    message=result.summary,
                    step_id=step_id,
                    worker_type=result.producer,
                    attempt_id=attempt_id,
                    retryable=bool(result.metadata.get("retryable", False)),
                    metadata=dict(result.metadata),
                )
            )
        return issues

    def _metadata(
        self,
        *,
        worker_results: list[Result],
        issues: list[WorkerIssue],
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
        budget_gate: BudgetGate,
        instance_attempts_used: int,
        loop_decisions: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "worker_results": [r.model_dump(mode="json") for r in worker_results],
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "partial_artifacts": [artifact.model_dump(mode="json") for artifact in partial_artifacts],
            "failed_step_artifacts": [
                artifact.model_dump(mode="json") for artifact in failed_step_artifacts
            ],
            "retry_count": budget_gate.retries_used,
            "instance_attempts_used": instance_attempts_used,
            "loop_decisions": list(loop_decisions or []),
            "artifact_quality": self._aggregate_artifact_quality(worker_results),
        }
        if extra:
            metadata.update(extra)
        return metadata

    def _aggregate_artifact_quality(self, worker_results: list[Result]) -> dict[str, Any]:
        aggregate: dict[str, Any] = {
            "expected_count": 0,
            "missing_count": 0,
            "empty_count": 0,
            "invalid_count": 0,
            "synthesized_count": 0,
            "missing_artifacts": [],
            "empty_artifacts": [],
            "invalid_artifacts": [],
            "synthesized_artifacts": [],
            "steps": [],
        }
        for result in worker_results:
            quality = result.metadata.get("artifact_quality")
            if not isinstance(quality, dict):
                continue
            aggregate["expected_count"] += int(quality.get("expected_count", 0) or 0)
            aggregate["missing_count"] += int(quality.get("missing_count", 0) or 0)
            aggregate["empty_count"] += int(quality.get("empty_count", 0) or 0)
            aggregate["invalid_count"] += int(quality.get("invalid_count", 0) or 0)
            aggregate["synthesized_count"] += int(quality.get("synthesized_count", 0) or 0)
            for key in ("missing_artifacts", "empty_artifacts", "invalid_artifacts", "synthesized_artifacts"):
                values = quality.get(key)
                if isinstance(values, list):
                    aggregate[key].extend(value if isinstance(value, dict) else str(value) for value in values)
            aggregate["steps"].append(
                {
                    "producer": result.producer,
                    "status": result.status,
                    **quality,
                }
            )
        return aggregate

    def _planner_replan(
        self,
        *,
        envelope: Envelope,
        current_plan: Plan,
        replan_request: ReplanRequest,
        trace: RuntimeMatrixLogger,
    ) -> Plan:
        replan_method = self._planner_runtime.replan
        try:
            signature = inspect.signature(replan_method)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and "trace" in signature.parameters:
            return replan_method(envelope, current_plan, replan_request, trace=trace)
        return replan_method(envelope, current_plan, replan_request)

    def _record_loop_decision(
        self,
        trace: RuntimeMatrixLogger,
        *,
        loop_decisions: list[dict[str, Any]],
        decision: LoopDecision,
        request_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        worker_type: str | None = None,
        stage: str | None = "plan_execution",
    ) -> None:
        payload = decision.model_dump(mode="json", exclude_none=True)
        loop_decisions.append(payload)
        self._trace(
            trace,
            stage=stage,
            event="loop_decision",
            status=decision.action,
            request_id=request_id,
            plan_id=plan_id,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            worker_type=worker_type,
            details=payload,
        )

    def _trace(
        self,
        trace: RuntimeMatrixLogger,
        *,
        event: str,
        status: str,
        stage: str | None = "plan_execution",
        request_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        worker_type: str | None = None,
        elapsed_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        trace.record(
            component="worker_kernel_runtime",
            stage=stage,
            event=event,
            status=status,
            request_id=request_id,
            plan_id=plan_id,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            worker_type=worker_type,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    def _finalize_result(self, result: Result, trace: RuntimeMatrixLogger) -> Result:
        metadata = attach_runtime_matrix(result.metadata, trace)
        return result.model_copy(update={"metadata": metadata})

    def _budget_result(
        self,
        *,
        run_id: str,
        exc: Exception,
        artifacts: list[ArtifactPayload] | None = None,
        worker_results: list[Result] | None = None,
        issues: list[WorkerIssue] | None = None,
        budget_gate: BudgetGate | None = None,
        partial_artifacts: list[ArtifactPayload] | None = None,
        failed_step_artifacts: list[ArtifactPayload] | None = None,
        instance_attempts_used: int = 0,
        loop_decisions: list[dict[str, Any]] | None = None,
    ) -> Result:
        metadata: dict[str, Any] = {}
        if budget_gate is not None:
            metadata = self._metadata(
                worker_results=worker_results or [],
                issues=issues or [],
                partial_artifacts=partial_artifacts or [],
                failed_step_artifacts=failed_step_artifacts or [],
                budget_gate=budget_gate,
                instance_attempts_used=instance_attempts_used,
                loop_decisions=loop_decisions,
            )
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="budget_exceeded",
            summary=str(exc),
            artifacts=artifacts or [],
            errors=[str(exc)],
            metadata=metadata,
        )

    def _kernel_error_result(
        self,
        *,
        run_id: str,
        summary: str,
        issue: WorkerIssue,
        loop_decisions: list[dict[str, Any]] | None = None,
    ) -> Result:
        metadata = {"issues": [issue.model_dump(mode="json")]}
        if loop_decisions is not None:
            metadata["loop_decisions"] = list(loop_decisions)
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="kernel_error",
            summary=summary,
            errors=[issue.message],
            metadata=metadata,
        )

    def _kernel_issue(
        self,
        *,
        code: str,
        message: str,
        step_id: str | None = None,
        worker_type: str | None = None,
    ) -> WorkerIssue:
        return WorkerIssue(
            issue_type="kernel_failure",
            code=code,
            message=message,
            step_id=step_id,
            worker_type=worker_type,
            retryable=False,
        )

    def _failed_instance_result(
        self,
        *,
        run_id: str,
        step_id: str,
        summary: str,
        artifacts: list[ArtifactPayload],
        worker_results: list[Result],
        issues: list[WorkerIssue],
        budget_gate: BudgetGate,
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
        instance_attempts_used: int,
        loop_decisions: list[dict[str, Any]] | None = None,
    ) -> Result:
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="failed",
            summary=summary,
            artifacts=artifacts,
            errors=[summary],
            metadata=self._metadata(
                worker_results=worker_results,
                issues=issues,
                partial_artifacts=partial_artifacts,
                failed_step_artifacts=failed_step_artifacts,
                budget_gate=budget_gate,
                instance_attempts_used=instance_attempts_used,
                loop_decisions=loop_decisions,
                extra={"failed_step_id": step_id},
            ),
        )
