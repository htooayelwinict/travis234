from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.context.budget import ContextBudgetManager, DEFAULT_SECTION_BUDGETS
from appv21.extensions.skills import SkillRouter
from appv21.state.models import AgentState, RequestEnvelope


def test_context_budget_estimates_section_sizes() -> None:
    payload = {
        "system": {"role": "architect"},
        "agent": ["planner", "executor"],
        "untracked": "ignored for section budgets",
    }

    estimate = ContextBudgetManager().estimate(payload)

    expected_system_chars = len(json.dumps(payload["system"], sort_keys=True, default=str))
    expected_agent_chars = len(json.dumps(payload["agent"], sort_keys=True, default=str))
    expected_total_chars = len(json.dumps(payload, sort_keys=True, default=str))

    assert estimate["total_chars"] == expected_total_chars
    assert estimate["sections"]["system"] == {
        "chars": expected_system_chars,
        "budget": DEFAULT_SECTION_BUDGETS["system"],
        "over_budget": False,
    }
    assert estimate["sections"]["agent"] == {
        "chars": expected_agent_chars,
        "budget": DEFAULT_SECTION_BUDGETS["agent"],
        "over_budget": False,
    }
    assert estimate["sections"]["skills"]["chars"] == 0
    assert estimate["over_budget_sections"] == []


def test_context_budget_marks_over_budget_sections() -> None:
    manager = ContextBudgetManager(
        section_budgets={
            **DEFAULT_SECTION_BUDGETS,
            "system": 2,
            "tools": 3,
        }
    )
    payload = {
        "system": "abcd",
        "tools": {"names": ["search"]},
        "world": "within",
    }

    estimate = manager.estimate(payload)

    assert estimate["sections"]["system"]["over_budget"] is True
    assert estimate["sections"]["tools"]["over_budget"] is True
    assert estimate["sections"]["world"]["over_budget"] is False
    assert estimate["over_budget_sections"] == ["system", "tools"]


def test_context_budget_defensively_copies_constructor_budget_dict() -> None:
    budgets = {
        **DEFAULT_SECTION_BUDGETS,
        "system": 2,
    }
    manager = ContextBudgetManager(section_budgets=budgets)

    budgets["system"] = 1000

    estimate = manager.estimate({"system": "abcd"})

    assert estimate["sections"]["system"]["budget"] == 2
    assert estimate["sections"]["system"]["over_budget"] is True


def test_context_budget_section_budgets_cannot_be_mutated() -> None:
    manager = ContextBudgetManager()

    with pytest.raises(TypeError):
        manager.section_budgets["world"] = 1

    assert manager.section_budgets["world"] == DEFAULT_SECTION_BUDGETS["world"]


def test_context_budget_rejects_unsupported_payload_objects() -> None:
    class UnsupportedPayload:
        pass

    manager = ContextBudgetManager()

    with pytest.raises(TypeError, match="JSON serializable"):
        manager.estimate({"system": UnsupportedPayload()})


def test_context_budget_over_budget_sections_use_canonical_order() -> None:
    manager = ContextBudgetManager(
        section_budgets={
            "tools": 3,
            "system": 2,
            "agent": DEFAULT_SECTION_BUDGETS["agent"],
            "skills": DEFAULT_SECTION_BUDGETS["skills"],
            "world": DEFAULT_SECTION_BUDGETS["world"],
            "state": DEFAULT_SECTION_BUDGETS["state"],
            "output_contract": DEFAULT_SECTION_BUDGETS["output_contract"],
            "decomposition": DEFAULT_SECTION_BUDGETS["decomposition"],
        }
    )

    estimate = manager.estimate(
        {
            "tools": {"names": ["search"]},
            "system": "abcd",
        }
    )

    assert list(estimate["sections"]) == list(DEFAULT_SECTION_BUDGETS)
    assert estimate["over_budget_sections"] == ["system", "tools"]


def test_workspace_cleanup_skill_activates_as_card() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal="Please cleanup and organize this workspace.",
            root_path=".",
        ),
    )

    cards = SkillRouter().active_skills(state)

    assert cards == [
        {
            "skill_id": "workspace_cleanup",
            "triggers": ["cleanup", "organize", "move", "workspace"],
            "modes": ["OBSERVE", "PLAN", "ACT", "VERIFY"],
            "summary": "Organize observed workspace files while preserving protected project, documentation, asset, and secret paths.",
            "tool_preferences": ["repo_snapshot", "read_file"],
            "artifact_templates": ["workspace_manifest"],
            "preservation_rules": [
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
            "verification_hints": [
                "Confirm protected paths are excluded from proposed moves.",
                "Workspace manifest must include observed_files, protected_paths, proposed_moves, and skipped_paths sections.",
                "Do not read or expose secret file contents.",
            ],
            "budget_priority": 80,
        }
    ]
    assert "prompt_patch" not in cards[0]


def test_workspace_cleanup_skill_does_not_activate_for_remove_imports() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal="remove unused imports",
            root_path=".",
        ),
    )

    assert SkillRouter().active_skills(state) == []


def test_workspace_cleanup_skill_cards_are_isolated_from_returned_mutations() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal="Organize this workspace safely.",
            root_path=".",
        ),
    )
    router = SkillRouter()

    cards = router.active_skills(state)
    cards[0]["tool_preferences"].append("mutated")
    cards[0]["preservation_rules"].clear()

    next_cards = router.active_skills(state)

    assert next_cards[0]["tool_preferences"] == ["repo_snapshot", "read_file"]
    assert next_cards[0]["preservation_rules"] == [
        "tests/**",
        "src/**",
        "assets/**",
        "secrets/**",
        "README.md",
        "docs/**",
        "**/keep*",
        "**/do_not_move*",
        "**/old_blob*",
    ]
