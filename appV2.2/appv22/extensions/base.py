from __future__ import annotations

from dataclasses import dataclass
import re
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
        text = activation_context_text(state).lower()
        return any(_trigger_matches(text, trigger) for trigger in self.triggers)


def activation_context_text(state: AgentState, *, max_chars: int = 6000) -> str:
    request = state.request
    parts = [request.active_user_request or request.user_goal]
    if request.user_goal and request.user_goal != request.active_user_request:
        parts.append(request.user_goal)
    ui_context = request.ui_context if isinstance(request.ui_context, dict) else {}
    summary = ui_context.get("conversation_summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary)
    text = "\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _trigger_matches(text: str, trigger: str) -> bool:
    normalized = trigger.lower().strip()
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9_]+", normalized):
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])", text) is not None
    return normalized in text


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

    def finalize_guidance(self, state: AgentState) -> str:
        ...

    def transform_tool_result(self, result: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def sanitize_world_ref_payload(self, kind: str, payload: Any) -> dict[str, Any]:
        ...

    def world_ref_has_usable_payload(self, state: AgentState, world_ref: dict[str, Any]) -> bool | None:
        ...
