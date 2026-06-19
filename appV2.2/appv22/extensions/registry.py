from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.state.models import AgentState


@dataclass(frozen=True)
class ResolvedExtensions:
    extension_ids: tuple[str, ...]
    skill_cards: tuple[SkillCard, ...]
    tool_ids: tuple[str, ...]


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
        )

    def tool_result_guidance(self, extension_ids: tuple[str, ...], result: dict[str, Any]) -> tuple[str, ...]:
        guidance: list[str] = []
        for extension_id in extension_ids:
            extension = self._extensions.get(extension_id)
            if extension is None:
                continue
            hook = getattr(extension, "tool_result_guidance", None)
            if not callable(hook):
                continue
            try:
                message = hook(result)
            except Exception:  # noqa: BLE001 - extension hook details are not safe runtime context.
                message = "Extension tool_result_guidance failed safely; continue using selected tools and public evidence."
            if isinstance(message, str) and message.strip():
                guidance.append(message.strip())
        return tuple(guidance)

    def finalize_guidance(self, extension_ids: tuple[str, ...], state: AgentState) -> tuple[str, ...]:
        guidance: list[str] = []
        for extension_id in extension_ids:
            extension = self._extensions.get(extension_id)
            if extension is None:
                continue
            hook = getattr(extension, "finalize_guidance", None)
            if not callable(hook):
                continue
            try:
                message = hook(state)
            except Exception:  # noqa: BLE001 - extension hook details are not safe runtime context.
                message = ""
            if isinstance(message, str) and message.strip():
                guidance.append(message.strip())
        return tuple(guidance)

    def transform_tool_result(self, extension_ids: tuple[str, ...], result: dict[str, Any]) -> dict[str, Any]:
        current = dict(result)
        for extension_id in extension_ids:
            extension = self._extensions.get(extension_id)
            if extension is None:
                continue
            hook = getattr(extension, "transform_tool_result", None)
            if not callable(hook):
                continue
            try:
                replacement = hook(current)
            except Exception:  # noqa: BLE001 - transform hooks are presentation only.
                replacement = None
            if isinstance(replacement, dict) and replacement:
                current = {**current, **replacement}
        return current

    def before_tool_call(
        self,
        extension_ids: tuple[str, ...],
        state: AgentState,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        for extension_id in extension_ids:
            extension = self._extensions.get(extension_id)
            if extension is None:
                continue
            hook = getattr(extension, "before_tool_call", None)
            if not callable(hook):
                continue
            try:
                result = hook(state, tool_id, arguments)
            except Exception:  # noqa: BLE001 - extension hook details are not safe runtime context.
                result = {
                    "reason": "extension_before_tool_call_exception",
                    "errors": ["extension_before_tool_call_exception"],
                }
            if isinstance(result, dict) and result:
                return result
        return None

    def after_tool_call(
        self,
        extension_ids: tuple[str, ...],
        state: AgentState,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        current = dict(result)
        for extension_id in extension_ids:
            extension = self._extensions.get(extension_id)
            if extension is None:
                continue
            hook = getattr(extension, "after_tool_call", None)
            if not callable(hook):
                continue
            try:
                replacement = hook(state, current)
            except Exception:  # noqa: BLE001 - keep original safe broker result; never persist hook exception details.
                replacement = None
            if isinstance(replacement, dict) and replacement:
                current = replacement
        return current

    def world_ref_has_usable_payload(
        self,
        extension_ids: tuple[str, ...],
        state: AgentState,
        world_ref: dict[str, Any],
    ) -> bool | None:
        for extension_id in extension_ids:
            extension = self._extensions.get(extension_id)
            if extension is None:
                continue
            hook = getattr(extension, "world_ref_has_usable_payload", None)
            if not callable(hook):
                continue
            try:
                result = hook(state, world_ref)
            except Exception:  # noqa: BLE001 - extension hook details are not safe runtime context.
                return False
            if isinstance(result, bool):
                return result
        return None
