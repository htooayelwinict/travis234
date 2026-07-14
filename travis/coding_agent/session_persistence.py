"""Focused persistence ownership for coding sessions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional

from travis.agent.agent import Agent
from travis.agent.types import AbortSignal
from travis.agent.types import AfterToolCallResult
from travis.agent.types import AgentContext
from travis.agent.types import AgentLoopTurnUpdate
from travis.agent.types import AgentTool
from travis.agent.types import AgentToolResult
from travis.agent.types import AgentMessage
from travis.agent.types import BeforeToolCallResult
from travis.agent.types import MessageEndEvent, MessageStartEvent
from travis.ai.model_resolver import ScopedModel
from travis.ai.models import (
    clamp_thinking_level,
    get_supported_thinking_levels,
)
from travis.ai.types import AssistantMessage, Cost, ImageContent, Message, Model, TextContent, UserMessage, now_ms
from travis.ai.types import ToolCall, ToolResultMessage, Usage
from travis.compaction.compressor import LEGACY_SUMMARY_PREFIX, SUMMARY_END_MARKER, SUMMARY_PREFIX, estimate_tokens
from travis.compaction.timing import CompactionManager
from travis.coding_agent.branch_summarization import generate_branch_summary
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.compaction_adapter import (
    SessionCompactionAdapter,
    compaction_summary_with_details,
)
from travis.coding_agent.compaction_coordinator import (
    CompactionCoordinator,
    CompactionTransactionCoordinator,
)
from travis.coding_agent.config import get_packaged_context_paths
from travis.coding_agent.extensions import ExtensionRunner, emit_session_shutdown_event
from travis.coding_agent.execution_backend import select_execution_backend
from travis.coding_agent.mailbox import CodingTurnMailbox, MailboxKind
from travis.coding_agent.message_utils import (
    bash_execution_text as _bash_execution_to_text,
    last_assistant_message as _last_assistant_message,
    user_message_text as _text_from_user_message_content,
)
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.process_context import ProcessContextResolver
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_index import SessionIndex
from travis.coding_agent.session_store import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CustomMessage,
    SessionStore,
    deserialize_message,
)
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from travis.coding_agent.subagents import (
    CallableSubagentBackend,
    CodexExecBackend,
    SubagentResult,
    SubagentSupervisor,
    SubagentTask,
)
from travis.coding_agent.tools import create_all_tool_definitions
from travis.coding_agent.tools.bash import BashExecOptions, BashOperations, create_local_bash_operations, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.coding_agent.tools.process import PROCESS_ACTIONS, create_process_tool_definition, prepare_process_arguments
from travis.coding_agent.tools.types import (
    ToolContext,
    ToolDefinition,
    create_tool_definition_from_agent_tool,
    wrap_tool_definition,
)

def _latest_compaction_entry(entries: list[dict]) -> dict | None:
    for entry in reversed(entries):
        if entry.get("type") == "compaction":
            return entry
    return None


def _entry_to_assistant_message(entry: dict | None) -> AssistantMessage | None:
    if not entry or entry.get("type") != "message":
        return None
    message = deserialize_message(entry.get("message", {}))
    return message if isinstance(message, AssistantMessage) else None


def _assistant_usage(message: AgentMessage) -> Usage | None:
    if not isinstance(message, AssistantMessage):
        return None
    if message.stop_reason in ("aborted", "error"):
        return None
    if _calculate_context_tokens(message.usage) <= 0:
        return None
    return message.usage


def _calculate_context_tokens(usage: Usage) -> int:
    return usage.total_tokens or usage.input + usage.output + usage.cache_read + usage.cache_write


def _estimate_context_tokens(messages: list[AgentMessage]) -> int:
    usage_index: int | None = None
    usage: Usage | None = None
    for index in range(len(messages) - 1, -1, -1):
        candidate = _assistant_usage(messages[index])
        if candidate is not None:
            usage_index = index
            usage = candidate
            break

    if usage is None or usage_index is None:
        return estimate_tokens(messages)

    trailing_tokens = estimate_tokens(messages[usage_index + 1 :])
    return _calculate_context_tokens(usage) + trailing_tokens


def _context_usage_confidence(messages: list[AgentMessage]) -> str:
    for message in reversed(messages):
        if _assistant_usage(message) is not None:
            return "provider_real"
    return "estimated_no_provider_usage"


def _collect_entries_for_branch_summary(
    session_store: SessionStore,
    old_leaf_id: str | None,
    target_id: str,
) -> tuple[list[dict], str | None]:
    if not old_leaf_id:
        return [], None

    old_path_ids = {entry["id"] for entry in session_store.get_branch(old_leaf_id)}
    target_path = session_store.get_branch(target_id)
    common_ancestor_id: str | None = None
    for entry in reversed(target_path):
        if entry["id"] in old_path_ids:
            common_ancestor_id = entry["id"]
            break

    entries: list[dict] = []
    current_id = old_leaf_id
    while current_id and current_id != common_ancestor_id:
        entry = session_store.get_entry(current_id)
        if entry is None:
            break
        entries.append(entry)
        current_id = entry.get("parentId")
    entries.reverse()
    return entries, common_ancestor_id


def _extract_user_entry_text(entry: dict) -> str:
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _extract_custom_message_entry_text(entry: dict) -> str:
    content = entry.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _user_message(text: str, images: list[ImageContent] | None = None) -> UserMessage:
    content: list[TextContent | ImageContent] = [TextContent(text=text)]
    if images:
        content.extend(images)
    return UserMessage(content=content)


def _get_user_message_text(message: UserMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))

class SessionPersistence:
    """Owns a focused AgentSession runtime concern."""

    def compact(self, focus: str | None = None, summarizer=None, deep: bool = False):
        if self._compaction_transactions is None:
            raise RuntimeError("No compaction manager configured")
        return self._compaction_transactions.manual(focus=focus, summarizer=summarizer, deep=deep)

    def set_compaction_manager(self, manager: CompactionManager | None) -> None:
        self._compaction_manager = manager
        self._compaction_transactions = (
            CompactionTransactionCoordinator(
                manager=manager,
                run_coordinator=self._compaction_coordinator,
                adapter=self._compaction_adapter,
                continue_agent=self.agent.continue_,
                extension_runner=self._extension_runner,
                branch_entries=lambda: self.session_entries,
                signal=lambda: self.agent.signal,
            )
            if manager is not None
            else None
        )

    @property
    def compaction_transactions(self) -> CompactionTransactionCoordinator:
        if self._compaction_transactions is None:
            raise RuntimeError("No compaction manager configured")
        return self._compaction_transactions

    @property
    def compaction_adapter(self) -> SessionCompactionAdapter:
        return self._compaction_adapter

    @property
    def is_compacting(self) -> bool:
        return self._compaction_adapter.is_running

    @property
    def session_entries(self) -> list[dict]:
        return self._session_store.entries if self._session_store else []

    def get_session_entry(self, entry_id: str) -> dict | None:
        return self._session_store.get_entry(entry_id) if self._session_store else None

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        return self._session_store.create_branched_session(leaf_id, path=path)

    def export_to_jsonl(self, output_path: str | None = None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        return self._session_store.export_to_jsonl(output_path)

    def export_to_html(self, output_path: str | dict | None = None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        from travis.coding_agent.export_html import export_session_to_html

        return export_session_to_html(self._session_store, self.agent.state, output_path)

    def append_custom_entry(self, custom_type: str, data=None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        return self._session_store.append_custom_entry(custom_type, data)

    @property
    def session_path(self) -> str | None:
        return str(self._session_store.path) if self._session_store else None

    @property
    def session_file(self) -> str | None:
        return self.session_path

    @property
    def session_id(self) -> str:
        return str(self._session_store.header.get("id", "")) if self._session_store else ""

    def branch(self, entry_id: str) -> None:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        self._session_store.branch(entry_id)
        snapshot = self._session_store.build_context(default_thinking_level=self.thinking_level)
        self.agent.state.messages = snapshot.messages
        self.agent.state.thinking_level = snapshot.thinking_level
        self._session_name = snapshot.session_name

    def navigate_tree(self, target_id: str, options: dict | None = None) -> dict:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        options = options or {}
        old_leaf_id = self._session_store.get_leaf_id()
        if target_id == old_leaf_id:
            return {"cancelled": False}

        target_entry = self._session_store.get_entry(target_id)
        if target_entry is None:
            raise ValueError(f"Entry {target_id} not found")

        entries_to_summarize, common_ancestor_id = _collect_entries_for_branch_summary(
            self._session_store,
            old_leaf_id,
            target_id,
        )
        custom_instructions = options.get("customInstructions", options.get("custom_instructions"))
        replace_instructions = options.get("replaceInstructions", options.get("replace_instructions"))
        label = options.get("label")
        wants_summary = bool(options.get("summarize", False))
        preparation = {
            "targetId": target_id,
            "oldLeafId": old_leaf_id,
            "commonAncestorId": common_ancestor_id,
            "entriesToSummarize": entries_to_summarize,
            "userWantsSummary": wants_summary,
            "customInstructions": custom_instructions,
            "replaceInstructions": replace_instructions,
            "label": label,
        }

        extension_summary: dict | None = None
        from_extension = False
        if self._extension_runner.has_handlers("session_before_tree"):
            before_result = self._extension_runner.emit(
                {
                    "type": "session_before_tree",
                    "preparation": preparation,
                    "signal": self.agent.signal,
                }
            )
            if isinstance(before_result, dict):
                if before_result.get("cancel"):
                    return {"cancelled": True}
                if before_result.get("customInstructions") is not None:
                    custom_instructions = before_result["customInstructions"]
                if before_result.get("replaceInstructions") is not None:
                    replace_instructions = before_result["replaceInstructions"]
                if before_result.get("label") is not None:
                    label = before_result["label"]
                summary_result = before_result.get("summary")
                if wants_summary and isinstance(summary_result, dict) and summary_result.get("summary"):
                    extension_summary = summary_result
                    from_extension = True
                elif wants_summary and isinstance(summary_result, str) and summary_result:
                    extension_summary = {"summary": summary_result}
                    from_extension = True

        summary_text: str | None = None
        summary_details = None
        if extension_summary:
            summary_text = str(extension_summary["summary"])
            summary_details = extension_summary.get("details")
        elif wants_summary and entries_to_summarize:
            branch_result = generate_branch_summary(
                entries_to_summarize,
                model=self.model,
                signal=self.agent.signal,
                custom_instructions=custom_instructions,
                replace_instructions=replace_instructions,
                stream_fn=self.model_registry.stream_simple,
            )
            if branch_result.aborted:
                return {"cancelled": True, "aborted": True}
            if branch_result.error:
                raise RuntimeError(branch_result.error)
            summary_text = branch_result.summary
            summary_details = {
                "readFiles": branch_result.read_files,
                "modifiedFiles": branch_result.modified_files,
            }

        new_leaf_id: str | None
        editor_text: str | None = None
        if target_entry.get("type") == "message" and target_entry.get("message", {}).get("role") == "user":
            new_leaf_id = target_entry.get("parentId")
            editor_text = _extract_user_entry_text(target_entry)
        elif target_entry.get("type") == "custom_message":
            new_leaf_id = target_entry.get("parentId")
            editor_text = _extract_custom_message_entry_text(target_entry)
        else:
            new_leaf_id = target_id

        summary_entry: dict | None = None
        if summary_text:
            summary_id = self._session_store.branch_with_summary(new_leaf_id, summary_text, summary_details, from_extension)
            summary_entry = self._session_store.get_entry(summary_id)
            if label:
                self._session_store.append_label_change(summary_id, label)
        elif new_leaf_id is None:
            self._session_store.reset_leaf()
        else:
            self._session_store.branch(new_leaf_id)

        if label and not summary_text:
            self._session_store.append_label_change(target_id, label)

        snapshot = self._session_store.build_context(default_thinking_level=self.thinking_level)
        self.agent.state.messages = snapshot.messages
        self.agent.state.thinking_level = snapshot.thinking_level
        self._session_name = snapshot.session_name
        self._extension_runner.emit(
            {
                "type": "session_tree",
                "newLeafId": self._session_store.get_leaf_id(),
                "oldLeafId": old_leaf_id,
                "summaryEntry": summary_entry,
                "fromExtension": from_extension if summary_text else None,
            }
        )
        result = {"cancelled": False}
        if editor_text is not None:
            result["editorText"] = editor_text
        if summary_entry is not None:
            result["summaryEntry"] = summary_entry
        return result

    def get_user_messages_for_forking(self) -> list[dict[str, str]]:
        if self._session_store is None:
            return []

        result: list[dict[str, str]] = []
        for entry in self._session_store.entries:
            if entry.get("type") != "message":
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = _extract_user_entry_text(entry)
            if text:
                result.append({"entryId": str(entry["id"]), "text": text})
        return result

    def get_last_assistant_text(self) -> str | None:
        for message in reversed(self.messages):
            if not isinstance(message, AssistantMessage):
                continue
            if message.stop_reason == "aborted" and not message.content:
                continue

            text = "".join(block.text for block in message.content if isinstance(block, TextContent))
            text = text.strip()
            return text or None
        return None

    def get_session_stats(self) -> dict[str, object]:
        messages = self.agent.state.messages
        user_messages = sum(1 for message in messages if isinstance(message, UserMessage))
        assistant_messages = sum(1 for message in messages if isinstance(message, AssistantMessage))
        tool_results = sum(1 for message in messages if isinstance(message, ToolResultMessage))

        tool_calls = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_cost = 0.0

        for message in messages:
            if not isinstance(message, AssistantMessage):
                continue
            tool_calls += sum(1 for block in message.content if isinstance(block, ToolCall))
            total_input += message.usage.input
            total_output += message.usage.output
            total_cache_read += message.usage.cache_read
            total_cache_write += message.usage.cache_write
            total_cost += message.usage.cost.total

        return {
            "sessionFile": self.session_file,
            "sessionId": self.session_id,
            "userMessages": user_messages,
            "assistantMessages": assistant_messages,
            "toolCalls": tool_calls,
            "toolResults": tool_results,
            "totalMessages": len(messages),
            "tokens": {
                "input": total_input,
                "output": total_output,
                "cacheRead": total_cache_read,
                "cacheWrite": total_cache_write,
                "total": total_input + total_output + total_cache_read + total_cache_write,
            },
            "cost": total_cost,
            "contextUsage": self.get_context_usage(),
        }

    def get_context_usage(self) -> dict[str, object] | None:
        context_window = self.model.context_window or 0
        if context_window <= 0:
            return None

        branch_entries = self._session_store.get_branch() if self._session_store else []
        latest_compaction = _latest_compaction_entry(branch_entries)
        if latest_compaction is not None:
            compaction_index = branch_entries.index(latest_compaction)
            has_post_compaction_usage = False
            for entry in reversed(branch_entries[compaction_index + 1 :]):
                if entry.get("type") != "message":
                    continue
                message_data = entry.get("message")
                if not isinstance(message_data, dict) or message_data.get("role") != "assistant":
                    continue
                assistant = self._session_store and self._session_store.get_entry(entry["id"])
                assistant_message = _entry_to_assistant_message(assistant or entry)
                if assistant_message is not None and _calculate_context_tokens(assistant_message.usage) > 0:
                    has_post_compaction_usage = True
                break
            if not has_post_compaction_usage:
                tokens = estimate_tokens(self.messages)
                return {
                    "tokens": tokens,
                    "contextWindow": context_window,
                    "percent": (tokens / context_window) * 100,
                    "estimated": True,
                    "confidence": "estimated_after_compaction",
                }

        tokens = _estimate_context_tokens(self.messages)
        confidence = _context_usage_confidence(self.messages)
        usage = {
            "tokens": tokens,
            "contextWindow": context_window,
            "percent": (tokens / context_window) * 100,
            "confidence": confidence,
        }
        if confidence != "provider_real":
            usage["estimated"] = True
        return usage

__all__ = (
    'SessionPersistence',
    '_assistant_usage',
    '_calculate_context_tokens',
    '_collect_entries_for_branch_summary',
    '_context_usage_confidence',
    '_entry_to_assistant_message',
    '_estimate_context_tokens',
    '_extract_custom_message_entry_text',
    '_extract_user_entry_text',
    '_get_user_message_text',
    '_latest_compaction_entry',
    '_user_message',
)
