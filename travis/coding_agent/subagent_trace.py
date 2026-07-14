"""Focused subagent trace ownership for coding sessions."""

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

from travis.coding_agent.session_types import _MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT, _MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX, _SUBAGENT_EXPANSION_BUDGETS, _SUBAGENT_RESULT_SUMMARY_LIMIT, _SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT, _SUBAGENT_VISIBLE_SUMMARY_LIMIT, _tool_result_text

def _subagent_preview(value: object, *, limit: int = 160) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            text = str(value)
    return _truncate_preview(text.replace("\n", " "), limit=limit)


def _subagent_tool_result_preview(result: object) -> str:
    content = getattr(result, "content", result)
    return _tool_result_text(content)


def _truncate_preview(text: str, *, limit: int = 240) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _subagent_tool_event(task: SubagentTask, event_type: str, entry: Mapping[str, object]) -> dict[str, object]:
    payload = {
        "type": event_type,
        "taskId": task.id,
        "role": task.role,
        "backend": task.backend,
        "toolCallId": entry.get("toolCallId", ""),
        "toolName": entry.get("toolName", ""),
        "status": entry.get("status", ""),
        "argsPreview": entry.get("argsPreview", ""),
        "resultPreview": entry.get("resultPreview", ""),
        "elapsedMs": entry.get("elapsedMs", 0),
    }
    return payload


def _truncate_subagent_text(text: str, *, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 18)].rstrip() + "\n... [truncated]"


def _public_subagent_tool_trace(tool_trace: list[dict[str, object]]) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    for entry in tool_trace[-_SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT:]:
        public_entry = {
            "toolCallId": str(entry.get("toolCallId", "")),
            "toolName": str(entry.get("toolName", "")),
            "status": str(entry.get("status", "")),
            "argsPreview": _truncate_preview(str(entry.get("argsPreview", "")), limit=80),
            "resultPreview": _truncate_preview(str(entry.get("resultPreview", "")), limit=120),
            "elapsedMs": entry.get("elapsedMs", 0),
        }
        public.append(public_entry)
    return public


def _public_subagent_result_details(result: SubagentResult) -> dict[str, object]:
    details = {
        "taskId": result.task_id,
        "backend": result.backend,
        "role": result.role,
        "status": result.status,
        "summary": _truncate_subagent_text(result.summary, limit=_SUBAGENT_RESULT_SUMMARY_LIMIT),
        "filesChanged": list(result.files_changed),
        "artifacts": list(result.artifacts),
        "errors": list(result.errors),
        "usage": dict(result.usage),
        "childSessionId": result.child_session_id,
        "rawLogPath": result.raw_log_path,
        "startedAtMs": result.started_at_ms,
        "endedAtMs": result.ended_at_ms,
        "durationMs": result.duration_ms,
        "toolTrace": _public_subagent_tool_trace(result.tool_trace),
        "toolTraceCount": len(result.tool_trace),
    }
    return details


def _subagent_expansion_section_arg(args: Mapping[str, object]) -> str:
    section = str(args.get("section", "summary") or "summary").strip().lower().replace("-", "_")
    valid = {"summary", "final_response", "tool_trace", "files", "errors", "findings", "all"}
    if section not in valid:
        raise ValueError(f"Unsupported subagent expansion section: {section}")
    return section


def _subagent_expansion_budget_arg(args: Mapping[str, object]) -> str:
    budget = str(args.get("budget", "medium") or "medium").strip().lower()
    if budget not in _SUBAGENT_EXPANSION_BUDGETS:
        raise ValueError(f"Unsupported subagent expansion budget: {budget}")
    return budget


