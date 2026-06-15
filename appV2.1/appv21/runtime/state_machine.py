"""Formal runtime transition policy for AppV2.1.

This module defines the policy in isolation. Runtime enforcement is wired in
the next integration task so the policy can be unit-tested before it affects
the live agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from appv21.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision
from appv21.state.models import RuntimeMode


TRANSITIONS: dict[RuntimeMode, frozenset[str]] = {
    "START": frozenset({"observe", "tool_call", "read_file", "pause"}),
    "THINK": frozenset({"observe", "tool_call", "read_file", "plan", "mutation_intent", "verify", "compact", "pause", "finalize"}),
    "OBSERVE": frozenset({"observe", "tool_call", "read_file", "plan", "compact", "pause", "finalize"}),
    "PLAN": frozenset({"observe", "tool_call", "read_file", "mutation_intent", "compact", "pause", "finalize"}),
    "ACT": frozenset({"verify", "observe", "tool_call", "read_file", "compact", "pause", "finalize"}),
    "VERIFY": frozenset({"finalize", "plan", "observe", "tool_call", "read_file", "compact", "pause"}),
    "REVISE": frozenset({"observe", "tool_call", "read_file", "plan", "pause"}),
    "COMPACT": frozenset({"observe", "tool_call", "read_file", "plan", "mutation_intent", "verify", "pause", "finalize"}),
    "PAUSE": frozenset(),
    "FINALIZE": frozenset(),
    "FAILED": frozenset(),
}

TARGET_MODE_BY_DECISION: dict[str, RuntimeMode] = {
    "observe": "OBSERVE",
    "tool_call": "OBSERVE",
    "read_file": "OBSERVE",
    "plan": "PLAN",
    "mutation_intent": "ACT",
    "verify": "VERIFY",
    "compact": "COMPACT",
    "pause": "PAUSE",
    "finalize": "FINALIZE",
}


@dataclass
class RuntimeStateMachine:
    max_repeated_decisions: int = 3
    _last_nonproductive_key: str | None = None
    _repeated_count: int = 0

    def validate_transition(self, current_mode: RuntimeMode | str, decision: RuntimeDecision) -> str | None:
        if current_mode not in TRANSITIONS:
            return f"invalid_mode:{current_mode}"
        allowed = TRANSITIONS[current_mode]  # type: ignore[index]
        if decision.kind not in allowed:
            return f"invalid_transition:{current_mode}->{decision.kind}"
        return None

    def next_mode(self, current_mode: RuntimeMode | str, decision: RuntimeDecision) -> RuntimeMode:
        rejection = self.validate_transition(current_mode, decision)
        if rejection is not None:
            raise ValueError(rejection)
        return TARGET_MODE_BY_DECISION[decision.kind]

    def record_progress(self, decision: RuntimeDecision, *, changed: bool) -> str | None:
        key = decision.kind
        if changed:
            self._last_nonproductive_key = None
            self._repeated_count = 0
            return None
        if key != self._last_nonproductive_key:
            self._last_nonproductive_key = key
            self._repeated_count = 0
        self._repeated_count += 1
        if self._repeated_count >= self.max_repeated_decisions:
            return f"repeated_loop:{key}"
        return None


def unmapped_decision_kinds() -> set[str]:
    return set(KNOWN_DECISION_KINDS) - set(TARGET_MODE_BY_DECISION)
