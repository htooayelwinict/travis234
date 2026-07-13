"""Focused types ownership for coding sessions."""

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
from travis.coding_agent.policies.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolGuardrailDecision,
    ToolLoopPolicy,
    append_toolguard_guidance,
    classify_tool_failure,
    toolguard_synthetic_result,
)
from travis.coding_agent.policies.iteration_limit import coding_iteration_limit_message
from travis.coding_agent.policies.package_consent import PackageMutationPolicy
from travis.coding_agent.policies.pipeline import PolicyPipeline
from travis.coding_agent.policies.types import (
    Allow,
    Block,
    CodingPolicyEvent,
    CodingTurnContext,
    RequireConsent,
    ToolCallView,
    TurnCapabilities,
)
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
from travis.coding_agent.capabilities import CapabilityViolation, WorkspaceCapability
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
from travis.coding_agent.provider_control_plane import ProviderControlPlane
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

_SUBAGENT_TOOL_NAMES = [
    "spawn_subagent",
    "wait_subagent",
    "list_subagents",
    "get_subagent_result",
    "expand_subagent_result",
    "cancel_subagent",
]
_DEFAULT_SUBAGENT_ALLOWED_TOOLS = ("read", "grep", "find", "ls")
_SKILL_SUBAGENT_ALLOWED_TOOL_NAMES = {"read", "grep", "find", "ls", "bash"}
_MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT = 300
_MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX = 300
_MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN = 3
_SUBAGENT_RESULT_SUMMARY_LIMIT = 1000
_SUBAGENT_VISIBLE_SUMMARY_LIMIT = 320
_SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT = 3
_SUBAGENT_EXPANSION_BUDGETS = {"short": 1200, "medium": 6000, "long": 12000}
_DEFAULT_ACTIVE_TOOL_NAMES = ["read", "bash", "edit", "write"]
_SUBAGENT_OPT_IN_TERMS = (
    "/subagents",
    "subagent",
    "subagents",
    "child agent",
    "child-agent",
    "delegate",
    "delegation",
    "spawn_subagent",
    "wait_subagent",
    "reviewer agent",
    "researcher agent",
)
_SUBAGENT_OPT_OUT_TERMS = (
    "without subagent",
    "without subagents",
    "no subagent",
    "no subagents",
    "do not use subagent",
    "do not use subagents",
    "don't use subagent",
    "don't use subagents",
)
_SUBAGENT_FILE_MUTATION_GOAL_PATTERN = re.compile(
    r"\b(?:write|create|edit|modify|update|delete|remove|save|append|overwrite)\b"
    r"[\s\S]{0,120}?"
    r"(?:"
    r"[\w./-]+\.(?:md|txt|json|ya?ml|py|js|ts|tsx|jsx|html|css|toml|ini|cfg|env|sh|rs|go|java|c|cpp|h|hpp|sql|csv|xml)"
    r"|\b(?:file|files|document|documents|artifact|artifacts)\b"
    r")",
    re.IGNORECASE,
)
_SUBAGENT_FILE_MUTATION_NEGATION_PREFIX_PATTERN = re.compile(
    r"(?:"
    r"\bdo\s+not\b"
    r"|\bdon't\b"
    r"|\bnever\b"
    r"|\bmust\s+not\b"
    r"|\bshould\s+not\b"
    r"|\bwithout\b"
    r"|\bavoid\b"
    r"|\bno\s+need\s+to\b"
    r")"
    r"(?:\s+\w+){0,6}\s*$",
    re.IGNORECASE,
)


def _subagent_goal_requests_file_mutation(goal: str) -> bool:
    text = str(goal or "")
    for match in _SUBAGENT_FILE_MUTATION_GOAL_PATTERN.finditer(text):
        prefix = text[max(0, match.start() - 80) : match.start()]
        if _SUBAGENT_FILE_MUTATION_NEGATION_PREFIX_PATTERN.search(prefix):
            continue
        return True
    return False


def _prompt_requests_subagent_tools(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(text or "").lower())
    if any(term in lowered for term in _SUBAGENT_OPT_OUT_TERMS):
        return False
    return any(term in lowered for term in _SUBAGENT_OPT_IN_TERMS)


