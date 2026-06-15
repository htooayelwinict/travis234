"""Dual context manager for AppV2.1."""

from __future__ import annotations

from appv21.state.events import RuntimeEvent
from appv21.state.models import AgentState
from appv21.context.compactor import RuntimeContextCompactor


class DualContextManager:
    def __init__(self, *, compactor: RuntimeContextCompactor | None = None) -> None:
        self.compactor = compactor or RuntimeContextCompactor()

    def build_turn_context(self, state: AgentState) -> dict:
        refs = state.world.refs.values()
        if state.context.world_digest:
            preserved = set(state.context.world_digest.get("preserved_world_refs") or state.context.world_digest.get("latest_world_refs") or [])
            refs = [ref for ref in state.world.refs.values() if ref.ref_id in preserved]
        world_refs = [
            {
                "ref_id": ref.ref_id,
                "kind": "repo_snapshot"
                if ref.kind == "tool_result" and ref.payload.get("tool_name") == "repo_snapshot"
                else ref.kind,
                "summary": ref.summary,
                "trust": ref.trust,
            }
            for ref in refs
        ]
        context = {
            "request": state.request.user_goal,
            "mode": state.mode,
            "conversation_summary": state.conversation.summary,
            "world_refs": world_refs,
            "artifacts": list(state.world.artifacts),
            "mutation_leases": list(state.world.mutation_leases),
            "verification_receipts": list(state.world.verification_receipts),
        }
        if state.context.world_digest:
            context["world_digest"] = state.context.world_digest
            context["compacted"] = True
        return context

    def maybe_compact(self, state: AgentState, *, force: bool = False) -> list[RuntimeEvent]:
        if not force and not self.compactor.should_compact(state):
            return []
        world_digest = self.compactor.compact(state)
        conversation_digest = state.conversation.summary or "Conversation compacted; current request remains active."
        reason = "context_overflow_forced" if force else "runtime_threshold_or_evidence"
        return [
            RuntimeEvent("ContextCompactionRequested", {"reason": reason}),
            RuntimeEvent("ContextCompacted", {"world_digest": world_digest, "conversation_digest": conversation_digest}),
        ]