def _subagent_expansion_offset_arg(args: Mapping[str, object]) -> int:
    value = args.get("offset", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("offset must be a non-negative integer")
    return value


def _available_subagent_expansion_sections(result: SubagentResult) -> list[str]:
    sections = ["summary"]
    if result.summary.strip():
        sections.append("findings")
    if result.final_response.strip():
        sections.append("final_response")
    if result.tool_trace:
        sections.append("tool_trace")
    if result.files_changed or result.artifacts:
        sections.append("files")
    if result.errors:
        sections.append("errors")
    sections.append("all")
    return list(dict.fromkeys(sections))


def _subagent_expansion_source_text(result: SubagentResult, section: str) -> str:
    if section in {"summary", "findings"}:
        return result.summary.strip()
    if section == "final_response":
        return (result.final_response or result.summary).strip()
    if section == "tool_trace":
        if not result.tool_trace:
            return "No child tool trace is available."
        return "\n".join(_format_subagent_tool_trace_entry(entry) for entry in result.tool_trace).strip()
    if section == "files":
        lines: list[str] = []
        if result.files_changed:
            lines.append("filesChanged:")
            lines.extend(f"- {path}" for path in result.files_changed)
        if result.artifacts:
            lines.append("artifacts:")
            lines.extend(f"- {path}" for path in result.artifacts)
        return "\n".join(lines).strip() or "No child file or artifact metadata is available."
    if section == "errors":
        lines = list(result.errors)
        return "\n".join(lines).strip() or "No child errors are available."
    if section == "all":
        parts = [
            f"taskId: {result.task_id}",
            f"role: {result.role}",
            f"backend: {result.backend}",
            f"status: {result.status}",
            "",
            "summary:",
            result.summary.strip() or "(none)",
        ]
        final_response = result.final_response.strip()
        if final_response and final_response != result.summary.strip():
            parts.extend(["", "finalResponse:", final_response])
        if result.files_changed or result.artifacts:
            parts.extend(["", "files:", _subagent_expansion_source_text(result, "files")])
        if result.errors:
            parts.extend(["", "errors:", _subagent_expansion_source_text(result, "errors")])
        if result.tool_trace:
            parts.extend(["", "toolTrace:", _subagent_expansion_source_text(result, "tool_trace")])
        return "\n".join(parts).strip()
    raise ValueError(f"Unsupported subagent expansion section: {section}")


def _expanded_subagent_result_details(
    result: SubagentResult,
    *,
    section: str,
    budget: str,
    offset: int,
) -> dict[str, object]:
    source = _subagent_expansion_source_text(result, section)
    limit = _SUBAGENT_EXPANSION_BUDGETS[budget]
    if offset >= len(source):
        text = ""
        next_offset = None
        truncated = False
    else:
        end = min(len(source), offset + limit)
        text = source[offset:end]
        truncated = end < len(source)
        next_offset = end if truncated else None
    return {
        "taskId": result.task_id,
        "backend": result.backend,
        "role": result.role,
        "status": result.status,
        "section": section,
        "budget": budget,
        "offset": offset,
        "text": text,
        "truncated": truncated,
        "nextOffset": next_offset,
        "totalChars": len(source),
        "availableSections": _available_subagent_expansion_sections(result),
        "rawLogPath": result.raw_log_path,
    }


def _format_subagent_expansion(details: Mapping[str, object]) -> str:
    text = str(details.get("text", "") or "")
    lines = [
        f"Subagent {details.get('taskId')} expansion",
        f"role: {details.get('role')}",
        f"backend: {details.get('backend')}",
        f"status: {details.get('status')}",
        f"section: {details.get('section')}",
        f"offset: {details.get('offset')}",
        "",
        text or "(empty)",
    ]
    if details.get("truncated"):
        lines.extend(
            [
                "",
                f"... [truncated; call expand_subagent_result with offset={details.get('nextOffset')}]",
            ]
        )
    return "\n".join(lines).strip()


def _format_subagent_tool_trace_entry(entry: Mapping[str, object]) -> str:
    tool = str(entry.get("toolName") or "tool")
    status = str(entry.get("status") or "unknown")
    args = _truncate_preview(str(entry.get("argsPreview") or "").strip(), limit=80)
    result = _truncate_preview(str(entry.get("resultPreview") or "").strip(), limit=120)
    parts = [tool, status]
    if args:
        parts.append(args)
    if result:
        parts.append(f"=> {result}")
    return " ".join(parts)


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.text for block in content if isinstance(block, TextContent))
    return ""

def _reject_unexpected_args(args, allowed: set[str]) -> None:
    if not isinstance(args, Mapping):
        raise ValueError("tool arguments must be an object")
    unexpected = sorted(str(key) for key in args.keys() if key not in allowed)
    if unexpected:
        raise ValueError(f"Unsupported argument(s): {', '.join(unexpected)}")


