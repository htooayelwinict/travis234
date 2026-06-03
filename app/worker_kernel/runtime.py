"""Worker-kernel runtime for plan execution."""

from __future__ import annotations

from typing import Any

from app.planner.contracts import PlannerValidationError
from app.planner.validator import PlannerPlanValidator
from app.schemas import ArtifactPayload, Envelope, Plan, ReplanRequest, Result, Task, WorkerIssue
from app.worker_kernel.budget import BudgetExceeded, BudgetGate
from app.worker_kernel.compiler import MissingInputArtifacts, TaskCompiler
from app.worker_kernel.dispatcher import WorkerDispatcher
from app.worker_kernel.registry import WorkerRegistry, build_default_registry


class WorkerKernelRuntime:
    def __init__(
        self,
        registry: WorkerRegistry | None = None,
        compiler: TaskCompiler | None = None,
        validator: PlannerPlanValidator | None = None,
        planner_runtime: Any | None = None,
        allow_replan: bool = True,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._compiler = compiler or TaskCompiler()
        self._validator = validator or PlannerPlanValidator()
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
        return cls(
            registry=registry,
            planner_runtime=planner_runtime,
            allow_replan=allow_replan,
        )

    def run(self, plan: Plan, *, envelope: Envelope | None = None, _replan_depth: int = 0) -> Result:
        plan, control_plane_adjustments = self._normalize_execution_plan(plan)
        run_id = f"run_{plan.plan_id}"
        try:
            budget_gate = BudgetGate(plan.budget)
            if envelope is not None:
                self._validator.validate(envelope, plan)
            budget_gate.check_plan(plan)
        except BudgetExceeded as exc:
            return self._budget_result(run_id=run_id, exc=exc)
        except (PlannerValidationError, ValueError) as exc:
            return self._kernel_error_result(
                run_id=run_id,
                summary="Plan failed worker-kernel preflight validation.",
                issue=self._kernel_issue(code="invalid_plan", message=str(exc)),
            )

        completed_artifacts: dict[str, ArtifactPayload] = {}
        partial_artifacts: list[ArtifactPayload] = []
        failed_step_artifacts: list[ArtifactPayload] = []
        worker_results: list[Result] = []
        completed_step_ids: list[str] = []
        issues: list[WorkerIssue] = []
        instance_attempts_used = 0

        for step in plan.steps:
            try:
                task = self._compiler.compile(
                    run_id=run_id,
                    step=step,
                    artifact_store=completed_artifacts,
                    plan=plan,
                    envelope=envelope,
                )
            except MissingInputArtifacts as exc:
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
                    status="needs_replan" if self._can_replan(envelope, _replan_depth) else "blocked",
                    summary=str(exc),
                    errors=[str(exc)],
                    metadata={
                        "missing_artifacts": exc.missing_artifacts,
                        "issues": [issue.model_dump(mode="json")],
                        "recommended_action": "request a fresh plan that produces the missing artifacts first",
                    },
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
                    )
                return result.model_copy(
                    update={
                        "artifacts": list(completed_artifacts.values()),
                        "metadata": self._metadata(
                            worker_results=worker_results,
                            issues=issues,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            budget_gate=budget_gate,
                            instance_attempts_used=instance_attempts_used,
                            extra={**result.metadata, **self._control_plane_metadata(control_plane_adjustments)},
                        ),
                    }
                )

            try:
                budget_gate.before_task(task)
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
                    instance_attempts_used=instance_attempts_used,
                )

            result: Result | None = None
            attempt_number = 0
            while True:
                attempt_number += 1
                instance_attempts_used += 1
                attempt_id = f"{step.step_id}_attempt_{attempt_number}"
                attempt_task = self._with_attempt_metadata(task, attempt_id=attempt_id)

                try:
                    result = self._dispatcher.dispatch(attempt_task)
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
                    return self._budget_result(
                        run_id=run_id,
                        exc=exc,
                        artifacts=list(completed_artifacts.values()),
                        worker_results=worker_results,
                        issues=issues,
                        budget_gate=budget_gate,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        instance_attempts_used=instance_attempts_used,
                    )
                except Exception as exc:
                    if isinstance(exc, ValueError) and "Unknown worker_type" in str(exc):
                        issue = self._kernel_issue(
                            code="unknown_worker_group",
                            message=str(exc),
                            step_id=step.step_id,
                            worker_type=step.worker_type,
                        )
                        issues.append(issue)
                        return self._kernel_error_result(
                            run_id=run_id,
                            summary="Worker kernel could not resolve worker group.",
                            issue=issue,
                        )
                    issue = WorkerIssue(
                        issue_type="instance_failure",
                        code="worker_exception",
                        message=str(exc),
                        step_id=step.step_id,
                        worker_type=step.worker_type,
                        attempt_id=attempt_id,
                        retryable=True,
                    )
                    issues.append(issue)
                    if self._retry_instance_failure(budget_gate):
                        continue
                    return self._failed_instance_result(
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
                    )

                worker_results.append(result)
                result_issues = self._issues_from_result(
                    result,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                )
                issues.extend(result_issues)
                retryable_instance_failure = any(
                    issue.issue_type == "instance_failure" and issue.retryable
                    for issue in result_issues
                )
                if result.status == "failed" and retryable_instance_failure and self._retry_instance_failure(
                    budget_gate
                ):
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
                return self._kernel_error_result(
                    run_id=run_id,
                    summary=f"Step {step.step_id} did not produce a result.",
                    issue=self._kernel_issue(
                        code="missing_worker_result",
                        message=f"Step {step.step_id} did not produce a result.",
                        step_id=step.step_id,
                        worker_type=step.worker_type,
                    ),
                )

            annotated_artifacts = self._annotate_artifacts(
                result.artifacts,
                result=result,
                step_id=step.step_id,
                attempt_id=str(result.metadata.get("attempt_id") or f"{step.step_id}_attempt_{attempt_number}"),
            )

            if result.status == "completed":
                for artifact in annotated_artifacts:
                    completed_artifacts[artifact.id] = artifact
                completed_step_ids.append(step.step_id)
                continue

            if result.status == "needs_replan":
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
                )

            if result.status in ["failed", "blocked", "budget_exceeded", "kernel_error"]:
                failed_step_artifacts.extend(annotated_artifacts)
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=result.status,
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
                        extra={**result.metadata, **self._control_plane_metadata(control_plane_adjustments)},
                    ),
                )

        return Result(
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
                extra=self._control_plane_metadata(control_plane_adjustments),
            ),
        )

    def _normalize_execution_plan(self, plan: Plan) -> tuple[Plan, list[dict[str, Any]]]:
        adjustments: list[dict[str, Any]] = []
        normalized_steps = []
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

        if not adjustments:
            return plan, adjustments

        budget = dict(plan.budget)
        required_model_calls = sum(step.max_model_calls for step in normalized_steps)
        current_model_budget = int(budget.get("max_model_calls", 0) or 0)
        if current_model_budget < required_model_calls:
            adjustments.append(
                {
                    "field": "budget.max_model_calls",
                    "from": current_model_budget,
                    "to": required_model_calls,
                    "reason": "budget must cover kernel-normalized worker model-call ceilings",
                }
            )
            budget["max_model_calls"] = required_model_calls

        return plan.model_copy(update={"steps": normalized_steps, "budget": budget}), adjustments

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
    ) -> Result:
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
            return Result(
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
                    extra={"replan_request": replan_request.model_dump(mode="json")},
                ),
            )

        replacement_plan = self._planner_runtime.replan(envelope, plan, replan_request)
        replacement_result = self.run(
            replacement_plan,
            envelope=envelope,
            _replan_depth=replan_depth + 1,
        )
        metadata = dict(replacement_result.metadata)
        metadata["replan"] = {
            "request": replan_request.model_dump(mode="json"),
            "replacement_plan": replacement_plan.model_dump(mode="json"),
            "original_worker_results": [r.model_dump(mode="json") for r in worker_results],
            "original_issues": [issue.model_dump(mode="json") for issue in issues],
            "partial_artifacts": [artifact.model_dump(mode="json") for artifact in partial_artifacts],
            "failed_step_artifacts": [
                artifact.model_dump(mode="json") for artifact in failed_step_artifacts
            ],
            "depth": replan_depth + 1,
        }
        return replacement_result.model_copy(update={"metadata": metadata})

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

        return ReplanRequest(
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            failed_step_id=failed_step_id,
            reason=reason,
            worker_result=result.model_dump(mode="json"),
            completed_artifacts=list(completed_artifacts.values()),
            completed_step_ids=list(completed_step_ids),
            remaining_budget={
                "max_tool_calls": max(0, budget_gate.max_tool_calls - budget_gate.tool_calls_used),
                "max_model_calls": max(0, budget_gate.max_model_calls - budget_gate.model_calls_used),
                "max_workers": max(0, budget_gate.max_workers - budget_gate.workers_used),
                "max_retries": max(0, budget_gate.max_retries - budget_gate.retries_used),
            },
            recommended_action=self._recommended_action(result),
            issues=issues,
            partial_artifacts=partial_artifacts,
            failed_step_artifacts=failed_step_artifacts,
        )

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

    def _retry_instance_failure(self, budget_gate: BudgetGate) -> bool:
        if not budget_gate.can_retry():
            return False
        try:
            budget_gate.record_retry()
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
        }
        if extra:
            metadata.update(extra)
        return metadata

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

    def _kernel_error_result(self, *, run_id: str, summary: str, issue: WorkerIssue) -> Result:
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="kernel_error",
            summary=summary,
            errors=[issue.message],
            metadata={"issues": [issue.model_dump(mode="json")]},
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
                extra={"failed_step_id": step_id},
            ),
        )
