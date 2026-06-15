from __future__ import annotations

from dataclasses import dataclass

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.state.models import AgentState


@dataclass(frozen=True)
class ResolvedExtensions:
    extension_ids: tuple[str, ...]
    skill_cards: tuple[SkillCard, ...]
    tool_ids: tuple[str, ...]
    planner_ids: tuple[str, ...]
    mutation_policy_ids: tuple[str, ...]
    mutation_executor_ids: tuple[str, ...]
    verifier_ids: tuple[str, ...]
    artifact_schema_ids: tuple[str, ...]


class ExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, RuntimeExtension] = {}

    def register(self, extension: RuntimeExtension) -> None:
        if extension.extension_id in self._extensions:
            raise ValueError(f"duplicate extension_id: {extension.extension_id}")
        self._extensions[extension.extension_id] = extension

    def resolve_active(self, state: AgentState) -> ResolvedExtensions:
        cards: list[SkillCard] = []
        seen_skill_cards: set[tuple[str, str]] = set()
        for extension in self._extensions.values():
            for card in extension.skill_cards():
                if not card.activates_for(state):
                    continue
                if card.extension_id != extension.extension_id:
                    raise ValueError(
                        "skill card extension_id mismatch: "
                        f"registered extension {extension.extension_id} returned {card.extension_id}"
                    )
                skill_key = (card.extension_id, card.skill_id)
                if skill_key in seen_skill_cards:
                    raise ValueError(f"duplicate active skill card: {card.extension_id}/{card.skill_id}")
                seen_skill_cards.add(skill_key)
                cards.append(card)
        cards = sorted(cards, key=lambda card: (card.extension_id, card.skill_id))
        return ResolvedExtensions(
            extension_ids=tuple(sorted({card.extension_id for card in cards})),
            skill_cards=tuple(cards),
            tool_ids=tuple(sorted({tool_id for card in cards for tool_id in card.tool_ids})),
            planner_ids=tuple(sorted({card.planner_id for card in cards})),
            mutation_policy_ids=tuple(sorted({card.mutation_policy_id for card in cards})),
            mutation_executor_ids=tuple(sorted({card.mutation_executor_id for card in cards})),
            verifier_ids=tuple(sorted({card.verifier_id for card in cards})),
            artifact_schema_ids=tuple(sorted(
                {schema_id for card in cards for schema_id in card.artifact_schema_ids}
            )),
        )
