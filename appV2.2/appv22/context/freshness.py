from __future__ import annotations

from typing import Any

from appv22.state.models import AgentState


def is_world_ref_fresh(state: AgentState, world_ref: dict[str, Any], definition: Any) -> bool:
    freshness = getattr(definition, "freshness", "stable")
    if freshness == "turn":
        return world_ref.get("request_id") == state.request.request_id and world_ref.get("run_id") == state.run_id
    if getattr(definition, "invalidated_by_mutation", False):
        return world_ref.get("mutation_seq") == state.mutation_seq
    return True


def fresh_world_refs(
    state: AgentState,
    world_refs: dict[str, dict[str, Any]],
    *,
    definition_for,
) -> dict[str, dict[str, Any]]:
    fresh: dict[str, dict[str, Any]] = {}
    for ref_id, ref in world_refs.items():
        if not isinstance(ref_id, str) or not isinstance(ref, dict):
            continue
        tool_id = ref.get("kind")
        if not isinstance(tool_id, str):
            continue
        definition = definition_for(tool_id)
        if definition is None or is_world_ref_fresh(state, ref, definition):
            fresh[ref_id] = ref
    return fresh


def stale_world_ref_ids(
    state: AgentState,
    world_refs: dict[str, dict[str, Any]],
    *,
    definition_for,
) -> set[str]:
    stale: set[str] = set()
    for ref_id, ref in world_refs.items():
        if not isinstance(ref_id, str) or not isinstance(ref, dict):
            continue
        tool_id = ref.get("kind")
        if not isinstance(tool_id, str):
            continue
        definition = definition_for(tool_id)
        if definition is not None and not is_world_ref_fresh(state, ref, definition):
            stale.add(ref_id)
    return stale
