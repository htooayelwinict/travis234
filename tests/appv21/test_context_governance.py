from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.context.budget import ContextBudgetManager, DEFAULT_SECTION_BUDGETS


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
