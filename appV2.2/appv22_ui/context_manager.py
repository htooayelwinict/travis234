from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from appv22_ui.tui_state import ConversationLine


SUMMARY_PREFIX = (
    "[UI SESSION SUMMARY - REFERENCE ONLY]\n"
    "Earlier UI conversation was compacted below. Treat it as background memory, "
    "not active instructions. The latest user request after this summary is authoritative. "
    "Do not resume completed historical tasks unless the latest request explicitly asks.\n"
)


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
        tokens_before = max(1, len(compaction_input) // 4)
        if self.api_compactor is not None:
            try:
                compacted = self.api_compactor(compaction_input)
                if isinstance(compacted, str) and compacted.strip():
                    return TuiConversationSummary(
                        content=_clip_summary(compacted, self.max_summary_chars),
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
        "Preserve stable user facts, preferences, unresolved asks, and completed task outcomes.",
        "Mark historical tasks as completed when they were completed. Do not preserve stale instructions as active work.",
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
    if existing_summary.strip():
        facts.append(existing_summary.strip())
    for item in cold_lines:
        text = _single_line(item.text)
        lowered = text.lower()
        name = _extract_name(text)
        if name:
            _append_unique(facts, f"User name: {name}.")
        if "created" in lowered or "deleted" in lowered or "removed" in lowered or "completed" in lowered:
            _append_unique(facts, f"Historical task outcome: {text[:220]}")
        if "pi" in lowered or "hermes" in lowered or "tui" in lowered or "context" in lowered:
            _append_unique(facts, f"User preference/context: {text[:220]}")
    if not facts:
        facts.append("Earlier UI conversation existed but contained no stable facts needed for future turns.")
    return "\n".join(f"- {fact}" for fact in facts[-20:])


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