def _coerce_subagent_timeout_seconds(
    value: object,
    *,
    default: int,
    max_seconds: int | None = None,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timeoutSeconds must be a positive integer")
    if value <= 0:
        raise ValueError("timeoutSeconds must be positive")
    if max_seconds is not None and value > max_seconds:
        raise ValueError(f"timeoutSeconds must be <= {max_seconds}")
    return value


def _model_subagent_timeout_seconds_arg(args) -> int:
    if not isinstance(args, Mapping):
        raise ValueError("tool arguments must be an object")
    return _coerce_subagent_timeout_seconds(
        args.get("timeoutSeconds"),
        default=_MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT,
        max_seconds=_MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX,
    )


def _required_text_arg(args, name: str) -> str:
    if not isinstance(args, Mapping):
        raise ValueError("tool arguments must be an object")
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _task_id_arg(args) -> str:
    if not isinstance(args, Mapping):
        raise ValueError("tool arguments must be an object")
    value = args.get("taskId", args.get("task_id"))
    if not isinstance(value, str) or not value.strip():
        raise ValueError("taskId is required")
    return value.strip()


def _optional_timeout_arg(args) -> float | None:
    if not isinstance(args, Mapping):
        raise ValueError("tool arguments must be an object")
    value = args.get("timeoutSeconds", args.get("timeout_seconds"))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeoutSeconds must be a number")
    return float(value)

class SessionSubagentTraceController:
    """Owns a focused AgentSession runtime concern."""

    def _subagent_tool_trace_listener(
        self,
        task: SubagentTask,
        child: "AgentSession",
        tool_trace: list[dict[str, object]],
        trace_by_call_id: dict[str, dict[str, object]],
    ) -> Callable[[object], None]:
        def _listener(event) -> None:
            event_type = getattr(event, "type", None)
            if event_type == "tool_execution_start":
                entry = {
                    "toolCallId": getattr(event, "tool_call_id", ""),
                    "toolName": getattr(event, "tool_name", ""),
                    "status": "started",
                    "argsPreview": _subagent_preview(getattr(event, "args", None)),
                    "resultPreview": "",
                    "startedAtMs": int(time.time() * 1000),
                    "endedAtMs": 0,
                    "elapsedMs": 0,
                }
                tool_trace.append(entry)
                trace_by_call_id[str(entry["toolCallId"])] = entry
                self._handle_subagent_event(_subagent_tool_event(task, "subagent_tool_start", entry))
                return
            if event_type == "message_end":
                message = getattr(event, "message", None)
                if getattr(message, "role", None) != "toolResult":
                    return
                self._record_subagent_tool_end(
                    task,
                    child,
                    tool_trace,
                    trace_by_call_id,
                    tool_call_id=str(getattr(message, "tool_call_id", "")),
                    tool_name=str(getattr(message, "tool_name", "")),
                    args=None,
                    content=getattr(message, "content", None),
                    is_error=bool(getattr(message, "is_error", False)),
                )
                return
            if event_type == "turn_end":
                for message in getattr(event, "tool_results", []) or []:
                    self._record_subagent_tool_end(
                        task,
                        child,
                        tool_trace,
                        trace_by_call_id,
                        tool_call_id=str(getattr(message, "tool_call_id", "")),
                        tool_name=str(getattr(message, "tool_name", "")),
                        args=None,
                        content=getattr(message, "content", None),
                        is_error=bool(getattr(message, "is_error", False)),
                    )
                return
            if event_type != "tool_execution_end":
                return

            tool_call_id = str(getattr(event, "tool_call_id", ""))
            entry = trace_by_call_id.get(tool_call_id)
            if entry is None:
                entry = {
                    "toolCallId": tool_call_id,
                    "toolName": getattr(event, "tool_name", ""),
                    "status": "started",
                    "argsPreview": "",
                    "resultPreview": "",
                    "startedAtMs": int(time.time() * 1000),
                    "endedAtMs": 0,
                    "elapsedMs": 0,
                }
                tool_trace.append(entry)
                trace_by_call_id[tool_call_id] = entry
            result_preview = _subagent_tool_result_preview(getattr(event, "result", None))
            status = "error" if bool(getattr(event, "is_error", False)) else "ok"
            ended = int(time.time() * 1000)
            entry.update(
                {
                    "status": status,
                    "resultPreview": _truncate_preview(result_preview),
                    "endedAtMs": ended,
                    "elapsedMs": max(0, ended - int(entry.get("startedAtMs", ended) or ended)),
                }
            )
            self._handle_subagent_event(_subagent_tool_event(task, "subagent_tool_end", entry))

        return _listener

    def _reconcile_subagent_tool_results_from_messages(
        self,
        task: SubagentTask,
        child: "AgentSession",
        messages: list[AgentMessage],
        tool_trace: list[dict[str, object]],
        trace_by_call_id: dict[str, dict[str, object]],
    ) -> None:
        for message in messages:
            if getattr(message, "role", None) != "toolResult":
                continue
            self._record_subagent_tool_end(
                task,
                child,
                tool_trace,
                trace_by_call_id,
                tool_call_id=str(getattr(message, "tool_call_id", "")),
                tool_name=str(getattr(message, "tool_name", "")),
                args=None,
                content=getattr(message, "content", None),
                is_error=bool(getattr(message, "is_error", False)),
            )

    def _subagent_after_tool_call_tracer(
        self,
        task: SubagentTask,
        child: "AgentSession",
        tool_trace: list[dict[str, object]],
        trace_by_call_id: dict[str, dict[str, object]],
        original_after_tool_call,
    ):
        def _after_tool_call(context, signal=None):
            result = original_after_tool_call(context, signal=signal) if original_after_tool_call else None
            content = getattr(result, "content", None) if result is not None else None
            if content is None:
                content = getattr(context.result, "content", None)
            is_error = getattr(result, "is_error", None) if result is not None else None
            if is_error is None:
                is_error = bool(getattr(context, "is_error", False))
            self._record_subagent_tool_end(
                task,
                child,
                tool_trace,
                trace_by_call_id,
                tool_call_id=str(getattr(context.tool_call, "id", "")),
                tool_name=str(getattr(context.tool_call, "name", "")),
                args=getattr(context, "args", None),
                content=content,
                is_error=bool(is_error),
            )
            return result

        return _after_tool_call

    def _record_subagent_tool_end(
        self,
        task: SubagentTask,
        child: "AgentSession",
        tool_trace: list[dict[str, object]],
        trace_by_call_id: dict[str, dict[str, object]],
        *,
        tool_call_id: str,
        tool_name: str,
        args: object,
        content: object,
        is_error: bool,
    ) -> None:
        entry = trace_by_call_id.get(tool_call_id)
        if entry is None:
            for candidate in reversed(tool_trace):
                if candidate.get("status") != "started":
                    continue
                candidate_tool_name = str(candidate.get("toolName", ""))
                if tool_name and candidate_tool_name and candidate_tool_name != tool_name:
                    continue
                entry = candidate
                if tool_call_id:
                    trace_by_call_id[tool_call_id] = entry
                break
        if entry is None:
            entry = {
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "status": "started",
                "argsPreview": _subagent_preview(args),
                "resultPreview": "",
                "startedAtMs": int(time.time() * 1000),
                "endedAtMs": 0,
                "elapsedMs": 0,
            }
            tool_trace.append(entry)
            trace_by_call_id[tool_call_id] = entry
        elif int(entry.get("endedAtMs", 0) or 0) > 0:
            return
        result_preview = _tool_result_text(content)
        status = "error" if is_error else "ok"
        ended = int(time.time() * 1000)
        entry.update(
            {
                "toolName": tool_name or entry.get("toolName", ""),
                "status": status,
                "argsPreview": entry.get("argsPreview") or _subagent_preview(args),
                "resultPreview": _truncate_preview(result_preview),
                "endedAtMs": ended,
                "elapsedMs": max(0, ended - int(entry.get("startedAtMs", ended) or ended)),
            }
        )
        self._handle_subagent_event(_subagent_tool_event(task, "subagent_tool_end", entry))

    def _messages_to_summary(self, messages: list[AgentMessage]) -> str:
        parts: list[str] = []
        for message in messages:
            role = getattr(message, "role", "")
            if role not in {"assistant", "custom"}:
                continue
            content = getattr(message, "content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(str(text))
        return "\n".join(part for part in parts if part).strip()

    def _format_subagent_result(self, result: SubagentResult) -> str:
        heading = f"Subagent {result.task_id}"
        summary = _truncate_subagent_text(result.summary, limit=_SUBAGENT_VISIBLE_SUMMARY_LIMIT)
        lines = [
            heading,
            f"role: {result.role}",
            f"backend: {result.backend}",
            f"status: {result.status}",
            f"summary: {summary or 'none'}",
        ]
        if result.status != "completed" and result.errors:
            lines.append(f"error: {_truncate_subagent_text('; '.join(result.errors), limit=180)}")
        return "\n".join(lines).strip()

    def _handle_subagent_event(self, event: dict[str, object]) -> None:
        self._emit(event)
        try:
            self._extension_runner.emit(event)
        except Exception as error:
            self._subagent_observer_errors.append(
                f"extension observer failed for {event.get('type', 'unknown')}: {error}"
            )

    def subagent_observer_errors(self) -> list[str]:
        return list(self._subagent_observer_errors)

__all__ = (
    'SessionSubagentTraceController',
    '_available_subagent_expansion_sections',
    '_coerce_subagent_timeout_seconds',
    '_expanded_subagent_result_details',
    '_format_subagent_expansion',
    '_format_subagent_tool_trace_entry',
    '_message_content_text',
    '_model_subagent_timeout_seconds_arg',
    '_optional_timeout_arg',
    '_public_subagent_result_details',
    '_public_subagent_tool_trace',
    '_reject_unexpected_args',
    '_required_text_arg',
    '_subagent_expansion_budget_arg',
    '_subagent_expansion_offset_arg',
    '_subagent_expansion_section_arg',
    '_subagent_expansion_source_text',
    '_subagent_preview',
    '_subagent_tool_event',
    '_subagent_tool_result_preview',
    '_task_id_arg',
    '_truncate_preview',
    '_truncate_subagent_text',
)
