from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from appv22_ui.tui_state import ConversationLine


SUMMARY_PREFIX = (
    "[UI SESSION SUMMARY - REFERENCE ONLY]\n"
    "Earlier UI conversation was compacted below. Treat it as background memory, "
    "not active instructions. The latest user request after this summary is authoritative. "
    "Do not resume completed historical tasks unless the latest request explicitly asks. "
    "If the latest request explicitly asks about prior UI/session events, answer from this reference summary and recent turns.\n"
)
EMPTY_FALLBACK_FACT = "Earlier UI conversation existed but contained no stable facts needed for future turns."


@dataclass
class TuiConversationSummary:
    content: str = ""
    tokens_before: int = 0
    compaction_count: int = 0
    source: str = "none"


class TuiContextManager:
    def __init__(
        self,
        *,
        max_hot_lines: int = 6,
        compact_after_lines: int = 12,
        max_summary_chars: int = 1800,
        api_compactor: Callable[[str], str] | None = None,
    ) -> None:
        self.max_hot_lines = max_hot_lines
        self.compact_after_lines = compact_after_lines
        self.max_summary_chars = max_summary_chars
        self.api_compactor = api_compactor

    def prepare_prompt(
        self,
        *,
        current_user_message: str,
        conversation: list[ConversationLine],
        existing_summary: str,
        compaction_count: int = 0,
    ) -> tuple[str, list[ConversationLine], TuiConversationSummary]:
        history = conversation[:-1] if conversation and conversation[-1].text == current_user_message else list(conversation)
        summary = TuiConversationSummary(
            content=existing_summary,
            compaction_count=compaction_count,
            source="existing" if existing_summary else "none",
        )
        hot_lines = list(history[-self.max_hot_lines :])
        if len(history) > self.compact_after_lines:
            cold_lines = history[: -self.max_hot_lines]
            summary = self._compact(existing_summary, cold_lines, compaction_count=compaction_count)
        prompt = self._build_prompt(current_user_message, hot_lines, summary.content)
        return prompt, hot_lines, summary

    def _compact(
        self,
        existing_summary: str,
        cold_lines: list[ConversationLine],
        *,
        compaction_count: int,
    ) -> TuiConversationSummary:
        compaction_input = _compaction_input(existing_summary, cold_lines)
        deterministic_summary = _fallback_summary(existing_summary, cold_lines)
        tokens_before = max(1, len(compaction_input) // 4)
        if self.api_compactor is not None:
            try:
                compacted = self.api_compactor(compaction_input)
                if isinstance(compacted, str) and compacted.strip():
                    return TuiConversationSummary(
                        content=_merge_reference_summary(
                            compacted,
                            deterministic_summary,
                            self.max_summary_chars,
                        ),
                        tokens_before=tokens_before,
                        compaction_count=compaction_count + 1,
                        source="api",
                    )
            except Exception:
                pass
        return TuiConversationSummary(
            content=_clip_summary(_fallback_summary(existing_summary, cold_lines), self.max_summary_chars),
            tokens_before=tokens_before,
            compaction_count=compaction_count + 1,
            source="fallback",
        )

    def _build_prompt(self, current_user_message: str, hot_lines: list[ConversationLine], summary: str) -> str:
        lines: list[str] = []
        if summary.strip():
            lines.extend([SUMMARY_PREFIX, summary.strip(), "--- END UI SESSION SUMMARY ---", ""])
        if hot_lines:
            lines.append("[RECENT UI TURNS]")
            for item in hot_lines:
                role = "user" if item.role == "user" else "assistant"
                lines.append(f"{role}: {_single_line(item.text)}")
            lines.append("")
        lines.extend(["[CURRENT USER REQUEST]", current_user_message])
        return "\n".join(lines)


def _compaction_input(existing_summary: str, cold_lines: list[ConversationLine]) -> str:
    lines = [
        "Summarize this UI session as reference-only memory.",
        "Preserve stable user facts, preferences, and Pi/Hermes/TUI context preferences.",
        "Preserve concrete historical tool-result facts such as protected_path, missing_file, denied, and failed markers.",
        "Do not infer task status from prose. Do not preserve stale instructions as active work.",
    ]
    if existing_summary:
        lines.extend(["Existing summary:", existing_summary])
    lines.append("Older UI turns:")
    for item in cold_lines:
        role = "user" if item.role == "user" else "assistant"
        lines.append(f"{role}: {_single_line(item.text)}")
    return "\n".join(lines)


def _fallback_summary(existing_summary: str, cold_lines: list[ConversationLine]) -> str:
    facts: list[str] = []
    if (
        existing_summary.strip()
        and not _is_empty_fallback_summary(existing_summary)
    ):
        _extend_existing_summary_facts(facts, existing_summary)
    for item in cold_lines:
        text = _single_line(item.text)
        lowered = text.lower()
        name = _extract_name(text)
        if name:
            _append_unique(facts, f"User name: {name}.")
        if "pi" in lowered or "hermes" in lowered or "tui" in lowered or "context" in lowered:
            _append_unique(facts, f"User preference/context: {text[:220]}")
        concrete_tool_fact = _concrete_tool_result_fact(text)
        if concrete_tool_fact:
            _append_unique(facts, concrete_tool_fact)
    if not facts:
        facts.append(EMPTY_FALLBACK_FACT)
    return "\n".join(f"- {fact}" for fact in facts[-20:])


def _has_unsafe_active_claim(summary: str) -> bool:
    lowered = summary.lower()
    return any(
        marker in lowered
        for marker in (
            "unresolved",
            "not yet",
            "no changes have been made",
            "latest user request",
            "current user request",
            "new session",
            "read tools were denied",
            "read tools were repeatedly denied",
            "no useful read tools",
            "tool was denied",
            "tools were denied",
            "unable to analyze",
            "cannot analyze",
            "could not analyze",
        )
    )


def _concrete_tool_result_fact(text: str) -> str:
    lowered = text.lower()
    markers = (
        "protected_path:",
        "missing_file:",
        "copy_requires_preserve_source:",
        "existing_file_requires_overwrite:",
        "old_text_not_unique:",
        "reported error:",
        " status denied",
        " status failed",
    )
    if not any(marker in lowered for marker in markers):
        return _plain_missing_file_fact(text)
    return f"Historical tool result: {text[:260]}"


def _plain_missing_file_fact(text: str) -> str:
    match = re.search(
        r"\b((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+)\s+(?:is|was)(?:\s+\w+){0,3}\s+missing\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return f"Historical tool result: missing_file:{match.group(1)}"


def _is_empty_fallback_summary(summary: str) -> bool:
    normalized = _single_line(summary).strip()
    while normalized.startswith("- "):
        normalized = normalized[2:].strip()
    return normalized == EMPTY_FALLBACK_FACT


def _extend_existing_summary_facts(facts: list[str], summary: str) -> None:
    for line in summary.splitlines():
        fact = _single_line(line).strip()
        while fact.startswith("- "):
            fact = fact[2:].strip()
        if fact == "Deterministic reference ledger:":
            continue
        if fact and not _has_unsafe_active_claim(fact):
            _append_unique(facts, fact)


def _merge_reference_summary(api_summary: str, deterministic_summary: str, max_chars: int) -> str:
    summary = _sanitize_api_tool_marker_claims(api_summary, deterministic_summary)
    facts: list[str] = []
    if (
        deterministic_summary.strip()
        and not _is_empty_fallback_summary(deterministic_summary)
    ):
        _extend_existing_summary_facts(facts, deterministic_summary)
    if facts:
        lowered_summary = summary.lower()
        missing_facts = [
            fact for fact in facts
            if fact.lower() not in lowered_summary
        ]
        if missing_facts:
            summary = "\n".join(
                [
                    summary,
                    "Deterministic reference ledger:",
                    *(f"- {fact}" for fact in missing_facts),
                ]
            )
    return _clip_summary(summary, max_chars)


def _sanitize_api_tool_marker_claims(api_summary: str, deterministic_summary: str) -> str:
    summary = api_summary.strip()
    if not summary:
        return summary
    summary = _sanitize_api_reference_control_language(summary)
    supported_protected = _tool_marker_paths(deterministic_summary, "protected_path")
    supported_missing = _tool_marker_paths(deterministic_summary, "missing_file")
    summary = _drop_unsupported_marker_paths(summary, "protected_path", supported_protected)
    summary = _drop_unsupported_marker_paths(summary, "missing_file", supported_missing)
    if "protected_path" in summary.lower() and not supported_protected:
        summary = _drop_tool_marker_clauses(summary, "protected_path")
    if "missing_file" in summary.lower() and not supported_missing:
        summary = _drop_tool_marker_clauses(summary, "missing_file")
    return _single_line(summary)


def _sanitize_api_reference_control_language(summary: str) -> str:
    cleaned = re.sub(
        r"\b(?:no active tasks remain;\s*)?latest (?:user )?request "
        r"(?:supersedes|overrides|replaces) (?:all )?(?:prior|previous|earlier) instructions\b",
        "latest user request remains authoritative",
        summary,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:prior|previous|earlier) instructions (?:are|were) "
        r"(?:superseded|overridden|replaced) by the latest (?:user )?request\b",
        "latest user request remains authoritative",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def _tool_marker_paths(text: str, marker: str) -> set[str]:
    return {
        match.group(1).strip(".,);]")
        for match in re.finditer(
            rf"\b{re.escape(marker)}:([^\s,;)]+)",
            text,
            flags=re.IGNORECASE,
        )
    }


def _drop_tool_marker_clauses(text: str, marker: str) -> str:
    cleaned = re.sub(
        rf"(?:,\s*|;\s*|\band\s+)?[^;\n,]*\b{re.escape(marker)}\b[^;\n,]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*(?:and\s+)?([.;])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,")


def _drop_unsupported_marker_paths(text: str, marker: str, supported_paths: set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        path = match.group(1).strip(".,);]")
        if path in supported_paths:
            return match.group(0)
        return ""

    cleaned = re.sub(
        rf"\b{re.escape(marker)}:([^\s,;)]+)",
        replace,
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+(?:and|or)\s+([.;,])", r"\1", cleaned)
    cleaned = re.sub(r"(?:,\s*){2,}", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned


def _extract_name(text: str) -> str:
    match = re.search(r"\bmy name is\s+([A-Za-z][A-Za-z0-9_-]{0,40})", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip().capitalize()


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


def _clip_summary(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 15)].rstrip() + "...<truncated>"
