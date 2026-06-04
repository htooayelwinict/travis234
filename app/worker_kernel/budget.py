"""Deterministic budget enforcement for worker-kernel execution."""

from __future__ import annotations

from app.schemas import Plan, Result, Task
from app.repair_policy import WORKER_STAGE_REPAIR_ATTEMPTS


class BudgetExceeded(Exception):
    """Raised when execution exceeds the plan budget ceilings."""


class BudgetGate:
    def __init__(self, budget: dict) -> None:
        budget = budget or {}
        self.max_tool_calls = int(budget.get("max_tool_calls", 8))
        self.max_model_calls = int(budget.get("max_model_calls", 2))
        self.max_workers = int(budget.get("max_workers", 2))
        self.max_retries = max(
            WORKER_STAGE_REPAIR_ATTEMPTS,
            int(budget.get("max_retries", WORKER_STAGE_REPAIR_ATTEMPTS)),
        )

        if (
            self.max_tool_calls < 0
            or self.max_model_calls < 0
            or self.max_workers < 0
            or self.max_retries < 0
        ):
            raise ValueError("Budget values must be non-negative")

        self.tool_calls_used = 0
        self.model_calls_used = 0
        self.workers_used = 0
        self.retries_used = 0

    def check_plan(self, plan: Plan) -> None:
        if not plan.steps:
            raise ValueError("Plan must contain at least one step")

        requested_tool_calls = sum(step.max_tool_calls for step in plan.steps)
        requested_model_calls = sum(step.max_model_calls for step in plan.steps)

        if any(step.max_tool_calls < 0 for step in plan.steps):
            raise ValueError("Plan step max_tool_calls must be non-negative")
        if any(step.max_model_calls < 0 for step in plan.steps):
            raise ValueError("Plan step max_model_calls must be non-negative")

        if len(plan.steps) > self.max_workers:
            raise BudgetExceeded("Plan requests too many worker steps.")
        if requested_tool_calls > self.max_tool_calls:
            raise BudgetExceeded("Plan requests too many tool calls.")
        if requested_model_calls > self.max_model_calls:
            raise BudgetExceeded("Plan requests too many model calls.")

    def before_task(self, task: Task) -> None:
        if self.workers_used + 1 > self.max_workers:
            raise BudgetExceeded("Worker budget exceeded.")
        if self.tool_calls_used + task.max_tool_calls > self.max_tool_calls:
            raise BudgetExceeded("Tool budget exceeded.")
        if self.model_calls_used + task.max_model_calls > self.max_model_calls:
            raise BudgetExceeded("Model budget exceeded.")

        self.workers_used += 1

    def after_result(self, result: Result) -> None:
        usage = result.usage or {}
        self.tool_calls_used += int(usage.get("tool_calls", 0))
        self.model_calls_used += int(usage.get("model_calls", 0))

        if self.tool_calls_used > self.max_tool_calls:
            raise BudgetExceeded("Tool budget exceeded.")
        if self.model_calls_used > self.max_model_calls:
            raise BudgetExceeded("Model budget exceeded.")

    def can_retry(self, *, step_retries_used: int = 0) -> bool:
        return step_retries_used < self.max_retries

    def record_retry(self, *, step_retries_used: int = 0) -> None:
        if not self.can_retry(step_retries_used=step_retries_used):
            raise BudgetExceeded("Retry budget exceeded.")
        self.retries_used += 1
