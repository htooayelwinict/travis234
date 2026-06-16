from __future__ import annotations

from typing import Any


SUMMARY_KEYS = ("goals", "decisions", "progress", "blockers", "evidence_refs")


def normalized_context_summary(summary: Any) -> dict[str, list[Any]]:
    source = summary if isinstance(summary, dict) else {}
    return {
        key: list(source.get(key, [])) if isinstance(source.get(key, []), list) else []
        for key in SUMMARY_KEYS
    }


def drop_unavailable_tool_risks(summary: Any, active_tool_ids: tuple[str, ...]) -> dict[str, list[Any]]:
    """Keep Hermes summaries as reference context, not stale tool policy.

    A previous turn can legitimately report that a tool was unavailable. That is
    useful history, but it must not remain an active blocker when the current
    turn's selected tool surface is different. This mirrors Pi's current-tool
    authority and Hermes' summary-as-reference boundary.
    """
    normalized = normalized_context_summary(summary)
    active = set(active_tool_ids)
    kept: list[Any] = []
    for risk in normalized.get("blockers", []):
        if not isinstance(risk, str):
            kept.append(risk)
            continue
        inactive_tool_id = inactive_tool_id_from_risk(risk)
        if inactive_tool_id and inactive_tool_id not in active:
            continue
        kept.append(risk)
    normalized["blockers"] = kept
    return normalized


def strip_cross_turn_tool_availability_risks(summary: Any) -> dict[str, list[Any]]:
    """Return a persistence-safe summary for future turns.

    Tool availability is recalculated from the registry/extension selection on
    each turn. Persisting unavailable-tool denials as active open risks causes
    stale summaries to override the current tool surface.
    """
    normalized = normalized_context_summary(summary)
    normalized["blockers"] = [
        risk
        for risk in normalized.get("blockers", [])
        if not (isinstance(risk, str) and is_tool_availability_risk(risk))
    ]
    return normalized


def strip_turn_local_repair_risks(summary: Any) -> dict[str, list[Any]]:
    """Remove provider-shape repair guidance from durable active risks.

    Malformed JSON/tool-call/schema repair is current-turn feedback, like Pi
    tool-result errors and Hermes message sanitization. It must not become
    persisted task state.
    """
    normalized = normalized_context_summary(summary)
    normalized["blockers"] = [
        risk
        for risk in normalized.get("blockers", [])
        if not (isinstance(risk, str) and is_turn_local_repair_risk(risk))
    ]
    return normalized


def resolve_tool_risks_after_success(summary: Any, tool_id: str) -> dict[str, list[Any]]:
    """Demote same-tool failures from active blockers after later success.

    Hermes keeps compacted history as reference material while the latest
    successful turn evidence wins. Pi keeps tool failures in chronological
    messages rather than a permanent active blocker. This gives AppV2.2 the
    same lifecycle: failed/denied risks for a tool are active until that tool
    later completes successfully.
    """
    normalized = normalized_context_summary(summary)
    if not tool_id:
        return normalized
    removed = [
        risk
        for risk in normalized.get("blockers", [])
        if isinstance(risk, str) and _risk_mentions_tool_failure(risk, tool_id)
    ]
    if not removed:
        return normalized
    normalized["blockers"] = [
        risk
        for risk in normalized.get("blockers", [])
        if not (isinstance(risk, str) and _risk_mentions_tool_failure(risk, tool_id))
    ]
    progress = normalized.setdefault("progress", [])
    marker = f"{tool_id}: prior failed/denied tool risk resolved by later successful result"
    if marker not in progress:
        progress.append(marker)
    return normalized


def resolve_tool_risks_from_world_refs(summary: Any, world_refs: Any) -> dict[str, list[Any]]:
    """Normalize active risks against already persisted successful evidence.

    Continued sessions may load an old failed tool risk and an already persisted
    successful world ref for the same tool. Before the next model call, the
    successful evidence must win; otherwise a compacted historical failure acts
    like live policy and can trap the agent in retries.
    """
    normalized = normalized_context_summary(summary)
    if not isinstance(world_refs, dict):
        return normalized
    resolved = normalized
    for ref in world_refs.values():
        if not isinstance(ref, dict):
            continue
        tool_id = ref.get("kind")
        if isinstance(tool_id, str) and tool_id:
            resolved = resolve_tool_risks_after_success(resolved, tool_id)
    return resolved


def inactive_tool_id_from_risk(risk: str) -> str:
    marker = "inactive_tool:"
    if marker in risk:
        return risk.split(marker, 1)[1].split()[0].strip(".,;:)'\"]")
    denied = " request was denied"
    if denied in risk:
        return risk.split(denied, 1)[0].strip()
    return ""


def is_tool_availability_risk(risk: str) -> bool:
    return "inactive_tool:" in risk or " request was denied" in risk


def is_durable_blocker(risk: str) -> bool:
    lowered = risk.lower()
    return any(
        marker in lowered
        for marker in (
            "protected_path",
            "approval",
            "policy",
            "blocked",
            "permission",
            "requires confirmation",
            "requires clarification",
            "clarification required",
            "user input required",
        )
    )


def is_turn_local_repair_risk(risk: str) -> bool:
    lowered = risk.lower()
    return any(
        marker in lowered
        for marker in (
            "malformed tool_call decision",
            "missing payload.tool_id",
            "provider returned invalid json",
            "invalid_provider_json",
            "malformed_tool_call",
            "unsupported decision",
            "unsupported_decision",
        )
    )


def _risk_mentions_tool_failure(risk: str, tool_id: str) -> bool:
    if tool_id not in risk:
        return False
    lowered = risk.lower()
    return any(
        marker in lowered
        for marker in (
            "reported error",
            "request was failed",
            "request was denied",
            "tool request denied previously",
            "tool feedback remains unresolved",
        )
    )
