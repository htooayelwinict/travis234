from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from appv22.extensions.registry import ResolvedExtensions
from appv22.state.models import AgentState

READ_TOOL_MODES = frozenset({"START", "THINK", "OBSERVE", "VERIFY"})


class ContextSelector:
    def select(
        self,
        state: AgentState,
        resolved: ResolvedExtensions,
        *,
        pre_turn_mode: str,
    ) -> dict[str, Any]:
        skill_cards = [card for card in resolved.skill_cards if pre_turn_mode in card.modes]
        selected_tools = [
            tool_id
            for card in skill_cards
            for tool_id in card.tool_ids
        ] if pre_turn_mode in READ_TOOL_MODES else []
        selected_skills = [card.skill_id for card in skill_cards]

        return {
            "state": {
                "mode": pre_turn_mode,
                "runtime_plan": deepcopy(state.runtime_plan),
                "mutation_receipts": deepcopy(state.mutation_receipts),
                "verification_receipts": deepcopy(state.verification_receipts),
            },
            "skills": [asdict(card) for card in skill_cards],
            "tools": selected_tools,
            "world": {"world_refs": deepcopy(state.world_refs)},
            "selection": {
                "mode": pre_turn_mode,
                "selected_tools": list(selected_tools),
                "selected_skills": selected_skills,
                "active_extensions": list(resolved.extension_ids),
                "available_tools": list(selected_tools),
            },
        }
