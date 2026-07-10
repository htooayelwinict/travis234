"""Adapters between persisted coding-session messages and the compressor."""

from __future__ import annotations

from collections.abc import Sequence

from appv231.agent.types import AgentMessage
from appv231.ai.types import AssistantMessage, TextContent, UserMessage, empty_usage, now_ms
from appv231.compaction.compressor import (
    COMPRESSED_SUMMARY_METADATA_KEY,
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
)

_READ_FILES_TAG = "read-files"
_MODIFIED_FILES_TAG = "modified-files"


def to_compressor_messages(messages: Sequence[AgentMessage]) -> list[AgentMessage]:
    adapted: list[AgentMessage] = []
    for index, message in enumerate(messages):
        if getattr(message, "role", None) != "compactionSummary":
            adapted.append(message)
            continue
        summary = compaction_summary_with_details(
            getattr(message, "summary", ""),
            getattr(message, "details", None),
        )
        text = f"{SUMMARY_PREFIX}\n{summary.strip()}\n\n{SUMMARY_END_MARKER}"
        timestamp = getattr(message, "timestamp", None) or now_ms()
        if _next_ordinary_role(messages, index + 1) == "user":
            envelope: AgentMessage = AssistantMessage(
                content=[TextContent(text=text)],
                api="compaction",
                provider="hermes",
                model="summary",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=timestamp,
            )
        else:
            envelope = UserMessage(content=[TextContent(text=text)], timestamp=timestamp)
        setattr(envelope, COMPRESSED_SUMMARY_METADATA_KEY, True)
        adapted.append(envelope)
    return adapted


def compaction_summary_with_details(summary: object, details: object) -> str:
    text = str(summary or "")
    if not isinstance(details, dict):
        return text
    sections: list[str] = []
    if f"<{_READ_FILES_TAG}>" not in text:
        section = _file_detail_section(_READ_FILES_TAG, details.get("readFiles"))
        if section:
            sections.append(section)
    if f"<{_MODIFIED_FILES_TAG}>" not in text:
        section = _file_detail_section(_MODIFIED_FILES_TAG, details.get("modifiedFiles"))
        if section:
            sections.append(section)
    if not sections:
        return text
    return text.rstrip() + "\n\n" + "\n\n".join(sections)


def _file_detail_section(tag: str, value: object) -> str:
    if not isinstance(value, list):
        return ""
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if path and path not in paths:
            paths.append(path)
    if not paths:
        return ""
    return f"<{tag}>\n" + "\n".join(paths) + f"\n</{tag}>"


def _next_ordinary_role(messages: Sequence[AgentMessage], start: int) -> str | None:
    for message in messages[start:]:
        role = getattr(message, "role", None)
        if role in {"user", "assistant", "toolResult"}:
            return "tool" if role == "toolResult" else role
    return None


__all__ = ["compaction_summary_with_details", "to_compressor_messages"]