_SPAWN_SUBAGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "description": "Short child-agent role name, e.g. reviewer or researcher."},
        "goal": {
            "type": "string",
            "description": (
                "Bounded read-only task for the child agent. Do not ask the child to write, edit, create, "
                "delete, or save files; if Lewis requested an artifact, the child should inspect and the parent should write it."
            ),
        },
        "backend": {"type": "string", "description": "Subagent backend to use. Defaults to internal."},
        "wait": {"type": "boolean", "description": "Wait for the child result before returning. Defaults to true."},
        "timeoutSeconds": {"type": "integer", "description": "Maximum seconds to wait for the child result."},
        "contextPack": {"type": "string", "description": "Optional context to include in the child prompt."},
    },
    "required": ["role", "goal"],
    "additionalProperties": False,
}
_EXPAND_SUBAGENT_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "taskId": {"type": "string", "description": "Completed child subagent task id."},
        "section": {
            "type": "string",
            "description": "Child-owned section to expand: summary, final_response, tool_trace, files, errors, findings, or all.",
        },
        "budget": {"type": "string", "description": "Expansion budget: short, medium, or long. Defaults to medium."},
        "offset": {"type": "integer", "description": "Character offset for paging long child output. Defaults to 0."},
    },
    "required": ["taskId"],
    "additionalProperties": False,
}
_TASK_ID_SCHEMA = {
    "type": "object",
    "properties": {
        "taskId": {"type": "string", "description": "Subagent task id."},
        "timeoutSeconds": {"type": "number", "description": "Optional wait timeout in seconds."},
    },
    "required": ["taskId"],
    "additionalProperties": False,
}
_LIST_SUBAGENTS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_CANCEL_SUBAGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "taskId": {"type": "string", "description": "Subagent task id."},
        "reason": {"type": "string", "description": "Optional cancellation reason."},
    },
    "required": ["taskId"],
    "additionalProperties": False,
}
_BRANCH_SUMMARY_PREFIX = "The following is a summary of a branch that this conversation came back from:\n\n<summary>\n"
_BRANCH_SUMMARY_SUFFIX = "</summary>"
_COMPACTION_SUMMARY_PREFIX = "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
_COMPACTION_SUMMARY_SUFFIX = "\n</summary>"
_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high"]
_RETRYABLE_ERROR_MARKERS = (
    "overloaded",
    "provider returned error",
    "provider_returned_error",
    "rate limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "service unavailable",
    "server error",
    "internal error",
    "network_error",
    "network error",
    "connection error",
    "connection refused",
    "connection lost",
    "websocket closed",
    "websocket error",
    "fetch failed",
    "socket hang up",
    "sse stream received no data events",
    "no data events",
    "stream ended",
    "timed out",
    "timeout",
    "terminated",
)
_MALFORMED_STREAMED_TOOL_ARGS_MARKER = "malformed streamed tool-call arguments"
_MALFORMED_STREAM_RECOVERY_PREFIX = (
    "The previous provider stream ended with malformed streamed tool-call arguments"
)
_PARTIAL_STREAM_STUB_ID = "partial-stream-stub"
_PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE = "partial_stream_dropped_tool_calls"
_MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE = "malformed_streamed_tool_call_arguments"
_MAX_PARTIAL_STREAM_CONTINUATIONS = 3
_NON_RETRYABLE_PROVIDER_LIMIT_MARKERS = (
    "gousagelimiterror",
    "freeusagelimiterror",
    "monthly usage limit reached",
    "available balance",
    "insufficient_quota",
    "out of budget",
    "quota exceeded",
    "billing",
)
@dataclass
class QueueUpdateEvent:
    steering: list[str]
    follow_up: list[str]
    type: str = "queue_update"



@dataclass
class AutoRetryStartEvent:
    attempt: int
    max_attempts: int
    delay_ms: int
    error_message: str
    type: str = "auto_retry_start"





@dataclass
class AutoRetryEndEvent:
    success: bool
    attempt: int
    final_error: str | None = None
    type: str = "auto_retry_end"



@dataclass
class BashResult:
    output: str
    exit_code: int | None
    cancelled: bool
    truncated: bool
    full_output_path: str | None = None




@dataclass
class SessionInfoChangedEvent:
    name: str | None
    type: str = "session_info_changed"


@dataclass
class ThinkingLevelChangedEvent:
    level: str
    type: str = "thinking_level_changed"


@dataclass
class ModelCycleResult:
    model: Model
    thinking_level: str
    is_scoped: bool




@dataclass
class ExtensionCompactionResult:
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: object | None = None




CompactionResult = ExtensionCompactionResult


