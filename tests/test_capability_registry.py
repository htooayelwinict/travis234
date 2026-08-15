from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from travis.coding_agent.capabilities.types import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityLoadContext,
    CapabilityRecord,
    CapabilitySource,
)


def test_capability_kinds_cover_phase_one_and_reserved_followups() -> None:
    assert {kind.value for kind in CapabilityKind} == {
        "context_file",
        "skill",
        "prompt_template",
        "theme",
        "extension",
        "tool",
        "agent_role",
    }


def test_capability_records_and_context_are_immutable() -> None:
    source = CapabilitySource("test", "/tmp/a")
    record = CapabilityRecord(CapabilityKind.SKILL, "audit", object(), source)
    context = CapabilityLoadContext(
        "/tmp/repo",
        "/tmp/agent",
        False,
        True,
        1,
        MappingProxyType({"reason": "test"}),
    )

    with pytest.raises(FrozenInstanceError):
        record.key = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        context.data["reason"] = "changed"  # type: ignore[index]


def test_diagnostic_attribution_is_stable() -> None:
    source = CapabilitySource("skills", "/repo/SKILL.md")
    item = CapabilityDiagnostic(
        "collision",
        "skills",
        "capability_collision",
        'skill "audit" was shadowed',
        source,
    )

    assert item.source is source
    assert item.code == "capability_collision"
