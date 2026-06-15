from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from appv22.context.budget import estimate_chars
from appv22.context.summaries import structured_summary


SUMMARY_KEYS = ("goals", "decisions", "progress", "open_risks", "evidence_refs")
PRESERVED_CONTEXT_SECTIONS = ("agent", "state", "skills", "tools", "selection")


def _summary_message(summary: dict[str, list[Any]], *, content: str) -> dict[str, Any]:
    return {
        "role": "system",
        "name": "context_summary",
        "content": content,
        "summary": summary,
    }


def _normal_summary(summary: dict[str, Any]) -> dict[str, list[Any]]:
    return {key: list(summary.get(key, [])) for key in SUMMARY_KEYS}


def _bounded_summary(summary: dict[str, list[Any]], *, max_items: int, max_item_chars: int) -> dict[str, list[Any]]:
    bounded: dict[str, list[Any]] = {}
    for key in SUMMARY_KEYS:
        values = summary.get(key, [])
        if max_items <= 0:
            values = []
        else:
            values = values[-max_items:]
        if max_item_chars <= 0:
            bounded[key] = []
        else:
            bounded[key] = [str(value)[:max_item_chars] for value in values]
    return bounded


def _summary_candidate(
    head: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    summary: dict[str, list[Any]],
    *,
    content: str,
) -> list[dict[str, Any]]:
    return [*head, _summary_message(summary, content=content), *tail]


def _fit_summary_candidate(
    head: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    summary: dict[str, list[Any]],
    *,
    budget: int,
) -> list[dict[str, Any]]:
    rich_content = "Structured context summary injected."
    candidate = _summary_candidate(head, tail, summary, content=rich_content)
    if estimate_chars(candidate) <= budget:
        return candidate

    for max_items, max_item_chars in (
        (8, 160),
        (6, 120),
        (4, 80),
        (3, 60),
        (2, 40),
        (1, 24),
        (1, 12),
    ):
        bounded = _bounded_summary(summary, max_items=max_items, max_item_chars=max_item_chars)
        candidate = _summary_candidate(head, tail, bounded, content=rich_content)
        if estimate_chars(candidate) <= budget:
            return candidate

    minimal = _bounded_summary(summary, max_items=0, max_item_chars=0)
    minimal["progress"] = [str(value)[:80] for value in summary.get("progress", [])[-1:]]
    minimal["evidence_refs"] = [str(value)[:120] for value in summary.get("evidence_refs", [])[-2:]]
    candidate = _summary_candidate(head, tail, minimal, content="Context summary.")
    if estimate_chars(candidate) <= budget:
        return candidate
    return candidate


def _is_preserved_context_section(message: dict[str, Any]) -> bool:
    return (
        message.get("name") == "provider_context_section"
        and message.get("section") in PRESERVED_CONTEXT_SECTIONS
    )


def _compact_preserved_context_section(message: dict[str, Any]) -> dict[str, Any]:
    compacted = deepcopy(message)
    section = compacted.get("section")
    payload = compacted.get("payload")
    if section == "skills" and isinstance(payload, list):
        compacted["payload"] = [
            {
                "skill_id": skill.get("skill_id"),
                "extension_id": skill.get("extension_id"),
                "summary": str(skill.get("summary", ""))[:240],
                "tool_ids": skill.get("tool_ids", ()),
                "observation_contract": skill.get("observation_contract"),
            }
            for skill in payload
            if isinstance(skill, dict) and skill.get("skill_id")
        ]
    elif section == "selection" and isinstance(payload, dict):
        compacted["payload"] = {
            "mode": payload.get("mode"),
            "selected_tools": payload.get("selected_tools", []),
            "selected_skills": payload.get("selected_skills", []),
        }
    compacted["content"] = f"{section}: {json.dumps(compacted.get('payload'), sort_keys=True, default=str)}"
    return compacted


class AgentContextCompressor:
    def __init__(self, *, max_chars: int, threshold: float = 0.50) -> None:
        self.max_chars = max_chars
        self.threshold = threshold

    def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        previous_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        copied = deepcopy(messages)
        if estimate_chars(copied) <= int(self.max_chars * self.threshold):
            return copied
        if not copied:
            return copied

        head = copied[:1]
        tail = copied[-1:] if len(copied) > 1 else []
        middle = copied[1:-1] if len(copied) > 1 else []
        preserved_middle = [
            _compact_preserved_context_section(message)
            for message in middle
            if _is_preserved_context_section(message)
        ]
        summarizable_middle = [message for message in middle if not _is_preserved_context_section(message)]
        for message in summarizable_middle:
            if message.get("role") == "tool" and len(str(message.get("content", ""))) > 1000:
                message["content"] = f"[pruned verbose tool result:{message.get('tool_result_id', 'unknown')}]"

        summary = _normal_summary(structured_summary(summarizable_middle, deepcopy(previous_summary)))
        return _fit_summary_candidate(
            head,
            [*preserved_middle, *tail],
            summary,
            budget=self.max_chars if preserved_middle else min(self.max_chars, int(self.max_chars * self.threshold)),
        )
