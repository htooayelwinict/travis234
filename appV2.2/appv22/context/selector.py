from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from appv22.extensions.registry import ResolvedExtensions
from appv22.state.models import AgentState

class ContextSelector:
    def __init__(self, *, tool_registry=None) -> None:
        self.tool_registry = tool_registry

    def select(
        self,
        state: AgentState,
        resolved: ResolvedExtensions,
        *,
        pre_turn_mode: str,
    ) -> dict[str, Any]:
        if pre_turn_mode in {"START", "THINK"}:
            skill_cards = list(resolved.skill_cards)
        else:
            skill_cards = [card for card in resolved.skill_cards if pre_turn_mode in card.modes]
        selected_tools = [
            tool_id
            for card in skill_cards
            for tool_id in card.tool_ids
        ]
        selected_tools = self._select_tools_for_turn(state, selected_tools)
        prompt_visible_tool_ids = set(selected_tools)
        serialized_skills = []
        for card in skill_cards:
            serialized_card = asdict(card)
            serialized_card["tool_ids"] = tuple(
                tool_id for tool_id in card.tool_ids if tool_id in prompt_visible_tool_ids
            )
            serialized_skills.append(serialized_card)
        selected_skills = [card.skill_id for card in skill_cards]

        return {
            "state": {
                "mode": pre_turn_mode,
                "context_summary": deepcopy(state.context_summary),
                "turn_feedback": list(state.turn_feedback),
            },
            "skills": serialized_skills,
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

    def _select_tools_for_turn(self, _state: AgentState, tool_ids: list[str]) -> list[str]:
        return list(dict.fromkeys(tool_ids))
