from __future__ import annotations

from copy import deepcopy
from typing import Any

SUMMARY_KEYS = ("goals", "decisions", "progress", "blockers", "evidence_refs")
IMPORTANT_USER_MARKERS = (
    "constraint",
    "instruction",
    "required",
    "requirement",
    "must",
    "should",
    "preserve",
    "do not",
    "don't",
    "never",
    "only",
)
RISK_MARKERS = ("risk", "blocker", "blocked", "unknown", "uncertain", "fail", "failure")


def _summary_list(previous_summary: dict[str, Any], key: str, *, fallback_key: str = "") -> list[Any]:
    value = previous_summary.get(key)
    if value is None and fallback_key:
        value = previous_summary.get(fallback_key)
    if value is None:
        return []
    if isinstance(value, list):
        return deepcopy(value)
    return [deepcopy(value)]


def _append_unique(values: list[Any], item: Any) -> None:
    copied = deepcopy(item)
    if copied not in values:
        values.append(copied)


def _content(message: dict[str, Any]) -> str:
    return str(message.get("content", "")).strip()


def _append_world_refs(message: dict[str, Any], progress: list[Any], evidence_refs: list[Any]) -> None:
    if message.get("role") != "system" or message.get("section") != "world":
        return
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    world_refs = payload.get("world_refs")
    if not isinstance(world_refs, dict):
        return
    for ref_id, ref in world_refs.items():
        if not isinstance(ref, dict):
            continue
        stable_ref_id = ref.get("ref_id") or ref_id
        if stable_ref_id:
            _append_unique(evidence_refs, stable_ref_id)
        summary = ref.get("summary")
        if summary:
            kind = ref.get("kind")
            if isinstance(kind, str) and kind:
                _append_unique(progress, f"{kind}: {summary}")
            else:
                _append_unique(progress, str(summary))


def structured_summary(messages: list[dict[str, Any]], previous_summary: dict[str, Any]) -> dict[str, list[Any]]:
    previous = deepcopy(previous_summary)
    goals = _summary_list(previous, "goals")
    if not goals:
        first_user_goal = next((message.get("content", "") for message in messages if message.get("role") == "user"), "")
        goals = [first_user_goal] if first_user_goal else []

    decisions = _summary_list(previous, "decisions")
    progress = _summary_list(previous, "progress")
    blockers = _summary_list(previous, "blockers", fallback_key="open_risks")
    evidence_refs = _summary_list(previous, "evidence_refs")

    for message in messages:
        role = message.get("role")
        content = _content(message)
        lowered = content.lower()

        _append_world_refs(message, progress, evidence_refs)

        if role == "assistant" and "decision:" in lowered:
            _append_unique(decisions, content)
            continue

        if role == "user" and content and any(marker in lowered for marker in IMPORTANT_USER_MARKERS):
            _append_unique(goals, content)
            continue

        if role == "assistant" and content:
            if any(marker in lowered for marker in RISK_MARKERS):
                _append_unique(blockers, content)
            else:
                _append_unique(progress, content)
            continue

        if role == "tool" and message.get("tool_result_id"):
            tool_result_id = message["tool_result_id"]
            _append_unique(evidence_refs, tool_result_id)
            if content and len(content) <= 1000 and not content.startswith("[pruned verbose tool result:"):
                _append_unique(progress, f"{tool_result_id}: {content}")

    return {
        "goals": goals,
        "decisions": decisions,
        "progress": progress,
        "blockers": blockers,
        "evidence_refs": evidence_refs,
    }