@dataclass
class ExtensionCommandContext:
    cwd: str
    _get_system_prompt: Callable[[], str]
    _get_system_prompt_options: Callable[[], BuildSystemPromptOptions]
    _send_message: Callable[[dict, dict | None], list[AgentMessage]]
    _send_user_message: Callable[[str | list[TextContent | ImageContent], dict | None], list[AgentMessage] | None]
    _append_entry: Callable[[str, object], str]
    _set_session_name: Callable[[str | None], None]
    _get_session_name: Callable[[], str | None]
    _get_active_tools: Callable[[], list[str]]
    _get_all_tools: Callable[[], list[dict]]
    _set_active_tools: Callable[[list[str]], None]
    _get_commands: Callable[[], list[dict]]
    _get_thinking_level: Callable[[], str]
    _set_thinking_level: Callable[[str], None]
    _set_model: Callable[[Model], bool]
    _set_label: Callable[[str, str | None], None]
    _exec: Callable[[str, list[str], dict | None], dict]
    _wait_for_idle: Callable[[], None]
    _get_signal: Callable[[], AbortSignal]
    _compact: Callable[[dict | None], ExtensionCompactionResult | None]
    _spawn_subagent: Callable[[str, str, dict | None], dict]
    _list_subagents: Callable[[], list[dict]]
    _get_subagent_result: Callable[[str], dict | None]
    _cancel_subagent: Callable[[str, str | None], dict]

    def get_system_prompt(self) -> str:
        return self._get_system_prompt()


    def get_system_prompt_options(self) -> BuildSystemPromptOptions:
        return self._get_system_prompt_options()


    def send_message(self, message: dict, options: dict | None = None) -> list[AgentMessage]:
        return self._send_message(message, options)


    def send_user_message(
        self,
        content: str | list[TextContent | ImageContent],
        options: dict | None = None,
    ) -> list[AgentMessage] | None:
        return self._send_user_message(content, options)


    def append_entry(self, custom_type: str, data=None) -> str:
        return self._append_entry(custom_type, data)


    def set_session_name(self, name: str | None) -> None:
        self._set_session_name(name)


    def get_session_name(self) -> str | None:
        return self._get_session_name()


    def get_active_tools(self) -> list[str]:
        return self._get_active_tools()


    def get_all_tools(self) -> list[dict]:
        return self._get_all_tools()


    def set_active_tools(self, tool_names: list[str]) -> None:
        self._set_active_tools(tool_names)


    def get_commands(self) -> list[dict]:
        return self._get_commands()


    def get_thinking_level(self) -> str:
        return self._get_thinking_level()


    def set_thinking_level(self, level: str) -> None:
        self._set_thinking_level(level)


    def set_model(self, model: Model) -> bool:
        return self._set_model(model)


    def set_label(self, entry_id: str, label: str | None) -> None:
        self._set_label(entry_id, label)


    def exec(self, command: str, args: list[str], options: dict | None = None) -> dict:
        return self._exec(command, args, options)

    def wait_for_idle(self) -> None:
        self._wait_for_idle()


    def get_signal(self) -> AbortSignal:
        return self._get_signal()


    def compact(self, options: dict | None = None) -> ExtensionCompactionResult | None:
        return self._compact(options)

    def spawn_subagent(self, role: str, goal: str, options: dict | None = None) -> dict:
        return self._spawn_subagent(role, goal, options)


    def list_subagents(self) -> list[dict]:
        return self._list_subagents()


    def get_subagent_result(self, task_id: str) -> dict | None:
        return self._get_subagent_result(task_id)


    def cancel_subagent(self, task_id: str, reason: str | None = None) -> dict:
        return self._cancel_subagent(task_id, reason)



def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Convert Travis coding-agent custom messages to provider-safe ai Messages."""
    out: list[Message] = []
    for message in _exclude_aborted_turns_from_context(messages):
        role = getattr(message, "role", None)
        if role == "bashExecution":
            if getattr(message, "exclude_from_context", False):
                continue
            out.append(
                UserMessage(
                    content=[TextContent(text=_bash_execution_to_text(message))],
                    timestamp=getattr(message, "timestamp", None) or 0,
                )
            )
        elif role == "branchSummary":
            out.append(
                UserMessage(
                    content=[
                        TextContent(
                            text=f"{_BRANCH_SUMMARY_PREFIX}{getattr(message, 'summary', '')}{_BRANCH_SUMMARY_SUFFIX}"
                        )
                    ],
                    timestamp=getattr(message, "timestamp", None) or 0,
                )
            )
        elif role == "compactionSummary":
            summary = compaction_summary_with_details(
                getattr(message, "summary", ""),
                getattr(message, "details", None),
            )
            out.append(
                UserMessage(
                    content=[
                        TextContent(
                            text=f"{_COMPACTION_SUMMARY_PREFIX}{summary}{_COMPACTION_SUMMARY_SUFFIX}"
                        )
                    ],
                    timestamp=getattr(message, "timestamp", None) or 0,
                )
            )
        elif role == "custom":
            content = getattr(message, "content", "")
            out.append(
                UserMessage(
                    content=[TextContent(text=content)] if isinstance(content, str) else content,
                    timestamp=getattr(message, "timestamp", None) or 0,
                )
            )
        elif role in ("user", "assistant", "toolResult"):
            out.append(message)
    return out


def _exclude_aborted_turns_from_context(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Keep aborted turns in transcript state while excluding them from future LLM context."""
    retained: list[AgentMessage] = []
    for message in messages:
        if isinstance(message, AssistantMessage) and message.stop_reason == "aborted":
            _drop_current_turn_from_context(retained)
            continue
        retained.append(message)
    return retained


