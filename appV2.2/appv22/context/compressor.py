from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from appv22.context.budget import estimate_chars
from appv22.context.summaries import structured_summary


SUMMARY_KEYS = ("goals", "decisions", "progress", "blockers", "evidence_refs")
PRESERVED_CONTEXT_SECTIONS = ("agent", "state", "skills", "tools", "tool_definitions", "world", "selection")


def _summary_message(summary: dict[str, list[Any]], *, content: str) -> dict[str, Any]:
    return {
        "role": "system",
        "name": "context_summary",
        "content": _summary_content(summary, fallback=content),
        "summary": summary,
    }


def _summary_content(summary: dict[str, list[Any]], *, fallback: str) -> str:
    lines = [fallback]
    evidence_refs = [str(ref) for ref in summary.get("evidence_refs", []) if ref]
    if evidence_refs:
        lines.append(f"Available evidence_refs: {', '.join(evidence_refs[:8])}")
    if fallback == "Context summary.":
        return "\n".join(lines)
    progress = [str(item) for item in summary.get("progress", []) if item]
    if progress:
        lines.append("Relevant progress/evidence:")
        lines.extend(f"- {item[:240]}" for item in progress[-6:])
    blockers = [str(item) for item in summary.get("blockers", []) if item]
    if blockers:
        lines.append("Active blockers:")
        lines.extend(f"- {item[:240]}" for item in blockers[-4:])
    return "\n".join(lines)


def _normal_summary(summary: dict[str, Any]) -> dict[str, list[Any]]:
    return {key: list(summary.get(key, [])) for key in SUMMARY_KEYS}


def _bounded_summary(summary: dict[str, list[Any]], *, max_items: int, max_item_chars: int) -> dict[str, list[Any]]:
    bounded: dict[str, list[Any]] = {}
    for key in SUMMARY_KEYS:
        values = summary.get(key, [])
        if key == "evidence_refs":
            keep_count = max(max_items, 8)
            bounded[key] = [str(value) for value in values[-keep_count:] if value]
            continue
        if max_items <= 0:
            values = []
        else:
            if key == "progress":
                recent = list(values[-max_items:])
                evidence_progress = [
                    value for value in values if str(value).startswith("toolres_")
                ][-4:]
                merged_values: list[Any] = []
                for value in [*evidence_progress, *recent]:
                    if value not in merged_values:
                        merged_values.append(value)
                values = merged_values
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
        (3, 80),
        (2, 80),
        (1, 80),
        (1, 60),
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


def _minimal_preserved_context_section(message: dict[str, Any]) -> dict[str, Any]:
    section = message.get("section")
    payload = message.get("payload")
    minimal = deepcopy(message)
    if section == "agent" and isinstance(payload, dict):
        minimal["payload"] = {
            "mode": payload.get("mode"),
            "request": str(payload.get("request", ""))[:400],
            "mode_contract": tuple(str(item)[:160] for item in payload.get("mode_contract", ())[:2])
            if isinstance(payload.get("mode_contract"), (list, tuple))
            else (),
        }
    elif section == "state" and isinstance(payload, dict):
        context_summary = payload.get("context_summary") if isinstance(payload.get("context_summary"), dict) else {}
        minimal["payload"] = {
            "mode": payload.get("mode"),
            "context_summary": _bounded_summary(_normal_summary(context_summary), max_items=2, max_item_chars=80),
        }
    elif section == "skills" and isinstance(payload, list):
        minimal["payload"] = [
            {
                "skill_id": skill.get("skill_id"),
                "summary": str(skill.get("summary", ""))[:120],
                "tool_ids": tuple(_edge_items(list(skill.get("tool_ids", ())), limit=24))
                if isinstance(skill.get("tool_ids"), (list, tuple))
                else (),
                "observation_contract": skill.get("observation_contract"),
                "instructions": tuple(str(item)[:120] for item in skill.get("instructions", ())[:2])
                if isinstance(skill.get("instructions"), (list, tuple))
                else (),
            }
            for skill in payload[:4]
            if isinstance(skill, dict)
        ]
    elif section == "tools" and isinstance(payload, list):
        minimal["payload"] = [str(tool_id)[:160] for tool_id in _edge_items(payload, limit=32)]
    elif section == "tool_definitions" and isinstance(payload, list):
        minimal["payload"] = [
            {
                "tool_id": tool.get("tool_id"),
                "category": tool.get("category"),
                "risk_level": tool.get("risk_level"),
                "argument_schema": tool.get("argument_schema"),
            }
            for tool in _edge_items(payload, limit=24)
            if isinstance(tool, dict)
        ]
    elif section == "world" and isinstance(payload, dict):
        minimal["payload"] = _compact_world_payload(payload)
    elif section == "selection" and isinstance(payload, dict):
        minimal["payload"] = {
            "mode": payload.get("mode"),
            "selected_tools": _edge_items(payload.get("selected_tools", []), limit=32)
            if isinstance(payload.get("selected_tools"), list)
            else [],
            "selected_skills": payload.get("selected_skills", [])[:8]
            if isinstance(payload.get("selected_skills"), list)
            else [],
        }
    else:
        minimal["payload"] = {}
    minimal["content"] = f"{section}: compacted preserved context"
    return minimal


