"""Runtime-owned compact run memory artifact builder."""

from __future__ import annotations

from collections import Counter
from typing import Any

from appv21.state.models import AgentState, Artifact


class RunMemoryBuilder:
    def build(self, state: AgentState, events: list[dict]) -> Artifact:
        verification_receipts = list(state.world.verification_receipts)
        mutation_receipts = list(state.world.mutation_receipts)
        return Artifact(
            artifact_id="run_memory",
            kind="context_summary",
            content={
                "goal": state.request.user_goal,
                "outcome": self._outcome(state),
                "event_counts": self._event_counts(events),
                "decision_counts": self._decision_counts(events),
                "tools_used": self._tools_used(events),
                "mutation_receipts": mutation_receipts,
                "verification_receipts": verification_receipts,
                "open_risks": self._open_risks(state, events),
            },
            producer="appv21_runtime",
            trust="runtime_verified",
            lifecycle="runtime_verified",
            evidence_refs=verification_receipts or mutation_receipts,
        )

    def _outcome(self, state: AgentState) -> str:
        if state.result and state.result.get("status"):
            return str(state.result["status"])
        if state.terminal:
            return "terminal"
        return "completed"

    def _event_counts(self, events: list[dict]) -> dict[str, int]:
        counts = Counter(str(event.get("event_type") or "") for event in events)
        counts.pop("", None)
        return dict(sorted(counts.items()))

    def _decision_counts(self, events: list[dict]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for event in events:
            if event.get("event_type") != "DecisionProposed":
                continue
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("kind"):
                counts[str(payload["kind"])] += 1
        return dict(sorted(counts.items()))

    def _tools_used(self, events: list[dict]) -> list[str]:
        tools: list[str] = []
        seen: set[str] = set()
        for event in events:
            if event.get("event_type") not in {"ToolCallCompleted", "ToolCallDenied"}:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or not payload.get("tool_name"):
                continue
            tool_name = str(payload["tool_name"])
            if tool_name not in seen:
                seen.add(tool_name)
                tools.append(tool_name)
        return tools

    def _open_risks(self, state: AgentState, events: list[dict]) -> list[dict[str, Any]]:
        risks: list[dict[str, Any]] = []
        if state.plan is not None:
            for unknown in state.plan.unknowns:
                risks.append({"source": "plan_unknown", "detail": unknown})
        for event in events:
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event_type == "ToolCallDenied":
                risks.append({"source": "tool_denied", "detail": payload.get("tool_name")})
            elif event_type == "DecisionRejected":
                risks.append({"source": "decision_rejected", "detail": payload.get("reason")})
            elif event_type == "VerificationRecorded" and payload.get("status") != "passed":
                risks.append({"source": "verification", "detail": payload.get("verification_id")})
        return risks
