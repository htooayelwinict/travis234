from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from appv22.context.budget import estimate_chars
from appv22.context.summaries import structured_summary


SUMMARY_KEYS = ("goals", "decisions", "progress", "open_risks", "evidence_refs")
PRESERVED_CONTEXT_SECTIONS = ("agent", "state", "skills", "tools", "world", "selection")


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
    open_risks = [str(item) for item in summary.get("open_risks", []) if item]
    if open_risks:
        lines.append("Open risks:")
        lines.extend(f"- {item[:240]}" for item in open_risks[-4:])
    return "\n".join(lines)


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
    directories = payload.get("directories")
    if isinstance(directories, list):
        if len(directories) <= 30:
            compacted["directories"] = [str(item)[:240] for item in directories]
        else:
            compacted["directory_count"] = len(directories)
            compacted["important_directories"] = _important_paths(directories)
    errors = payload.get("errors")
    if isinstance(errors, list):
        compacted["errors"] = [str(item)[:240] for item in errors[:20]]
    text_previews = payload.get("text_previews")
    if isinstance(text_previews, dict):
        preview_items = list(text_previews.items())
        if len(preview_items) > 30:
            preview_items = [
                (path, content)
                for path, content in preview_items
                if _is_important_path(str(path))
            ][:30]
        compacted["text_previews"] = {
            str(path)[:240]: str(content)[:700]
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
        or normalized.startswith(("docs/", "notes/", "risks/", "finance/", "people/", "vendors/"))
    )


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
        return _fit_summary_candidate(
            head,
            [*preserved_middle, *tail],
            summary,
            budget=self.max_chars if preserved_middle else min(self.max_chars, int(self.max_chars * self.threshold)),
        )
