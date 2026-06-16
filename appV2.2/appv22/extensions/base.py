from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from appv22.state.models import AgentState


@dataclass(frozen=True)
class ObservationContract:
    evidence_refs: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    preferred_tool_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "evidence_kinds", tuple(self.evidence_kinds))


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    extension_id: str
    triggers: tuple[str, ...]
    modes: tuple[str, ...]
    summary: str
    tool_ids: tuple[str, ...]
    observation_contract: ObservationContract | None = None
    instructions: tuple[str, ...] = ()
    always_active: bool = False

    def __post_init__(self) -> None:
        for field_name in ("triggers", "modes", "tool_ids", "instructions"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))

    def activates_for(self, state: AgentState) -> bool:
        if self.always_active:
            return True
        text = (state.request.active_user_request or state.request.user_goal).lower()
        return any(trigger.lower() in text for trigger in self.triggers)


class RuntimeExtension(Protocol):
    extension_id: str

    def skill_cards(self) -> list[SkillCard]:
        ...

    def before_tool_call(self, state: AgentState, tool_id: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def after_tool_call(self, state: AgentState, result: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        ...

    def transform_tool_result(self, result: dict[str, Any]) -> dict[str, Any] | None:
        ...
