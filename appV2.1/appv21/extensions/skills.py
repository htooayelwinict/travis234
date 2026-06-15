"""Skill router for AppV2.1."""

from __future__ import annotations

from appv21.extensions.skill_registry import SkillRegistry
from appv21.state.models import AgentState


class SkillRouter:
    def active_skills(self, state: AgentState) -> list[dict]:
        return SkillRegistry().active_skill_cards(state)
