"""Worker-kernel runtime for plan execution."""

from __future__ import annotations

from typing import Any

from app.schemas import Envelope, Plan, ReplanRequest, Result
from app.worker_kernel.budget import BudgetExceeded, BudgetGate
from app.worker_kernel.compiler import TaskCompiler
from app.worker_kernel.dispatcher import WorkerDispatcher
from app.worker_kernel.registry import WorkerRegistry, build_default_registry


class WorkerKernelRuntime:
    def __init__(
        self,
        registry: WorkerRegistry | None = None,
        compiler: TaskCompiler | None = None,
        planner_runtime: Any | None = None,
        allow_replan: bool = True,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._compiler = compiler or TaskCompiler()
        self._dispatcher = WorkerDispatcher(self._registry)
        self._planner_runtime = planner_runtime
        self._allow_replan = allow_replan

    def run(self, plan: Plan, *, envelope: Envelope | None = None, _replan_depth: int = 0) -> Result:
        run_id = f"run_{plan.plan_id}"
        budget_gate = BudgetGate(plan.budget)

        try:
            budget_gate.check_plan(plan)
        except BudgetExceeded as exc:
            return Result(
                run_id=run_id,
                producer="worker_kernel",
                status="budget_exceeded",
                summary=str(exc),
                errors=[str(exc)],
            )

        artifacts: dict[str, dict] = {}
        worker_results: list[Result] = []
        completed_step_ids: list[str] = []

        for step in plan.steps:
            task = self._compiler.compile(
                run_id=run_id,
                step=step,
                artifact_store=artifacts,
            )

            try:
                budget_gate.before_task(task)
            except BudgetExceeded as exc:
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="budget_exceeded",
                    summary=str(exc),
                    errors=[str(exc)],
                    metadata={"worker_results": [r.model_dump() for r in worker_results]},
                )

            result = self._dispatcher.dispatch(task)

            try:
                budget_gate.after_result(result)
            except BudgetExceeded as exc:
                worker_results.append(result)
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="budget_exceeded",
                    summary=str(exc),
                    artifacts=list(artifacts.values()),
                    errors=[str(exc)],
                    metadata={"worker_results": [r.model_dump() for r in worker_results]},
                )

            worker_results.append(result)

            for artifact in result.artifacts:
                artifact_id = artifact.get("id") or artifact.get("artifact_id")
                if artifact_id:
                    artifacts[str(artifact_id)] = artifact

            if result.status == "completed":
                completed_step_ids.append(step.step_id)

            if result.status in ["failed", "blocked", "budget_exceeded"]:
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=result.status,
                    summary=f"Execution stopped at step {step.step_id}: {result.summary}",
                    artifacts=list(artifacts.values()),
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata={"worker_results": [r.model_dump() for r in worker_results]},
                )

            if result.status == "needs_replan":
                replan_request = self._build_replan_request(
                    plan=plan,
                    run_id=run_id,
                    failed_step_id=step.step_id,
                    result=result,
                    artifacts=artifacts,
                    completed_step_ids=completed_step_ids,
                    budget_gate=budget_gate,
                )
                if (
                    not self._allow_replan
                    or self._planner_runtime is None
                    or envelope is None
                    or _replan_depth >= 1
                ):
                    return Result(
                        run_id=run_id,
                        producer="worker_kernel",
                        status="needs_replan",
                        summary=f"Execution stopped at step {step.step_id}: {result.summary}",
                        artifacts=list(artifacts.values()),
                        errors=result.errors,
                        warnings=result.warnings,
                        metadata={
                            "worker_results": [r.model_dump() for r in worker_results],
                            "replan_request": replan_request.model_dump(mode="json"),
                        },
                    )

                replacement_plan = self._planner_runtime.replan(
                    envelope,
                    plan,
                    replan_request,
                )
                replacement_result = self.run(
                    replacement_plan,
                    envelope=envelope,
                    _replan_depth=_replan_depth + 1,
                )
                metadata = dict(replacement_result.metadata)
                metadata["replan"] = {
                    "request": replan_request.model_dump(mode="json"),
                    "replacement_plan": replacement_plan.model_dump(mode="json"),
                    "original_worker_results": [r.model_dump() for r in worker_results],
                    "depth": _replan_depth + 1,
                }
                return replacement_result.model_copy(update={"metadata": metadata})

        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="completed",
            summary="Plan executed successfully.",
            artifacts=list(artifacts.values()),
            usage={
                "tool_calls": budget_gate.tool_calls_used,
                "model_calls": budget_gate.model_calls_used,
                "workers": budget_gate.workers_used,
            },
            metadata={"worker_results": [r.model_dump() for r in worker_results]},
        )

    def _build_replan_request(
        self,
        *,
        plan: Plan,
        run_id: str,
        failed_step_id: str,
        result: Result,
        artifacts: dict[str, dict],
        completed_step_ids: list[str],
        budget_gate: BudgetGate,
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
            completed_artifacts=list(artifacts.values()),
            completed_step_ids=list(completed_step_ids),
            remaining_budget={
                "max_tool_calls": max(0, budget_gate.max_tool_calls - budget_gate.tool_calls_used),
                "max_model_calls": max(0, budget_gate.max_model_calls - budget_gate.model_calls_used),
                "max_workers": max(0, budget_gate.max_workers - budget_gate.workers_used),
                "max_retries": max(0, budget_gate.max_retries - budget_gate.retries_used),
            },
            recommended_action=self._recommended_action(result),
        )

    def _recommended_action(self, result: Result) -> str | None:
        value = (result.metadata or {}).get("recommended_action")
        if isinstance(value, str) and value.strip():
            return value
        return None
