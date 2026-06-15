"""Prompt/context assembly contract for AppV2.1."""

from __future__ import annotations

from typing import Any

from appv21.state.models import AgentState


class PromptBuilder:
    """Builds layered agent prompt payloads without owning state transitions."""

    def build(
        self,
        *,
        state: AgentState,
        turn_context: dict[str, Any],
        active_skills: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
        selected_context: dict[str, Any] | None = None,
        context_budget: dict[str, Any] | None = None,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if selected_context is not None:
            prompt_state = selected_context["state"]
            prompt_world = selected_context["world"]
            prompt_skills = selected_context["skills"]
            prompt_tools = selected_context["tools"]
            prompt_selection = selection if selection is not None else selected_context.get("selection", {})
        else:
            prompt_state = {
                "mode": state.mode,
                "plan": state.plan.__dict__ if state.plan is not None else None,
                "artifacts": list(state.world.artifacts),
                "mutation_receipts": list(state.world.mutation_receipts),
                "verification_receipts": list(state.world.verification_receipts),
                "pauses": [pause.__dict__ for pause in state.pauses],
                "terminal": state.terminal,
            }
            prompt_world = turn_context
            prompt_skills = active_skills
            prompt_tools = tool_specs
            prompt_selection = selection or {}

        return {
            "state": prompt_state,
            "system": {
                "identity": "AppV2.1 runtime-first coding agent",
                "contract": [
                    "Conversation enters the agent loop before planning.",
                    "Planner may only plan from runtime-observed world refs.",
                    "All writes require a runtime-issued mutation lease.",
                    "Runtime-verified artifacts require evidence references.",
                ],
            },
            "agent": {
                "mode": state.mode,
                "request": state.request.user_goal,
                "constraints": state.request.constraints,
            },
            "skills": prompt_skills,
            "world": prompt_world,
            "decomposition": turn_context.get("decomposition", {}),
            "tools": prompt_tools,
            "context_budget": context_budget or {},
            "selection": prompt_selection,
            "output_contract": {
                "allowed_decisions": ["observe", "read_file", "plan", "tool_call", "mutation_intent", "verify", "compact", "pause", "finalize"],
                "write_boundary": "MutationLease",
                "artifact_boundary": "ArtifactValidator",
            },
        }
