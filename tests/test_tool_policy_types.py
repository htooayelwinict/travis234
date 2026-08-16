from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from travis.agent.types import AgentToolResult
from travis.coding_agent.policy.types import ToolPolicyDecision, ToolPolicySettings
from travis.coding_agent.tools.types import ToolDefinition


def _execute(*_args: object, **_kwargs: object) -> AgentToolResult:
    return AgentToolResult(content=[])


def test_tool_definition_defaults_to_undeclared_effects() -> None:
    tool = ToolDefinition(
        name="legacy",
        label="Legacy",
        description="",
        parameters={},
        execute=_execute,
    )

    assert tool.effects == frozenset()
    assert tool.policy_context is None


def test_policy_types_are_immutable() -> None:
    settings = ToolPolicySettings(mode="audit", auto_allow_effects=frozenset({"read"}))
    decision = ToolPolicyDecision(
        tool_name="read",
        effects=frozenset({"read"}),
        mode="audit",
        allow=True,
        reason_code="auto_allowed",
    )

    with pytest.raises(FrozenInstanceError):
        settings.mode = "enforce"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.allow = False  # type: ignore[misc]


def test_policy_settings_normalize_effects_to_frozenset() -> None:
    settings = ToolPolicySettings(
        mode="enforce",
        auto_allow_effects=frozenset({"read", "execute", "read"}),
    )

    assert settings.auto_allow_effects == frozenset({"read", "execute"})
