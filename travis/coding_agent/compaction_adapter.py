"""Adapters between persisted coding-session messages and the compressor."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

from travis.agent.types import AgentMessage
from travis.ai.types import AssistantMessage, TextContent, UserMessage, empty_usage, now_ms
from travis.compaction.compressor import (
    COMPRESSED_SUMMARY_METADATA_KEY,
    LEGACY_SUMMARY_PREFIX,
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
    estimate_tokens,
)

if TYPE_CHECKING:
    from travis.coding_agent.process_context import ProcessContextRecord
    from travis.coding_agent.session_store import SessionStore


class CompactionSessionState(Protocol):
    messages: list[AgentMessage]
    thinking_level: str


@dataclass
class CompactionStartEvent:
    reason: str
    type: str = "compaction_start"


@dataclass
class CompactionEndEvent:
    reason: str
    result: object | None
    aborted: bool
    will_retry: bool
    error_message: str | None = None
    type: str = "compaction_end"

    @property
    def willRetry(self) -> bool:
        return self.will_retry

    @property
    def errorMessage(self) -> str | None:
        return self.error_message


class SessionCompactionAdapter:
    """Owns compaction lifecycle events and durable session application."""

    def __init__(
        self,
        *,
        session_store: SessionStore | None,
        state: CompactionSessionState,
        process_context: object | None,
        emit: Callable[[object], None],
        set_session_name: Callable[[str | None], None],
    ) -> None:
        self._session_store = session_store
        self._state = state
        self._process_context = process_context
        self._emit = emit
        self._set_session_name = set_session_name
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def messages(self) -> list[AgentMessage]:
        return self._state.messages

    def begin(self, reason: str) -> None:
        self._running = True
        self._emit(CompactionStartEvent(reason=reason))

    def end(
        self,
        *,
        reason: str,
        result: object | None,
        aborted: bool,
        will_retry: bool,
        error_message: str | None = None,
    ) -> None:
        try:
            self._emit(
                CompactionEndEvent(
                    reason=reason,
                    result=result,
                    aborted=aborted,
                    will_retry=will_retry,
                    error_message=error_message,
                )
            )
        finally:
            self._running = False

    def apply_manual_status(self, status, source_messages: Sequence[AgentMessage]):
        context_entry_ids = self._session_context_message_entry_ids()
        if self._session_store is not None and status.compressed:
            first_kept = self._first_kept_entry_id(status, context_entry_ids, fallback_to_leaf=True)
            summary = status.summary or extract_compaction_summary(status.messages)
            tokens_before = status.tokens_before or estimate_tokens(list(source_messages))
            details = self._merge_process_details(getattr(status, "details", None), source_messages)
            self._session_store.append_compaction(
                summary,
                first_kept,
                tokens_before,
                details=details,
            )
            status.first_kept_entry_id = first_kept
            status.messages = self._restore_persisted_context()
        else:
            self.replace_messages(status.messages)
        return status

    def apply_result(
        self,
        compacted_messages: Sequence[AgentMessage],
        result: object,
        *,
        source_messages: Sequence[AgentMessage],
        retain_source_suffix: bool = True,
    ) -> list[AgentMessage]:
        compacted = list(compacted_messages)
        if self._session_store is None or not getattr(result, "compressed", False):
            return self.replace_messages(compacted)

        context_entry_ids = self._session_context_message_entry_ids()
        source = list(source_messages)
        summary = getattr(result, "summary", None) or extract_compaction_summary(compacted)
        tokens_before = int(getattr(result, "tokens_before", 0) or estimate_tokens(source))
        first_kept = (
            self._first_kept_entry_id(result, context_entry_ids, fallback_to_leaf=False)
            if retain_source_suffix
            else ""
        )
        parent_id = self._compaction_parent_entry_id(source, context_entry_ids)
        details = self._merge_process_details(getattr(result, "details", None), source)
        self._session_store.append_compaction(
            summary,
            first_kept,
            tokens_before,
            details=details,
            parent_id=parent_id,
        )
        return self._restore_persisted_context()

    def replace_messages(self, messages: Sequence[AgentMessage]) -> list[AgentMessage]:
        replaced = list(messages)
        self._state.messages = replaced
        return replaced

    def _restore_persisted_context(self) -> list[AgentMessage]:
        assert self._session_store is not None
        snapshot = self._session_store.build_context(default_thinking_level=self._state.thinking_level)
        self._state.messages = snapshot.messages
        self._state.thinking_level = snapshot.thinking_level
        self._set_session_name(snapshot.session_name)
        return snapshot.messages

    def _merge_process_details(self, details: object, messages: Sequence[AgentMessage]):
        resolver = getattr(self._process_context, "resolve", None)
        records = resolver(list(messages)) if callable(resolver) else ()
        return merge_process_compaction_details(details, records)

    def _first_kept_entry_id(
        self,
        result: object,
        context_entry_ids: list[str],
        *,
        fallback_to_leaf: bool,
    ) -> str:
        index = getattr(result, "first_kept_message_index", None)
        if index is not None and 0 <= index < len(context_entry_ids):
            return context_entry_ids[index]
        if fallback_to_leaf and self._session_store is not None:
            return self._session_store.leaf_id or ""
        return ""

    def _compaction_parent_entry_id(
        self,
        source_messages: list[AgentMessage],
        context_entry_ids: list[str],
    ) -> str | None:
        if source_messages and len(source_messages) <= len(context_entry_ids):
            return context_entry_ids[len(source_messages) - 1]
        if self._session_store is not None:
            return self._session_store.leaf_id
        return None

    def _session_context_message_entry_ids(self) -> list[str]:
        if self._session_store is None:
            return []
        branch = self._session_store.get_branch()
        compaction_entry = None
        for entry in branch:
            if entry.get("type") == "compaction" and entry.get("summary"):
                compaction_entry = entry

        def contributes(entry: dict) -> bool:
            entry_type = entry.get("type")
            if entry_type in {"message", "custom_message"}:
                return True
            return bool(entry_type == "branch_summary" and entry.get("summary"))

        if compaction_entry is None:
            return [entry["id"] for entry in branch if entry.get("id") and contributes(entry)]

        ids = [compaction_entry["id"]]
        compaction_index = branch.index(compaction_entry)
        first_kept_id = compaction_entry.get("firstKeptEntryId")
        found_first_kept = first_kept_id is None
        for entry in branch[:compaction_index]:
            if entry.get("id") == first_kept_id:
                found_first_kept = True
            if found_first_kept and entry.get("id") and contributes(entry):
                ids.append(entry["id"])
        for entry in branch[compaction_index + 1 :]:
            if entry.get("id") and contributes(entry):
                ids.append(entry["id"])
        return ids


def extract_compaction_summary(messages: Sequence[AgentMessage]) -> str:
    for message in messages:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                str(getattr(block, "text", block.get("text", "") if isinstance(block, dict) else ""))
                for block in content
            )
        else:
            text = str(content or "")
        if text.startswith(SUMMARY_PREFIX) or text.startswith(LEGACY_SUMMARY_PREFIX):
            prefix = SUMMARY_PREFIX if text.startswith(SUMMARY_PREFIX) else LEGACY_SUMMARY_PREFIX
            text = text[len(prefix) :]
            marker_index = text.find(SUMMARY_END_MARKER)
            if marker_index >= 0:
                text = text[:marker_index]
            return text.strip()
    return ""

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
    "CompactionEndEvent",
    "CompactionStartEvent",
    "SessionCompactionAdapter",
    "compaction_summary_with_details",
    "extract_compaction_summary",
    "merge_process_compaction_details",
    "to_compressor_messages",
]
