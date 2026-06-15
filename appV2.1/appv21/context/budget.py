from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from typing import Mapping


DEFAULT_SECTION_BUDGETS = MappingProxyType({
    "system": 8000,
    "agent": 12000,
    "skills": 10000,
    "tools": 12000,
    "world": 20000,
    "state": 16000,
    "output_contract": 6000,
    "decomposition": 8000,
})


def _immutable_section_budgets(section_budgets: Mapping[str, int]) -> Mapping[str, int]:
    merged = {**DEFAULT_SECTION_BUDGETS, **dict(section_budgets)}
    return MappingProxyType(
        {section_name: merged[section_name] for section_name in DEFAULT_SECTION_BUDGETS}
    )


def _default_section_budgets() -> Mapping[str, int]:
    return _immutable_section_budgets(DEFAULT_SECTION_BUDGETS)


def _estimate_chars(value: Any) -> int:
    try:
        serialized = json.dumps(value, sort_keys=True)
    except TypeError as exc:
        raise TypeError("context budget payload values must be JSON serializable") from exc
    return len(serialized)


@dataclass(frozen=True)
class ContextBudgetManager:
    section_budgets: Mapping[str, int] = field(default_factory=_default_section_budgets)

    def __post_init__(self) -> None:
        object.__setattr__(self, "section_budgets", _immutable_section_budgets(self.section_budgets))

    def estimate(self, payload: dict[str, Any]) -> dict[str, Any]:
        sections: dict[str, dict[str, Any]] = {}
        over_budget_sections: list[str] = []

        for section_name in DEFAULT_SECTION_BUDGETS:
            budget = self.section_budgets[section_name]
            chars = _estimate_chars(payload[section_name]) if section_name in payload else 0
            over_budget = chars > budget
            sections[section_name] = {
                "chars": chars,
                "budget": budget,
                "over_budget": over_budget,
            }
            if over_budget:
                over_budget_sections.append(section_name)

        return {
            "total_chars": _estimate_chars(payload),
            "sections": sections,
            "over_budget_sections": over_budget_sections,
        }
