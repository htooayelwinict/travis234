"""Monotonic capability resolution for typed subagent roles."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from travis.coding_agent.agent_roles import AgentRoleDefinition, ArtifactPolicy, ModelRole
from travis.coding_agent.policy import TOOL_EFFECT_ORDER, ToolEffect

_CONTEXT_MAX_CHARS = 32_768
_CONTEXT_FILE_MAX_CHARS = 16_384
_SUBAGENT_CONTROL_TOOLS = {
    "spawn_subagent",
    "wait_subagent",
    "list_subagents",
    "get_subagent_result",
    "expand_subagent_result",
    "cancel_subagent",
}


class _ToolDefinition(Protocol):
    effects: frozenset[ToolEffect]


@dataclass(frozen=True)
class ResolvedAgentRole:
    definition_name: str
    allowed_tools: tuple[str, ...]
    allowed_effects: tuple[ToolEffect, ...]
    model_role: ModelRole
    context_pack: str
    timeout_seconds: int
    result_schema: dict[str, object] | None
    artifact_policy: ArtifactPolicy


def resolve_agent_role(
    definition: AgentRoleDefinition,
    parent_tools: tuple[str, ...] | list[str],
    definitions_by_name: Mapping[str, _ToolDefinition],
    requested_timeout: int | None,
) -> ResolvedAgentRole:
    """Freeze a role against the already-filtered parent capability ceiling."""

    if requested_timeout is not None and (
        isinstance(requested_timeout, bool)
        or not isinstance(requested_timeout, int)
        or requested_timeout <= 0
    ):
        raise ValueError("requested subagent timeout must be positive")
    parent = tuple(dict.fromkeys(parent_tools))
    requested_tools = (
        set(parent)
        if definition.allowed_tools is None
        else set(definition.allowed_tools)
    )
    effect_ceiling = (
        tuple(TOOL_EFFECT_ORDER)
        if definition.allowed_effects is None
        else definition.allowed_effects
    )
    effect_set = set(effect_ceiling)
    final_tools: list[str] = []
    for name in parent:
        if name not in requested_tools:
            continue
        tool = definitions_by_name.get(name)
        if tool is None or not tool.effects:
            continue
        if not set(tool.effects).issubset(effect_set):
            continue
        # The runtime's authoritative supervisor ceiling is currently one child
        # level. Role declarations may narrow that contract, never widen it.
        if name in _SUBAGENT_CONTROL_TOOLS:
            continue
        final_tools.append(name)
    timeout = min(
        definition.default_timeout_seconds,
        requested_timeout if requested_timeout is not None else definition.default_timeout_seconds,
    )
    return ResolvedAgentRole(
        definition_name=definition.name,
        allowed_tools=tuple(final_tools),
        allowed_effects=tuple(effect_ceiling),
        model_role=definition.model_role,
        context_pack=_load_context_pack(definition),
        timeout_seconds=timeout,
        result_schema=copy.deepcopy(definition.result_schema),
        artifact_policy=definition.artifact_policy,
    )


def _load_context_pack(definition: AgentRoleDefinition) -> str:
    if definition.source.path is None:
        return ""
    root = Path(definition.source.path).resolve().parent
    sections: list[str] = []
    remaining = _CONTEXT_MAX_CHARS
    for kind, entries in (("skill", definition.skills), ("context", definition.context)):
        for entry in entries:
            path = (root / entry).resolve()
            try:
                path.relative_to(root)
                content = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            content = content[:_CONTEXT_FILE_MAX_CHARS]
            section = f"Role {kind} ({entry}):\n{content}"
            if len(section) > remaining:
                section = section[:remaining]
            if not section:
                return "\n\n".join(sections)
            sections.append(section)
            remaining -= len(section)
            if remaining <= 0:
                return "\n\n".join(sections)
    return "\n\n".join(sections)


__all__ = ["ResolvedAgentRole", "resolve_agent_role"]
