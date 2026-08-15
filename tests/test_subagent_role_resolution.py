from __future__ import annotations

from pathlib import Path

import pytest

from travis.agent.types import AgentToolResult
from travis.coding_agent.agent_roles import AgentRoleDefinition
from travis.coding_agent.capabilities import CapabilitySource
from travis.coding_agent.subagent_roles import resolve_agent_role
from travis.coding_agent.tools import ToolDefinition


def _tool(name: str, *effects: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        label=name,
        description=name,
        parameters={},
        execute=lambda *_args: AgentToolResult(content=[]),
        effects=frozenset(effects),
    )


def _role(tmp_path: Path, **overrides: object) -> AgentRoleDefinition:
    mapping: dict[str, object] = {"name": "reviewer", **overrides}
    return AgentRoleDefinition.from_mapping(
        mapping,
        source=CapabilitySource("roles", str(tmp_path / "reviewer.json")),
    )


def test_role_tools_and_effects_only_narrow_active_parent_tools(tmp_path: Path) -> None:
    definitions = {
        item.name: item
        for item in (
            _tool("read", "read"),
            _tool("edit", "read", "write"),
            _tool("bash", "execute"),
            _tool("extension_without_metadata"),
        )
    }
    role = _role(
        tmp_path,
        allowedTools=["read", "edit", "excluded", "extension_without_metadata"],
        allowedEffects=["read"],
    )

    resolved = resolve_agent_role(
        role,
        parent_tools=("read", "edit", "bash", "extension_without_metadata"),
        definitions_by_name=definitions,
        requested_timeout=300,
    )

    assert resolved.allowed_tools == ("read",)
    assert resolved.allowed_effects == ("read",)
    assert resolved.timeout_seconds == 300


def test_missing_lists_inherit_but_explicit_empty_lists_grant_none(tmp_path: Path) -> None:
    definitions = {"read": _tool("read", "read"), "bash": _tool("bash", "execute")}
    inherited = resolve_agent_role(
        _role(tmp_path),
        parent_tools=("read", "bash"),
        definitions_by_name=definitions,
        requested_timeout=None,
    )
    empty = resolve_agent_role(
        _role(tmp_path, allowedTools=[], allowedEffects=[]),
        parent_tools=("read", "bash"),
        definitions_by_name=definitions,
        requested_timeout=3600,
    )

    assert inherited.allowed_tools == ("read", "bash")
    assert inherited.timeout_seconds == 1800
    assert empty.allowed_tools == ()
    assert empty.allowed_effects == ()
    assert empty.timeout_seconds == 1800


def test_role_cannot_defeat_current_depth_one_supervisor_ceiling(tmp_path: Path) -> None:
    definitions = {
        "read": _tool("read", "read"),
        "spawn_subagent": _tool("spawn_subagent", "execute"),
        "wait_subagent": _tool("wait_subagent", "read"),
    }

    resolved = resolve_agent_role(
        _role(
            tmp_path,
            canSpawn=True,
            maxDepth=1,
            allowedTools=["read", "spawn_subagent", "wait_subagent"],
        ),
        parent_tools=("read", "spawn_subagent", "wait_subagent"),
        definitions_by_name=definitions,
        requested_timeout=None,
    )

    assert resolved.allowed_tools == ("read",)


def test_role_context_is_bounded_and_resolved_beneath_role_source(tmp_path: Path) -> None:
    (tmp_path / "review.md").write_text("review context", encoding="utf-8")
    (tmp_path / "SKILL.md").write_text("skill context", encoding="utf-8")
    role = _role(tmp_path, context=["review.md"], skills=["SKILL.md"])

    resolved = resolve_agent_role(
        role,
        parent_tools=(),
        definitions_by_name={},
        requested_timeout=None,
    )

    assert "skill context" in resolved.context_pack
    assert "review context" in resolved.context_pack
    assert str(tmp_path) not in resolved.context_pack


def test_role_resolution_freezes_schema_and_model_role(tmp_path: Path) -> None:
    role = _role(
        tmp_path,
        modelRole="reviewer",
        resultSchema={"type": "object"},
        artifactPolicy="declared",
    )

    resolved = resolve_agent_role(
        role, parent_tools=(), definitions_by_name={}, requested_timeout=None
    )

    assert resolved.definition_name == "reviewer"
    assert resolved.model_role == "reviewer"
    assert resolved.result_schema == {"type": "object"}
    assert resolved.artifact_policy == "declared"


def test_requested_timeout_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        resolve_agent_role(
            _role(tmp_path),
            parent_tools=(),
            definitions_by_name={},
            requested_timeout=0,
        )
