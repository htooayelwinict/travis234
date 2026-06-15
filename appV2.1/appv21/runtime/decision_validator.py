"""Runtime decision validation for AppV2.1."""

from __future__ import annotations

from typing import Any

from appv21.runtime import rejections
from appv21.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision
from appv21.state.models import AgentState


class DecisionValidator:
    known_decision_kinds = KNOWN_DECISION_KINDS

    def validate(self, decision: RuntimeDecision, state: AgentState) -> list[str]:
        issues: list[str] = []
        payload = decision.payload

        if decision.kind not in self.known_decision_kinds:
            issues.append(f"{rejections.UNSUPPORTED_DECISION}:{decision.kind}")
        issues.extend(self._validate_evidence(decision, state))
        issues.extend(self._validate_payload(decision))
        if (
            decision.kind == "finalize"
            and not state.world.verification_receipts
            and not (isinstance(payload, dict) and payload.get("explicit_noop"))
        ):
            issues.append(rejections.FINALIZE_WITHOUT_VERIFICATION)
        return issues

    def _validate_evidence(self, decision: RuntimeDecision, state: AgentState) -> list[str]:
        issues: list[str] = []
        for ref in decision.evidence_refs:
            if ref == "plan://accepted/latest":
                if state.plan is None:
                    issues.append(f"{rejections.MISSING_EVIDENCE}:{ref}")
            elif ref == "verification://latest":
                if not state.world.verification_receipts:
                    issues.append(f"{rejections.MISSING_EVIDENCE}:{ref}")
            elif not self._has_world_evidence(ref, state):
                issues.append(f"{rejections.MISSING_EVIDENCE}:{ref}")
        return issues

    def _validate_payload(self, decision: RuntimeDecision) -> list[str]:
        payload: Any = decision.payload
        if not isinstance(payload, dict):
            return [f"{rejections.INVALID_PAYLOAD}:payload_not_object"]

        issues: list[str] = []
        if decision.kind == "tool_call" and not (payload.get("tool") or payload.get("tool_name")):
            issues.append(f"{rejections.INVALID_PAYLOAD}:tool_name_required")
        if decision.kind == "mutation_intent" and "operations" in payload and not isinstance(payload["operations"], list):
            issues.append(f"{rejections.INVALID_PAYLOAD}:operations_not_list")
        return issues

    def _has_world_evidence(self, ref: str, state: AgentState) -> bool:
        return (
            ref in state.world.refs
            or ref in state.world.mutation_receipts
            or ref in state.world.verification_receipts
        )
