"""Kernel-owned plan normalization for worker execution."""

from __future__ import annotations

from typing import Any

from app.repair_policy import WORKER_STAGE_REPAIR_ATTEMPTS
from app.schemas import Plan, PlanStep


def normalize_execution_plan(plan: Plan, registry: Any) -> tuple[Plan, list[dict[str, Any]]]:
    """Return a plan whose budgets cover kernel-owned worker recovery attempts."""

    adjustments: list[dict[str, Any]] = []
    normalized_steps: list[PlanStep] = []
    budget = dict(plan.budget)
    current_retry_budget = int(budget.get("max_retries", 0) or 0)
    if current_retry_budget < WORKER_STAGE_REPAIR_ATTEMPTS:
        adjustments.append(
            {
                "field": "budget.max_retries",
                "from": current_retry_budget,
                "to": WORKER_STAGE_REPAIR_ATTEMPTS,
                "reason": (
                    "worker runtime retries are capped per stage, with "
                    f"{WORKER_STAGE_REPAIR_ATTEMPTS} repair attempts per stage"
                ),
            }
        )
        budget["max_retries"] = WORKER_STAGE_REPAIR_ATTEMPTS

    for step in plan.steps:
        minimum_model_calls = minimum_model_calls_for_step(step, registry)
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
        retry_envelope_call_budget(step.max_tool_calls, retry_limit, kind="tool")
        for step in normalized_steps
    )
    required_model_calls = sum(
        retry_envelope_call_budget(step.max_model_calls, retry_limit, kind="model")
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


def retry_envelope_call_budget(initial_limit: int, retry_limit: int, *, kind: str) -> int:
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


def minimum_model_calls_for_step(step: PlanStep, registry: Any) -> int:
    try:
        group = registry.get(step.worker_type)
    except ValueError:
        return step.max_model_calls

    minimum_model_calls = getattr(group, "minimum_model_calls", None)
    if callable(minimum_model_calls):
        return int(minimum_model_calls(step))
    if step.max_tool_calls > 0 and step_uses_tools(step):
        return 2
    return step.max_model_calls


def step_uses_tools(step: PlanStep) -> bool:
    permissions = step.permissions
    return any(
        [
            permissions.read_files,
            permissions.write_files,
            permissions.run_commands,
            permissions.web_research,
        ]
    )
