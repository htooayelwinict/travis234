from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from travis.coding_agent.agent_roles import AgentRoleDefinition, AgentRoleRegistry
from travis.coding_agent.capabilities import (
    CapabilityKind,
    CapabilityRecord,
    CapabilitySource,
)
from travis.coding_agent.capabilities.registry import CapabilityResolution, CapabilitySnapshot


SOURCE = CapabilitySource("roles", "/tmp/reviewer.json", scope="global")


def test_role_defaults_are_narrow_and_bounded() -> None:
    role = AgentRoleDefinition.from_mapping({"name": "reviewer"}, source=SOURCE)

    assert role.model_role == "worker"
    assert role.can_spawn is False
    assert role.max_depth == 1
    assert role.default_timeout_seconds == 1800
    assert role.allowed_tools is None
    assert role.allowed_effects is None
    assert role.artifact_policy == "none"
    with pytest.raises(FrozenInstanceError):
        role.name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("timeout", [0, 3601, True, "10"])
def test_role_timeout_is_strictly_bounded(timeout: object) -> None:
    with pytest.raises((TypeError, ValueError), match="timeout"):
        AgentRoleDefinition.from_mapping(
            {"name": "worker", "defaultTimeoutSeconds": timeout}, source=SOURCE
        )


@pytest.mark.parametrize("name", ["", "UPPER", "two words", "../escape", "a" * 65])
def test_role_name_is_safe_and_bounded(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        AgentRoleDefinition.from_mapping({"name": name}, source=SOURCE)


def test_role_normalizes_lists_effects_and_defensively_copies_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    role = AgentRoleDefinition.from_mapping(
        {
            "name": "reviewer",
            "description": "Review changes",
            "modelRole": "reviewer",
            "allowedTools": ["read", "grep"],
            "allowedEffects": ["network", "read", "write"],
            "skills": ["review/SKILL.md"],
            "context": ["context/review.md"],
            "resultSchema": schema,
            "artifactPolicy": "declared_and_trace",
        },
        source=SOURCE,
    )
    schema["type"] = "array"

    assert role.allowed_tools == ("read", "grep")
    assert role.allowed_effects == ("read", "write", "network")
    assert role.skills == ("review/SKILL.md",)
    assert role.context == ("context/review.md",)
    assert role.result_schema == {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }


@pytest.mark.parametrize(
    "mapping,match",
    [
        ({"name": "worker", "unknown": 1}, "unknown"),
        ({"name": "worker", "modelRole": "primary"}, "modelRole"),
        ({"name": "worker", "allowedEffects": ["delete"]}, "effect"),
        ({"name": "worker", "allowedTools": [1]}, "allowedTools"),
        ({"name": "worker", "skills": ["/tmp/SKILL.md"]}, "relative"),
        ({"name": "worker", "context": ["../secret"]}, "escape"),
        ({"name": "worker", "artifactPolicy": "all"}, "artifactPolicy"),
        ({"name": "worker", "resultSchema": {"type": "invalid"}}, "schema"),
    ],
)
def test_role_rejects_unknown_or_unsafe_fields(mapping: dict[str, object], match: str) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        AgentRoleDefinition.from_mapping(mapping, source=SOURCE)


def test_role_rejects_oversized_and_overdeep_schemas() -> None:
    with pytest.raises(ValueError, match="64 KiB"):
        AgentRoleDefinition.from_mapping(
            {"name": "large", "resultSchema": {"description": "x" * 65537}},
            source=SOURCE,
        )
    nested: dict[str, object] = {"type": "string"}
    for _ in range(33):
        nested = {"properties": {"child": nested}}
    with pytest.raises(ValueError, match="32"):
        AgentRoleDefinition.from_mapping(
            {"name": "deep", "resultSchema": nested}, source=SOURCE
        )


def test_role_registry_projects_snapshot_winners() -> None:
    low = AgentRoleDefinition.from_mapping({"name": "reviewer"}, source=SOURCE)
    high_source = CapabilitySource("roles", "/tmp/project.json", scope="project")
    high = AgentRoleDefinition.from_mapping(
        {"name": "reviewer", "description": "project"}, source=high_source
    )
    records = (
        CapabilityRecord(CapabilityKind.AGENT_ROLE, "reviewer", high, high_source, 20),
    )
    snapshot = CapabilitySnapshot(1, (), {CapabilityKind.AGENT_ROLE: records}, {
        (CapabilityKind.AGENT_ROLE, "reviewer"): CapabilityResolution(
            records[0], (records[0],)
        ),
    }, {})
    registry = AgentRoleRegistry(snapshot)

    assert low.name == "reviewer"
    assert registry.get("reviewer") is high
    assert registry.get("missing") is None
    assert registry.list() == (high,)