def _edge_items(items: list[Any], *, limit: int) -> list[Any]:
    if limit <= 0 or len(items) <= limit:
        return list(items)
    head_count = limit // 2
    tail_count = limit - head_count
    return [*items[:head_count], *items[-tail_count:]]


def _hard_budget_candidate(
    head: list[dict[str, Any]],
    preserved_middle: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    summary: dict[str, list[Any]],
    *,
    budget: int,
) -> list[dict[str, Any]]:
    candidate = _fit_summary_candidate(head, [*preserved_middle, *tail], summary, budget=budget)
    if estimate_chars(candidate) <= budget:
        return candidate

    minimal_preserved = [_minimal_preserved_context_section(message) for message in preserved_middle]
    minimal_preserved = _prioritize_referenced_tool_sections(minimal_preserved, summary)
    candidate = _fit_summary_candidate(head, [*minimal_preserved, *tail], summary, budget=budget)
    if estimate_chars(candidate) <= budget:
        return candidate

    by_section = {
        message.get("section"): message
        for message in minimal_preserved
        if message.get("section") in {"state", "skills", "tool_definitions", "world", "selection"}
    }
    head_attempts = (
        head,
        [{**head[0], "content": str(head[0].get("content", ""))[:200], "payload": {}}] if head else [],
    )
    section_attempts = (
        ("state", "skills", "tool_definitions", "world", "selection"),
        ("skills", "tool_definitions", "world", "selection"),
        ("state", "skills", "tool_definitions", "world"),
        ("skills", "tool_definitions", "world"),
        ("tool_definitions", "world"),
        ("world",),
        ("skills",),
        ("tool_definitions",),
        (),
    )
    for head_candidate in head_attempts:
        for sections in section_attempts:
            section_messages = [by_section[section] for section in sections if section in by_section]
            candidate = _fit_summary_candidate(
                head_candidate,
                [*section_messages, *tail],
                _bounded_summary(summary, max_items=1, max_item_chars=40),
                budget=budget,
            )
            if estimate_chars(candidate) <= budget:
                return candidate

    minimal_summary = _bounded_summary(summary, max_items=0, max_item_chars=0)
    minimal_summary["evidence_refs"] = [str(value)[:120] for value in summary.get("evidence_refs", [])[-2:]]
    candidate = _summary_candidate(head, tail, minimal_summary, content="Context summary.")
    if estimate_chars(candidate) > budget and tail:
        for content_limit in (1200, 800, 480, 240, 120):
            compacted_tail = _compact_tail_messages(tail, content_limit=content_limit)
            candidate = _summary_candidate(head, compacted_tail, minimal_summary, content="Context summary.")
            if estimate_chars(candidate) <= budget:
                return candidate
        tail = _compact_tail_messages(tail, content_limit=120)
        candidate = _summary_candidate(head, tail, minimal_summary, content="Context summary.")
    if estimate_chars(candidate) > budget and head:
        head = [{**head[0], "content": str(head[0].get("content", ""))[:200], "payload": {}}]
        candidate = _summary_candidate(head, tail, minimal_summary, content="Context summary.")
    if estimate_chars(candidate) > budget and tail:
        for content_limit in (80, 40):
            compacted_tail = _compact_tail_messages(tail, content_limit=content_limit)
            candidate = _summary_candidate(head, compacted_tail, minimal_summary, content="Context summary.")
            if estimate_chars(candidate) <= budget:
                return candidate
    if estimate_chars(candidate) > budget:
        candidate = [_summary_message(minimal_summary, content="Context summary.")]
    return candidate


def _compact_tail_messages(tail: list[dict[str, Any]], *, content_limit: int) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in tail:
        item = deepcopy(message)
        content = str(item.get("content", ""))
        if len(content) > content_limit:
            item["content"] = (
                content[:content_limit]
                + f"\n[latest message compacted from {len(content)} chars; head preserved]"
            )
        compacted.append(item)
    return compacted


