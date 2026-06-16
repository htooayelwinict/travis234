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

    def _select_tools_for_turn(self, state: AgentState, tool_ids: list[str]) -> list[str]:
        unique_tool_ids = list(dict.fromkeys(tool_ids))
        if self._has_repeated_completed_observe_result(state):
            return []
        return unique_tool_ids

    def _tool_category(self, tool_id: str) -> str:
        definition = None
        if self.tool_registry is not None:
            lookup = getattr(self.tool_registry, "definition", None)
            if callable(lookup):
                definition = lookup(tool_id)
        return str(getattr(definition, "category", ""))

    def _has_repeated_completed_observe_result(self, state: AgentState) -> bool:
        seen: set[tuple[str, str]] = set()
        for result in state.tool_results.values():
            if not isinstance(result, dict) or result.get("status") != "completed":
                continue
            tool_id = result.get("tool_id")
            if not isinstance(tool_id, str) or self._tool_category(tool_id) != "observe":
                continue
            marker = (tool_id, repr(result.get("arguments") if isinstance(result.get("arguments"), dict) else {}))
            if marker in seen:
                return True
            seen.add(marker)
        return False