def _drop_current_turn_from_context(retained: list[AgentMessage]) -> None:
    while retained:
        candidate = retained[-1]
        if _is_aborted_turn_boundary(candidate):
            return
        removed = retained.pop()
        if getattr(removed, "role", None) == "user":
            while retained and getattr(retained[-1], "role", None) == "user":
                retained.pop()
            return


def _is_aborted_turn_boundary(message: AgentMessage) -> bool:
    role = getattr(message, "role", None)
    if role in {"bashExecution", "branchSummary", "compactionSummary", "custom"}:
        return True
    return isinstance(message, AssistantMessage) and message.stop_reason not in {"aborted", "toolUse"}


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent) or getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
        elif isinstance(block, ImageContent) or getattr(block, "type", None) == "image":
            parts.append("[image]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _append_toolguard_content(content, decision: ToolGuardrailDecision):
    blocks = list(content or [])
    if not blocks:
        return [TextContent(text=append_toolguard_guidance("", decision))]
    for index, block in enumerate(blocks):
        if isinstance(block, TextContent):
            blocks[index] = replace(block, text=append_toolguard_guidance(block.text, decision))
            return blocks
        if getattr(block, "type", None) == "text":
            text = str(getattr(block, "text", ""))
            blocks[index] = TextContent(text=append_toolguard_guidance(text, decision))
            return blocks
    blocks.append(TextContent(text=append_toolguard_guidance("", decision)))
    return blocks


def _with_toolguard_details(details, decision: ToolGuardrailDecision):
    next_details = dict(details) if isinstance(details, dict) else {}
    warnings = list(next_details.get("toolGuardrailWarnings") or [])
    warnings.append(decision.to_metadata())
    next_details["toolGuardrailWarnings"] = warnings
    return next_details

__all__ = (
    'AutoRetryEndEvent',
    'AutoRetryStartEvent',
    'BashResult',
    'CompactionResult',
    'ExtensionCommandContext',
    'ExtensionCompactionResult',
    'ModelCycleResult',
    'QueueUpdateEvent',
    'SessionInfoChangedEvent',
    'ThinkingLevelChangedEvent',
    '_BRANCH_SUMMARY_PREFIX',
    '_BRANCH_SUMMARY_SUFFIX',
    '_CANCEL_SUBAGENT_SCHEMA',
    '_COMPACTION_SUMMARY_PREFIX',
    '_COMPACTION_SUMMARY_SUFFIX',
    '_DEFAULT_ACTIVE_TOOL_NAMES',
    '_DEFAULT_SUBAGENT_ALLOWED_TOOLS',
    '_EXPAND_SUBAGENT_RESULT_SCHEMA',
    '_LIST_SUBAGENTS_SCHEMA',
    '_MALFORMED_STREAMED_TOOL_ARGS_MARKER',
    '_MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE',
    '_MALFORMED_STREAM_RECOVERY_PREFIX',
    '_MAX_PARTIAL_STREAM_CONTINUATIONS',
    '_MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN',
    '_MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT',
    '_MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX',
    '_NON_RETRYABLE_PROVIDER_LIMIT_MARKERS',
    '_PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE',
    '_PARTIAL_STREAM_STUB_ID',
    '_RETRYABLE_ERROR_MARKERS',
    '_SKILL_SUBAGENT_ALLOWED_TOOL_NAMES',
    '_SPAWN_SUBAGENT_SCHEMA',
    '_SUBAGENT_EXPANSION_BUDGETS',
    '_SUBAGENT_FILE_MUTATION_GOAL_PATTERN',
    '_SUBAGENT_FILE_MUTATION_NEGATION_PREFIX_PATTERN',
    '_SUBAGENT_OPT_IN_TERMS',
    '_SUBAGENT_OPT_OUT_TERMS',
    '_SUBAGENT_RESULT_SUMMARY_LIMIT',
    '_SUBAGENT_TOOL_NAMES',
    '_SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT',
    '_SUBAGENT_VISIBLE_SUMMARY_LIMIT',
    '_TASK_ID_SCHEMA',
    '_THINKING_LEVELS',
    '_append_toolguard_content',
    '_drop_current_turn_from_context',
    '_exclude_aborted_turns_from_context',
    '_is_aborted_turn_boundary',
    '_prompt_requests_subagent_tools',
    '_subagent_goal_requests_file_mutation',
    '_tool_result_text',
    '_with_toolguard_details',
    'default_convert_to_llm',
)