def _prioritize_referenced_tool_sections(
    messages: list[dict[str, Any]],
    summary: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    referenced_tool_ids = _referenced_tool_ids(messages, summary)
    if not referenced_tool_ids:
        return messages
    prioritized: list[dict[str, Any]] = []
    for message in messages:
        section = message.get("section")
        payload = message.get("payload")
        updated = deepcopy(message)
        if section == "tools" and isinstance(payload, list):
            updated["payload"] = _ordered_tool_ids(payload, referenced_tool_ids)[:32]
        elif section == "selection" and isinstance(payload, dict):
            updated["payload"] = dict(payload)
            selected_tools = payload.get("selected_tools")
            if isinstance(selected_tools, list):
                updated["payload"]["selected_tools"] = _ordered_tool_ids(selected_tools, referenced_tool_ids)[:32]
        elif section == "skills" and isinstance(payload, list):
            skills: list[dict[str, Any]] = []
            for skill in payload:
                if not isinstance(skill, dict):
                    continue
                compacted_skill = dict(skill)
                tool_ids = skill.get("tool_ids")
                if isinstance(tool_ids, (list, tuple)):
                    compacted_skill["tool_ids"] = tuple(_ordered_tool_ids(list(tool_ids), referenced_tool_ids)[:32])
                skills.append(compacted_skill)
            updated["payload"] = skills
        elif section == "tool_definitions" and isinstance(payload, list):
            referenced = [
                _ultra_compact_tool_definition(tool)
                for tool in payload
                if isinstance(tool, dict) and tool.get("tool_id") in referenced_tool_ids
            ]
            if referenced:
                updated["payload"] = referenced
            else:
                updated["payload"] = [
                    _ultra_compact_tool_definition(tool)
                    for tool in payload[:8]
                    if isinstance(tool, dict) and tool.get("tool_id")
                ]
        updated["content"] = f"{section}: {json.dumps(updated.get('payload'), sort_keys=True, default=str)}"
        prioritized.append(updated)
    return prioritized


def _referenced_tool_ids(messages: list[dict[str, Any]], summary: dict[str, list[Any]]) -> list[str]:
    haystack = " ".join(
        str(item)
        for key in SUMMARY_KEYS
        for item in summary.get(key, [])
    )
    tool_ids: list[str] = []
    for message in messages:
        payload = message.get("payload")
        if message.get("section") == "tool_definitions" and isinstance(payload, list):
            for tool in payload:
                if not isinstance(tool, dict):
                    continue
                tool_id = tool.get("tool_id")
                if isinstance(tool_id, str) and tool_id and tool_id in haystack and tool_id not in tool_ids:
                    tool_ids.append(tool_id)
        elif message.get("section") in {"tools", "selection", "skills"}:
            candidates: list[Any] = []
            if isinstance(payload, list):
                candidates = payload
            elif isinstance(payload, dict):
                selected_tools = payload.get("selected_tools")
                if isinstance(selected_tools, list):
                    candidates = selected_tools
            for candidate in candidates:
                if isinstance(candidate, str) and candidate in haystack and candidate not in tool_ids:
                    tool_ids.append(candidate)
    return tool_ids


def _ordered_tool_ids(tool_ids: list[Any], referenced_tool_ids: list[str]) -> list[str]:
    normalized = [str(tool_id) for tool_id in tool_ids if isinstance(tool_id, str) and tool_id]
    ordered: list[str] = []
    for tool_id in referenced_tool_ids:
        if tool_id in normalized and tool_id not in ordered:
            ordered.append(tool_id)
    for tool_id in normalized:
        if tool_id not in ordered:
            ordered.append(tool_id)
    return ordered


def _ultra_compact_tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    compacted = {
        "tool_id": tool.get("tool_id"),
        "category": tool.get("category"),
        "risk_level": tool.get("risk_level"),
        "argument_schema": tool.get("argument_schema"),
    }
    guidance = str(tool.get("guidance", ""))
    if guidance:
        compacted["guidance"] = guidance[:160]
    return compacted


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
        def compact_instructions(skill: dict[str, Any]) -> tuple[str, ...]:
            instructions = skill.get("instructions", ())
            if not isinstance(instructions, (list, tuple)):
                return ()
            return tuple(str(item)[:240] for item in instructions[:4])

        compacted["payload"] = [
            {
                "skill_id": skill.get("skill_id"),
                "extension_id": skill.get("extension_id"),
                "summary": str(skill.get("summary", ""))[:240],
                "tool_ids": skill.get("tool_ids", ()),
                "observation_contract": skill.get("observation_contract"),
                "instructions": compact_instructions(skill),
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
    elif section == "tool_definitions" and isinstance(payload, list):
        compacted["payload"] = [
            {
                "tool_id": tool.get("tool_id"),
                "category": tool.get("category"),
                "risk_level": tool.get("risk_level"),
                "argument_schema": tool.get("argument_schema"),
                "guidance": str(tool.get("guidance", ""))[:240],
            }
            for tool in payload
            if isinstance(tool, dict) and tool.get("tool_id")
        ]
    elif section == "world" and isinstance(payload, dict):
        compacted["payload"] = _compact_world_payload(payload)
    compacted["content"] = f"{section}: {json.dumps(compacted.get('payload'), sort_keys=True, default=str)}"
    return compacted


def _compact_world_payload(payload: dict[str, Any]) -> dict[str, Any]:
    world_refs = payload.get("world_refs")
    if not isinstance(world_refs, dict):
        return {"world_refs": {}}
    compacted_refs: dict[str, Any] = {}
    for ref_id, ref in world_refs.items():
        if not isinstance(ref_id, str) or not isinstance(ref, dict):
            continue
        compacted_ref = {
            "ref_id": ref.get("ref_id", ref_id),
            "kind": ref.get("kind"),
            "summary": str(ref.get("summary", ""))[:240],
        }
        ref_payload = ref.get("payload")
        if isinstance(ref_payload, dict):
            compacted_ref["payload"] = _compact_world_ref_payload(ref_payload)
        compacted_refs[ref_id] = compacted_ref
    return {"world_refs": compacted_refs}


def _compact_world_ref_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    files = payload.get("files")
    if isinstance(files, list):
        if len(files) <= 30:
            compacted["files"] = [str(item)[:240] for item in files]
        else:
            compacted["file_count"] = len(files)
            compacted["important_files"] = _important_paths(files)
            compacted["top_level_file_groups"] = _top_level_groups(files)
    directories = payload.get("directories")
    if isinstance(directories, list):
        if len(directories) <= 30:
            compacted["directories"] = [str(item)[:240] for item in directories]
        else:
            compacted["directory_count"] = len(directories)
            compacted["important_directories"] = _important_paths(directories)
            compacted["top_level_directory_groups"] = _top_level_groups(directories)
    errors = payload.get("errors")
    if isinstance(errors, list):
        compacted["errors"] = [str(item)[:240] for item in errors[:20]]
    text_previews = payload.get("text_previews")
    if isinstance(text_previews, dict):
        preview_items = list(text_previews.items())
        large_repo = isinstance(files, list) and len(files) > 30
        if large_repo:
            preview_items = [
                (path, content)
                for path, content in preview_items
                if _is_important_path(str(path))
            ][:8]
        elif len(preview_items) > 30:
            preview_items = [
                (path, content)
                for path, content in preview_items
                if _is_important_path(str(path))
            ][:30]
        if preview_items:
            preview_char_budget = 360 if large_repo else 700
            compacted["text_previews"] = {
                str(path)[:240]: str(content)[:preview_char_budget]
                for path, content in preview_items[:30]
            }
    for key, value in payload.items():
        if key in compacted or key in {"files", "directories", "errors", "text_previews"}:
            continue
        compacted[str(key)[:120]] = _compact_generic_payload(value)
    return compacted


def _compact_generic_payload(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:700]
    if isinstance(value, str):
        return value[:700]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(key)[:120]: _compact_generic_payload(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [_compact_generic_payload(item, depth=depth + 1) for item in list(value)[:20]]
    return str(value)[:700]


def _important_paths(paths: list[Any]) -> list[str]:
    return [str(path)[:240] for path in paths if _is_important_path(str(path))][:30]


def _is_important_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return (
        normalized == "README.md"
        or normalized.startswith(
            (
                "docs/",
                "notes/",
                "meetings/",
                "inbox/",
                "risks/",
                "finance/",
                "people/",
                "vendors/",
            )
        )
    )


def _top_level_groups(paths: list[Any], *, limit: int = 12) -> dict[str, int]:
    groups: dict[str, int] = {}
    for raw_path in paths:
        normalized = str(raw_path).replace("\\", "/").lstrip("/")
        if not normalized:
            continue
        group = normalized.split("/", 1)[0]
        groups[group] = groups.get(group, 0) + 1
    return dict(sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:limit])


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

        summary = _normal_summary(structured_summary(middle, deepcopy(previous_summary)))
        return _hard_budget_candidate(
            head,
            preserved_middle,
            tail,
            summary,
            budget=self.max_chars if preserved_middle else min(self.max_chars, int(self.max_chars * self.threshold)),
        )
