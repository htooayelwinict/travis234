"""Structured skill cards for AppV2.1 context governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from appv21.state.models import AgentState


WORKSPACE_CLEANUP_TRIGGERS = ("cleanup", "organize", "move", "workspace")


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    triggers: list[str]
    modes: list[str]
    summary: str
    tool_preferences: list[str]
    artifact_templates: list[str]
    preservation_rules: list[str]
    verification_hints: list[str]
    budget_priority: int

    def to_prompt_card(self) -> dict:
        return asdict(self)


WORKSPACE_CLEANUP_CARD = SkillCard(
    skill_id="workspace_cleanup",
    triggers=list(WORKSPACE_CLEANUP_TRIGGERS),
    modes=["OBSERVE", "PLAN", "ACT", "VERIFY"],
    summary=(
        "Organize observed workspace files while preserving protected project, "
        "documentation, asset, and secret paths."
    ),
    tool_preferences=["repo_snapshot", "read_file"],
    artifact_templates=["workspace_manifest"],
    preservation_rules=[
        "tests/**",
        "src/**",
        "assets/**",
        "secrets/**",
        "README.md",
        "docs/**",
        "**/keep*",
        "**/do_not_move*",
        "**/old_blob*",
    ],
    verification_hints=[
        "Confirm protected paths are excluded from proposed moves.",
        (
            "Workspace manifest must include observed_files, protected_paths, "
            "proposed_moves, and skipped_paths sections."
        ),
        "Do not read or expose secret file contents.",
    ],
    budget_priority=80,
)


class SkillRegistry:
    def active_skill_cards(self, state: AgentState) -> list[dict]:
        text = state.request.user_goal.lower()
        if any(trigger in text for trigger in WORKSPACE_CLEANUP_TRIGGERS):
            return [WORKSPACE_CLEANUP_CARD.to_prompt_card()]
        return []
