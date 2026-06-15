"""Structured skill cards for AppV2.1 context governance."""

from __future__ import annotations

import re
from dataclasses import dataclass

from appv21.state.models import AgentState


WORKSPACE_CLEANUP_TRIGGERS = ("cleanup", "organize", "move", "workspace")
WORKSPACE_CLEANUP_INTENT_TOKENS = frozenset(("cleanup", "organize", "move"))
WORKSPACE_CLEANUP_INTENT_PHRASES = ("clean up",)


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    triggers: tuple[str, ...]
    modes: tuple[str, ...]
    summary: str
    tool_preferences: tuple[str, ...]
    artifact_templates: tuple[str, ...]
    preservation_rules: tuple[str, ...]
    verification_hints: tuple[str, ...]
    budget_priority: int

    def to_prompt_card(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "triggers": list(self.triggers),
            "modes": list(self.modes),
            "summary": self.summary,
            "tool_preferences": list(self.tool_preferences),
            "artifact_templates": list(self.artifact_templates),
            "preservation_rules": list(self.preservation_rules),
            "verification_hints": list(self.verification_hints),
            "budget_priority": self.budget_priority,
        }


WORKSPACE_CLEANUP_CARD = SkillCard(
    skill_id="workspace_cleanup",
    triggers=WORKSPACE_CLEANUP_TRIGGERS,
    modes=("OBSERVE", "PLAN", "ACT", "VERIFY"),
    summary=(
        "Organize observed workspace files while preserving protected project, "
        "documentation, asset, and secret paths."
    ),
    tool_preferences=("repo_snapshot", "read_file"),
    artifact_templates=("workspace_manifest",),
    preservation_rules=(
        "tests/**",
        "src/**",
        "assets/**",
        "secrets/**",
        "README.md",
        "docs/**",
        "**/keep*",
        "**/do_not_move*",
        "**/old_blob*",
    ),
    verification_hints=(
        "Confirm protected paths are excluded from proposed moves.",
        (
            "Workspace manifest must include observed_files, protected_paths, "
            "proposed_moves, and skipped_paths sections."
        ),
        "Do not read or expose secret file contents.",
    ),
    budget_priority=80,
)


class SkillRegistry:
    def active_skill_cards(self, state: AgentState) -> list[dict]:
        text = state.request.user_goal.lower()
        tokens = set(re.findall(r"[a-z0-9_]+", text))
        has_intent_token = bool(tokens & WORKSPACE_CLEANUP_INTENT_TOKENS)
        has_intent_phrase = any(phrase in text for phrase in WORKSPACE_CLEANUP_INTENT_PHRASES)
        if has_intent_token or has_intent_phrase:
            return [WORKSPACE_CLEANUP_CARD.to_prompt_card()]
        return []
