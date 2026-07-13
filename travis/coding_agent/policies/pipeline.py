"""Composable first-decision-wins coding policy evaluation."""

from __future__ import annotations

from collections.abc import Iterable

from travis.coding_agent.policies.types import (
    Allow,
    CodingTurnContext,
    PolicyDecision,
    ToolCallView,
    ToolPolicy,
)


class PolicyPipeline:
    def __init__(self, policies: Iterable[ToolPolicy]) -> None:
        self._policies = tuple(policies)

    def evaluate(self, call: ToolCallView, context: CodingTurnContext) -> PolicyDecision:
        for policy in self._policies:
            decision = policy.evaluate(call, context)
            if not isinstance(decision, Allow):
                return decision
        return Allow()


__all__ = ["PolicyPipeline"]
