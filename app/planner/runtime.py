"""Planner runtime that emits validated plans for worker execution."""

from __future__ import annotations

from typing import Any

from app.planner.env_config import build_planner_model_client
from app.planner.prompt_chain import LLMPlanCompiler, PlannerPromptChainError
from app.schemas import Envelope, Plan, ReplanRequest


class PlannerRuntime:
    def __init__(
        self,
        *,
        compiler: Any | None = None,
        model_client: Any | None = None,
        fallback_on_error: bool = True,
    ) -> None:
        if compiler is not None:
            self._compiler = compiler
        elif model_client is not None:
            self._compiler = LLMPlanCompiler(model_client=model_client)
        else:
            self._compiler = None
        self._fallback_on_error = fallback_on_error

    @classmethod
    def from_env(
        cls,
        dotenv_path: str = ".env",
        *,
        client_factory=None,
        fallback_on_error: bool = True,
    ) -> "PlannerRuntime":
        model_client = build_planner_model_client(
            dotenv_path,
            **({"client_factory": client_factory} if client_factory is not None else {}),
        )
        return cls(
            model_client=model_client,
            fallback_on_error=fallback_on_error,
        )

    def run(self, envelope: Envelope) -> Plan:
        if self._compiler is None:
            return self._safe_fallback_plan(envelope, fallback_reason="planner_llm_unavailable")

        try:
            plan = self._compiler.run(envelope)
            metadata = dict(plan.metadata)
            metadata["planner_runtime"] = {
                "mode": "llm_prompt_chain",
                "fallback_reason": None,
            }
            return plan.model_copy(update={"metadata": metadata})
        except Exception as exc:
            if not self._fallback_on_error:
                raise
            return self._safe_fallback_plan(
                envelope,
                fallback_reason=self._fallback_reason(exc),
            )

    def replan(self, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
        if self._compiler is None:
            return self._safe_fallback_replan(
                envelope,
                current_plan=current_plan,
                replan_request=replan_request,
                fallback_reason="planner_llm_unavailable",
            )

        try:
            plan = self._compiler.replan(
                envelope=envelope,
                current_plan=current_plan,
                replan_request=replan_request,
            )
            metadata = dict(plan.metadata)
            metadata["planner_runtime"] = {
                "mode": "llm_prompt_chain_replan",
                "fallback_reason": None,
            }
            return plan.model_copy(update={"metadata": metadata})
        except Exception as exc:
            if not self._fallback_on_error:
                raise
            return self._safe_fallback_replan(
                envelope,
                current_plan=current_plan,
                replan_request=replan_request,
                fallback_reason=self._fallback_reason(exc),
            )

    def _safe_fallback_plan(self, envelope: Envelope, *, fallback_reason: str) -> Plan:
        return Plan(
            plan_id=f"plan_{envelope.request_id}",
            request_id=envelope.request_id,
            planner="fallback",
            objective=f"Clarify and observe before acting: {envelope.normalized_input}",
            strategy="observe_first",
            steps=[
                {
                    "step_id": "observe_scope",
                    "worker_type": "repo_worker",
                    "instruction": (
                        "Collect scope context and identify likely target files before any mutation. "
                        "Summarize assumptions and unknowns."
                    ),
                    "output_artifacts": ["scope_observation"],
                    "max_tool_calls": 3,
                    "max_model_calls": 1,
                    "permissions": {
                        "read_files": True,
                        "write_files": False,
                        "run_commands": False,
                        "web_research": False,
                    },
                }
            ],
            budget={
                "max_tool_calls": 3,
                "max_model_calls": 1,
                "max_workers": 1,
                "max_retries": 0,
            },
            success_criteria=["First step is observation only.", "No file mutation is attempted."],
            metadata={
                "planner_runtime": {
                    "mode": "fallback",
                    "fallback_reason": fallback_reason,
                }
            },
        )

    def _safe_fallback_replan(
        self,
        envelope: Envelope,
        *,
        current_plan: Plan,
        replan_request: ReplanRequest,
        fallback_reason: str,
    ) -> Plan:
        return Plan(
            plan_id=f"plan_{envelope.request_id}_replan_fallback",
            request_id=envelope.request_id,
            planner="fallback_replan",
            objective=f"Recover from failed step {replan_request.failed_step_id}: {envelope.normalized_input}",
            strategy="reobserve_after_replan_request",
            execution_pattern="discover_finalize",
            steps=[
                {
                    "step_id": "reobserve_scope",
                    "worker_type": "repo_worker",
                    "phase": "DISCOVER",
                    "mode": "observe_only",
                    "task_id": "replan_recovery",
                    "instruction": (
                        "Known facts: A prior worker requested replan before the task could continue. "
                        "Unknowns: The safe next mutation scope is not established. "
                        "Do now: Re-observe relevant context and summarize what must be clarified or replanned. "
                        "Do not do: Do not mutate files or run commands. "
                        "Output: replan_scope_observation."
                    ),
                    "output_artifacts": ["replan_scope_observation"],
                    "max_tool_calls": 3,
                    "max_model_calls": 1,
                    "permissions": {
                        "read_files": True,
                        "write_files": False,
                        "run_commands": False,
                        "web_research": False,
                    },
                },
                {
                    "step_id": "finalize_replan_fallback",
                    "worker_type": "research_worker",
                    "phase": "FINALIZE",
                    "mode": "summarize_only",
                    "task_id": "replan_recovery",
                    "instruction": (
                        "Known facts: replan_scope_observation is available. "
                        "Unknowns: Whether the original plan can continue without a stronger planner. "
                        "Do now: Summarize the replan request, recovered scope, and safe next action. "
                        "Do not do: Do not invent completed work. "
                        "Output: final_report."
                    ),
                    "input_artifacts": ["replan_scope_observation"],
                    "output_artifacts": ["final_report"],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                    "permissions": {
                        "read_files": False,
                        "write_files": False,
                        "run_commands": False,
                        "web_research": False,
                    },
                },
            ],
            budget={
                "max_tool_calls": 3,
                "max_model_calls": 2,
                "max_workers": 2,
                "max_retries": 0,
            },
            global_invariants=["observe_before_mutate", "no_file_mutation_in_fallback_replan"],
            success_criteria=["A safe replan recovery summary is produced."],
            metadata={
                "planner_runtime": {
                    "mode": "fallback_replan",
                    "fallback_reason": fallback_reason,
                },
                "replan": {
                    "parent_plan_id": current_plan.plan_id,
                    "failed_step_id": replan_request.failed_step_id,
                    "reason": replan_request.reason,
                },
            },
        )

    def _fallback_reason(self, exc: Exception) -> str:
        if isinstance(exc, PlannerPromptChainError):
            return "planner_llm_validation_failed"
        return "planner_llm_error"
