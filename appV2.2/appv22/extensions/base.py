from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from appv22.state.models import AgentState


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    extension_id: str
    triggers: tuple[str, ...]
    modes: tuple[str, ...]
    summary: str
    planner_id: str
    mutation_policy_id: str
    mutation_executor_id: str
    verifier_id: str
    tool_ids: tuple[str, ...]
    artifact_schema_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("triggers", "modes", "tool_ids", "artifact_schema_ids"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))

    def activates_for(self, state: AgentState) -> bool:
        if self.modes and state.mode not in self.modes:
            return False
        text = state.request.user_goal.lower()
        return any(trigger.lower() in text for trigger in self.triggers)


class RuntimeExtension(Protocol):
    extension_id: str

    def skill_cards(self) -> list[SkillCard]:
        ...

    def register_capabilities(self, capabilities: object) -> None:
        ...
