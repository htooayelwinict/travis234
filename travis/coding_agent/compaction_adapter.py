"""Adapters between persisted coding-session messages and the compressor."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from travis.agent.types import AgentMessage
from travis.ai.types import AssistantMessage, TextContent, UserMessage, empty_usage, now_ms
from travis.compaction.compressor import (
    COMPRESSED_SUMMARY_METADATA_KEY,
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
)

if TYPE_CHECKING:
    from travis.coding_agent.process_context import ProcessContextRecord

_READ_FILES_TAG = "read-files"
_MODIFIED_FILES_TAG = "modified-files"
_MANAGED_PROCESSES_TAG = "managed-processes"
_PROCESS_ID = re.compile(r"^proc_[0-9a-f]{32}$")
_PROCESS_STATUSES = frozenset(
    {
        "starting",
        "running",
        "stopping",
        "draining",
        "exited",
        "timed_out",
        "terminated",
        "failed",
        "unavailable",
    }
)


def merge_process_compaction_details(
    details: object,
    records: Sequence[ProcessContextRecord],
) -> dict[str, object] | None:
    merged = dict(details) if isinstance(details, Mapping) else {}
    serialized = [record.as_compaction_details() for record in records[:16]]
    if serialized:
        merged["managedProcesses"] = serialized
    return merged or None


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
                provider="travis",
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
    if f"<{_MANAGED_PROCESSES_TAG}>" not in text:
        section = _process_detail_section(details.get("managedProcesses"))
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


def _process_detail_section(value: object) -> str:
    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    for item in value[:16]:
        if not isinstance(item, Mapping):
            continue
        session_id = item.get("sessionId")
        status = item.get("status")
        cursor = _nonnegative_int(item.get("cursor"))
        output_size = _nonnegative_int(item.get("outputSize"))
        if (
            not isinstance(session_id, str)
            or _PROCESS_ID.fullmatch(session_id) is None
            or not isinstance(status, str)
            or status not in _PROCESS_STATUSES
            or cursor is None
            or output_size is None
        ):
            continue
        fields = [
            session_id,
            f"status={status}",
            f"cursor={cursor}",
            f"outputSize={output_size}",
        ]
        exit_code = item.get("exitCode")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            fields.append(f"exitCode={exit_code}")
        if item.get("durableOutput") is True:
            fields.append("durableOutput=true")
        lines.append(" ".join(fields))
    if not lines:
        return ""
    return f"<{_MANAGED_PROCESSES_TAG}>\n" + "\n".join(lines) + f"\n</{_MANAGED_PROCESSES_TAG}>"


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _next_ordinary_role(messages: Sequence[AgentMessage], start: int) -> str | None:
    for message in messages[start:]:
        role = getattr(message, "role", None)
        if role in {"user", "assistant", "toolResult"}:
            return "tool" if role == "toolResult" else role
    return None


__all__ = [
    "compaction_summary_with_details",
    "merge_process_compaction_details",
    "to_compressor_messages",
]
