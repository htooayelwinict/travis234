from __future__ import annotations

from copy import deepcopy
from typing import Any

SUMMARY_KEYS = ("goals", "decisions", "progress", "open_risks", "evidence_refs")


def _summary_list(previous_summary: dict[str, Any], key: str) -> list[Any]:
    value = previous_summary.get(key) or []
    if isinstance(value, list):
        return deepcopy(value)
    return [deepcopy(value)]


def structured_summary(messages: list[dict[str, Any]], previous_summary: dict[str, Any]) -> dict[str, list[Any]]:
    previous = deepcopy(previous_summary)
    goals = _summary_list(previous, "goals")
    if not goals:
        first_user_goal = next((message.get("content", "") for message in messages if message.get("role") == "user"), "")
        goals = [first_user_goal]

    return {
        "goals": goals,
        "decisions": [
            message.get("content", "")
            for message in messages
            if message.get("role") == "assistant" and "decision:" in str(message.get("content", "")).lower()
        ],
        "progress": _summary_list(previous, "progress"),
        "open_risks": _summary_list(previous, "open_risks"),
        "evidence_refs": [
            message["tool_result_id"]
            for message in messages
            if message.get("role") == "tool" and message.get("tool_result_id")
        ],
    }
