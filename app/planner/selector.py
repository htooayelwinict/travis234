"""Planner selection logic."""

from __future__ import annotations

import re

from app.planner.base import BasePlanner
from app.planner.planners import (
    CodePlanner,
    DirectPlanner,
    FallbackPlanner,
    InfraPlanner,
    ResearchPlanner,
)
from app.schemas import Envelope


class PlannerSelector:
    """Deterministically selects a planner implementation."""

    def __init__(self) -> None:
        self._direct = DirectPlanner()
        self._code = CodePlanner()
        self._research = ResearchPlanner()
        self._infra = InfraPlanner()
        self._fallback = FallbackPlanner()
        self._registry: dict[str, BasePlanner] = {
            "direct_planner": self._direct,
            "direct": self._direct,
            "code_planner": self._code,
            "code": self._code,
            "research_planner": self._research,
            "research": self._research,
            "infra_planner": self._infra,
            "infra": self._infra,
            "fallback_planner": self._fallback,
            "fallback": self._fallback,
        }

    def select(self, envelope: Envelope) -> BasePlanner:
        if self._needs_observe_first(envelope):
            return self._fallback

        if _has_text(envelope.input_type, "question") and not envelope.artifacts:
            return self._direct

        if envelope.confidence < 0.55:
            return self._fallback

        if _has_signal(envelope.intents, "code") or (
            _has_signal(envelope.domains, "code") and _has_signal(envelope.risks, "file mutation")
        ):
            return self._code

        if _has_signal(envelope.intents, "research") or _has_signal(envelope.domains, "research"):
            return self._research

        if _has_signal(envelope.intents, "infra") or _has_signal(envelope.domains, "infra"):
            return self._infra

        return self._fallback

    def _needs_observe_first(self, envelope: Envelope) -> bool:
        return (
            _has_text(envelope.input_type, "ambiguous")
            or _has_signal(envelope.risks, "ambiguous scope")
            or _has_signal(envelope.context_needed, "scope clarification")
            or _has_signal(envelope.constraints, "target scope must be identified before mutation")
            or envelope.confidence < 0.55
        )


def _has_signal(values: list[str], needle: str) -> bool:
    return any(_has_text(value, needle) for value in values)


def _has_text(value: str, needle: str) -> bool:
    return _normalize(needle) in _normalize(value)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
