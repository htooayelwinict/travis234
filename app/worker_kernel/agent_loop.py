"""First-class agent loop for one worker instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from app.runtime_matrix import RuntimeMatrixLogger
from app.schemas import Result, Task
from app.worker_kernel.model_adapter import WorkerModelDecisionError
from app.worker_kernel.observations import error_observation
from app.repair_policy import LOCAL_MODEL_DECISION_REPAIR_ATTEMPTS
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


@dataclass(frozen=True)
class AgentTurn:
    round_number: int
    model_calls_used: int
    remaining_tool_calls: int


@dataclass(frozen=True)
class AgentLoopOutcome:
    result: Result
    rounds_used: int


IssueResultFactory = Callable[
    [Literal["failed", "blocked", "budget_exceeded", "needs_replan"], str, str, str, bool],
    Result,
]


class AgentRunLoop:
    """Owns model turns, tool turns, finalization, and local repair boundaries."""

    def __init__(self, *, max_rounds: int) -> None:
        self._max_rounds = max_rounds

    def run(
        self,
        *,
        worker_type: str,
        task: Task,
        template: WorkerInstanceTemplate,
        state: Any,
        usage: dict[str, int],
        controller: Any,
        prompt_builder: Callable[..., str],
        execute_tool_calls: Callable[..., Result | None],
        handle_final_result: Callable[..., Result],
        fallback_from_observations: Callable[..., Result],
        issue_result: IssueResultFactory,
        trace_event: Callable[..., None],
        trace: RuntimeMatrixLogger | None = None,
    ) -> AgentLoopOutcome:
        rounds = 0
        model_decision_repairs = 0
        while rounds < self._max_rounds:
            if usage["model_calls"] >= task.max_model_calls:
                return AgentLoopOutcome(
                    result=fallback_from_observations(
                        task=task,
                        template=template,
                        state=state,
                        usage=usage,
                    ),
                    rounds_used=rounds,
                )

            rounds += 1
            usage["model_calls"] += 1
            turn = AgentTurn(
                round_number=rounds,
                model_calls_used=usage["model_calls"],
                remaining_tool_calls=max(0, task.max_tool_calls - usage["tool_calls"]),
            )
            trace_event(
                trace,
                task=task,
                template=template,
                event="worker_model_call_started",
                status="started",
                details={
                    "round": turn.round_number,
                    "model_calls_used_including_this_turn": turn.model_calls_used,
                    "remaining_tool_calls": turn.remaining_tool_calls,
                    "agent_loop": "agent_run_loop_v1",
                },
            )
            try:
                decision = controller.decide(
                    stage=f"{worker_type}_{template.name}",
                    prompt=prompt_builder(task=task, template=template, state=state, usage=usage),
                )
            except Exception as exc:
                if (
                    isinstance(exc, WorkerModelDecisionError)
                    and model_decision_repairs < LOCAL_MODEL_DECISION_REPAIR_ATTEMPTS
                    and usage["model_calls"] < task.max_model_calls
                ):
                    model_decision_repairs += 1
                    repair_record = {
                        "instance": template.name,
                        "tool_name": None,
                        "arguments": {},
                        "observation": {
                            "model_behavior_error": True,
                            "message": str(exc),
                            "instruction": (
                                "Return valid JSON matching the worker decision schema. "
                                "Use either tool_calls or final_result, not free-form text."
                            ),
                        },
                        "tool_observation": error_observation(
                            tool_name="worker_model_decision",
                            status="failed",
                            code="model_behavior_error",
                            message=str(exc),
                            repair_hint="Return valid structured JSON matching the worker decision schema.",
                        ).model_dump(mode="json"),
                    }
                    if hasattr(state, "observations"):
                        state.observations.append(repair_record)
                    trace_event(
                        trace,
                        task=task,
                        template=template,
                        event="worker_model_decision_repair_scheduled",
                        status="retrying",
                        details={
                            "error": str(exc),
                            "round": rounds,
                            "repair_attempt": model_decision_repairs,
                            "max_repair_attempts": LOCAL_MODEL_DECISION_REPAIR_ATTEMPTS,
                            "agent_loop": "agent_run_loop_v1",
                        },
                    )
                    continue
                trace_event(
                    trace,
                    task=task,
                    template=template,
                    event="worker_model_call_failed",
                    status="failed",
                    details={"error": str(exc), "round": rounds, "agent_loop": "agent_run_loop_v1"},
                )
                return AgentLoopOutcome(
                    result=issue_result(
                        "failed",
                        "instance_failure",
                        "model_behavior_error" if isinstance(exc, WorkerModelDecisionError) else "worker_llm_error",
                        str(exc),
                        True,
                    ),
                    rounds_used=rounds,
                )

            trace_event(
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
                    "agent_loop": "agent_run_loop_v1",
                },
            )
            if decision.tool_calls:
                tool_result = execute_tool_calls(
                    task=task,
                    template=template,
                    state=state,
                    usage=usage,
                    tool_calls=decision.tool_calls,
                    trace=trace,
                )
                if tool_result is not None:
                    return AgentLoopOutcome(result=tool_result, rounds_used=rounds)
                continue

            if decision.final_result is not None:
                return AgentLoopOutcome(
                    result=handle_final_result(
                        task=task,
                        template=template,
                        state=state,
                        usage=usage,
                        final=decision.final_result,
                    ),
                    rounds_used=rounds,
                )

            return AgentLoopOutcome(
                result=issue_result(
                    "failed",
                    "instance_failure",
                    "empty_worker_decision",
                    "worker model returned neither tool_calls nor final_result",
                    True,
                ),
                rounds_used=rounds,
            )

        return AgentLoopOutcome(
            result=fallback_from_observations(
                task=task,
                template=template,
                state=state,
                usage=usage,
            ),
            rounds_used=rounds,
        )
