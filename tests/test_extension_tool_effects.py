from __future__ import annotations

import pytest

from travis.agent.types import AgentToolResult
from travis.coding_agent import ExtensionRunner
from travis.coding_agent.tools.types import ToolDefinition


def _execute(*_args: object, **_kwargs: object) -> AgentToolResult:
    return AgentToolResult(content=[])


def _definition(**overrides: object) -> ToolDefinition:
    values: dict[str, object] = {
        "name": "extension_tool",
        "label": "extension",
        "description": "",
        "parameters": {},
        "execute": _execute,
    }
    values.update(overrides)
    return ToolDefinition(**values)  # type: ignore[arg-type]


def test_extension_registration_preserves_declared_effects_and_context() -> None:
    runner = ExtensionRunner()
    context = lambda args: {"action": "query", "server": args.get("server")}  # noqa: E731
    runner.register_tool(
        _definition(
            effects=["network", "read", "network"],
            policy_context=context,
        )
    )

    registered = runner.get_all_registered_tools()[0].definition

    assert registered.effects == frozenset({"read", "network"})
    assert registered.policy_context is context


def test_legacy_extension_without_effects_remains_load_compatible_and_undeclared() -> None:
    runner = ExtensionRunner()
    runner.register_tool(_definition())

    registered = runner.get_all_registered_tools()[0].definition

    assert registered.effects == frozenset()
    assert registered.policy_context is None


def test_extension_unknown_effect_is_rejected_at_registration_construction() -> None:
    with pytest.raises(ValueError, match="Unknown tool effects"):
        _definition(effects=["read", "credential_access"])


def test_extension_effect_string_is_not_treated_as_character_inventory() -> None:
    with pytest.raises(ValueError, match="collection"):
        _definition(effects="read")


def test_extension_registration_revalidates_mutated_effect_metadata() -> None:
    runner = ExtensionRunner()
    definition = _definition()
    definition.effects = frozenset({"credential_access"})  # type: ignore[assignment]

    with pytest.raises(ValueError, match="Unknown tool effects"):
        runner.register_tool(definition)


def test_extension_registration_rejects_noncallable_policy_context() -> None:
    runner = ExtensionRunner()
    definition = _definition()
    definition.policy_context = "unsafe"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="policy_context"):
        runner.register_tool(definition)
