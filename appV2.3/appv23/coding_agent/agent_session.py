"""AgentSession composition root. Port of pi coding-agent core/sdk.ts + agent-session.ts (subset)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional

from appv23.agent.agent import Agent
from appv23.agent.types import AbortSignal
from appv23.agent.types import AfterToolCallResult
from appv23.agent.types import AgentTool
from appv23.agent.types import AgentToolResult
from appv23.agent.types import AgentMessage
from appv23.agent.types import BeforeToolCallResult
from appv23.agent.types import MessageEndEvent, MessageStartEvent
from appv23.agent.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolGuardrailDecision,
    append_toolguard_guidance,
    classify_tool_failure,
    toolguard_synthetic_result,
)
from appv23.ai.model_resolver import ScopedModel
from appv23.ai.models import (
    clamp_thinking_level,
    get_api_key_for_provider,
    get_supported_thinking_levels,
    get_provider_auth_status,
    get_provider_display_name,
    get_providers,
    get_models,
    has_configured_auth,
    register_model_request_headers,
    register_provider_auth_config,
    set_provider_models,
    unregister_provider_auth_config,
    unregister_provider_models,
)
from appv23.ai.stream import ApiProvider, register_api_provider
from appv23.ai.types import AssistantMessage, Cost, ImageContent, Message, Model, TextContent, UserMessage
from appv23.ai.types import ToolCall, ToolResultMessage, Usage
from appv23.compaction.compressor import LEGACY_SUMMARY_PREFIX, SUMMARY_END_MARKER, SUMMARY_PREFIX, estimate_tokens
from appv23.compaction.timing import CompactionManager
from appv23.coding_agent.branch_summarization import generate_branch_summary
from appv23.coding_agent.extensions import ExtensionRunner, emit_session_shutdown_event
from appv23.coding_agent.resource_loader import DefaultResourceLoader
from appv23.coding_agent.session_store import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CustomMessage,
    SessionStore,
    deserialize_message,
)
from appv23.coding_agent.settings_manager import SettingsManager
from appv23.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from appv23.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from appv23.coding_agent.subagents import (
    CallableSubagentBackend,
    CodexExecBackend,
    SubagentResult,
    SubagentSupervisor,
    SubagentTask,
)
from appv23.coding_agent.tools import create_all_tool_definitions
from appv23.coding_agent.tools.bash import BASH_SCHEMA, BashExecOptions, BashOperations, create_local_bash_operations
from appv23.coding_agent.tools.output_accumulator import OutputAccumulator
from appv23.coding_agent.tools.types import (
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
    "cancel_subagent",
]
_DEFAULT_SUBAGENT_ALLOWED_TOOLS = ("read", "grep", "find", "ls")
_SKILL_SUBAGENT_ALLOWED_TOOL_NAMES = {"read", "grep", "find", "ls", "bash"}
_MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT = 300
_MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX = 300
_MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN = 3
_SUBAGENT_RESULT_SUMMARY_LIMIT = 1000
_SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT = 3
_DEFAULT_ACTIVE_TOOL_NAMES = ["read", "bash", "edit", "write", *_SUBAGENT_TOOL_NAMES]
_SPAWN_SUBAGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "description": "Short child-agent role name, e.g. reviewer or researcher."},
        "goal": {"type": "string", "description": "Bounded task for the child agent to complete."},
        "backend": {"type": "string", "description": "Subagent backend to use. Defaults to internal."},
        "wait": {"type": "boolean", "description": "Wait for the child result before returning. Defaults to true."},
        "timeoutSeconds": {"type": "integer", "description": "Maximum seconds to wait for the child result."},
        "contextPack": {"type": "string", "description": "Optional context to include in the child prompt."},
    },
    "required": ["role", "goal"],
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
    "stream ended",
    "timed out",
    "timeout",
    "terminated",
)
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

    @property
    def followUp(self) -> list[str]:
        return self.follow_up


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


@dataclass
class AutoRetryStartEvent:
    attempt: int
    max_attempts: int
    delay_ms: int
    error_message: str
    type: str = "auto_retry_start"

    @property
    def maxAttempts(self) -> int:
        return self.max_attempts

    @property
    def delayMs(self) -> int:
        return self.delay_ms

    @property
    def errorMessage(self) -> str:
        return self.error_message


@dataclass
class AutoRetryEndEvent:
    success: bool
    attempt: int
    final_error: str | None = None
    type: str = "auto_retry_end"

    @property
    def finalError(self) -> str | None:
        return self.final_error


@dataclass
class BashResult:
    output: str
    exit_code: int | None
    cancelled: bool
    truncated: bool
    full_output_path: str | None = None

    @property
    def exitCode(self) -> int | None:
        return self.exit_code

    @property
    def fullOutputPath(self) -> str | None:
        return self.full_output_path


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

    @property
    def thinkingLevel(self) -> str:
        return self.thinking_level

    @property
    def isScoped(self) -> bool:
        return self.is_scoped


class _SessionModelRegistry:
    def __init__(self, active_model_fn: Callable[[], Model]) -> None:
        self._active_model_fn = active_model_fn

    def getAll(self) -> list[Model]:
        return _models_with_active_fallback(self._active_model_fn())

    get_all = getAll

    def getAvailable(self) -> list[Model]:
        active_model = self._active_model_fn()
        return [
            model
            for model in self.getAll()
            if _models_are_same(model, active_model) or has_configured_auth(model)
        ]

    get_available = getAvailable

    def find(self, provider: str, model_id: str) -> Model | None:
        return next(
            (
                model
                for model in self.getAll()
                if model.provider == provider and model.id == model_id
            ),
            None,
        )

    def hasConfiguredAuth(self, model: Model) -> bool:
        return _models_are_same(model, self._active_model_fn()) or has_configured_auth(model)

    has_configured_auth = hasConfiguredAuth

    def getProviderAuthStatus(self, provider: str) -> dict[str, object]:
        if provider == self._active_model_fn().provider:
            return {"configured": True, "source": "active_session"}
        return get_provider_auth_status(provider)

    get_provider_auth_status = getProviderAuthStatus

    def getProviderDisplayName(self, provider: str) -> str:
        return get_provider_display_name(provider)

    get_provider_display_name = getProviderDisplayName

    def getApiKeyForProvider(self, provider: str) -> str | None:
        return get_api_key_for_provider(provider)

    get_api_key_for_provider = getApiKeyForProvider


def _models_are_same(left: Model | None, right: Model | None) -> bool:
    return bool(left and right and left.provider == right.provider and left.id == right.id)


def _models_with_active_fallback(active_model: Model) -> list[Model]:
    models = [model for provider in get_providers() for model in get_models(provider)]
    if not any(_models_are_same(model, active_model) for model in models):
        models.append(active_model)
    return models


@dataclass
class ExtensionCompactionResult:
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: object | None = None

    @property
    def firstKeptEntryId(self) -> str:
        return self.first_kept_entry_id

    @property
    def tokensBefore(self) -> int:
        return self.tokens_before


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

    getSystemPrompt = get_system_prompt

    def get_system_prompt_options(self) -> BuildSystemPromptOptions:
        return self._get_system_prompt_options()

    getSystemPromptOptions = get_system_prompt_options

    def send_message(self, message: dict, options: dict | None = None) -> list[AgentMessage]:
        return self._send_message(message, options)

    sendMessage = send_message

    def send_user_message(
        self,
        content: str | list[TextContent | ImageContent],
        options: dict | None = None,
    ) -> list[AgentMessage] | None:
        return self._send_user_message(content, options)

    sendUserMessage = send_user_message

    def append_entry(self, custom_type: str, data=None) -> str:
        return self._append_entry(custom_type, data)

    appendEntry = append_entry

    def set_session_name(self, name: str | None) -> None:
        self._set_session_name(name)

    setSessionName = set_session_name

    def get_session_name(self) -> str | None:
        return self._get_session_name()

    getSessionName = get_session_name

    def get_active_tools(self) -> list[str]:
        return self._get_active_tools()

    getActiveTools = get_active_tools

    def get_all_tools(self) -> list[dict]:
        return self._get_all_tools()

    getAllTools = get_all_tools

    def set_active_tools(self, tool_names: list[str]) -> None:
        self._set_active_tools(tool_names)

    setActiveTools = set_active_tools

    def get_commands(self) -> list[dict]:
        return self._get_commands()

    getCommands = get_commands

    def get_thinking_level(self) -> str:
        return self._get_thinking_level()

    getThinkingLevel = get_thinking_level

    def set_thinking_level(self, level: str) -> None:
        self._set_thinking_level(level)

    setThinkingLevel = set_thinking_level

    def set_model(self, model: Model) -> bool:
        return self._set_model(model)

    setModel = set_model

    def set_label(self, entry_id: str, label: str | None) -> None:
        self._set_label(entry_id, label)

    setLabel = set_label

    def exec(self, command: str, args: list[str], options: dict | None = None) -> dict:
        return self._exec(command, args, options)

    def wait_for_idle(self) -> None:
        self._wait_for_idle()

    waitForIdle = wait_for_idle

    def get_signal(self) -> AbortSignal:
        return self._get_signal()

    getSignal = get_signal

    def compact(self, options: dict | None = None) -> ExtensionCompactionResult | None:
        return self._compact(options)

    def spawn_subagent(self, role: str, goal: str, options: dict | None = None) -> dict:
        return self._spawn_subagent(role, goal, options)

    spawnSubagent = spawn_subagent

    def list_subagents(self) -> list[dict]:
        return self._list_subagents()

    listSubagents = list_subagents

    def get_subagent_result(self, task_id: str) -> dict | None:
        return self._get_subagent_result(task_id)

    getSubagentResult = get_subagent_result

    def cancel_subagent(self, task_id: str, reason: str | None = None) -> dict:
        return self._cancel_subagent(task_id, reason)

    cancelSubagent = cancel_subagent


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Convert Pi coding-agent custom messages to provider-safe ai Messages."""
    out: list[Message] = []
    for message in _exclude_aborted_turns_from_context(messages):
        role = getattr(message, "role", None)
        if role == "bashExecution":
            if getattr(message, "excludeFromContext", False):
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
            out.append(
                UserMessage(
                    content=[
                        TextContent(
                            text=f"{_COMPACTION_SUMMARY_PREFIX}{getattr(message, 'summary', '')}{_COMPACTION_SUMMARY_SUFFIX}"
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


def _bash_execution_to_text(message) -> str:
    text = f"Ran `{getattr(message, 'command', '')}`\n"
    output = getattr(message, "output", "")
    if output:
        text += f"```\n{output}\n```"
    else:
        text += "(no output)"
    if getattr(message, "cancelled", False):
        text += "\n\n(command cancelled)"
    else:
        exit_code = getattr(message, "exitCode", None)
        if exit_code not in (None, 0):
            text += f"\n\nCommand exited with code {exit_code}"
    if getattr(message, "truncated", False) and getattr(message, "fullOutputPath", None):
        text += f"\n\n[Output truncated. Full output: {message.fullOutputPath}]"
    return text


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


def _coerce_tool_guardrail_config(
    value: ToolCallGuardrailConfig | Mapping[str, object] | None,
) -> ToolCallGuardrailConfig:
    if isinstance(value, ToolCallGuardrailConfig):
        return value
    if isinstance(value, Mapping):
        return ToolCallGuardrailConfig.from_mapping(value)
    return ToolCallGuardrailConfig()


def _settings_value(settings_manager: object, *names: str):
    for name in names:
        value = getattr(settings_manager, name, None)
        if callable(value):
            result = value()
            if result is not None:
                return result
        elif value is not None:
            return value
    return None


class AgentSession:
    """Wires an agent.Agent with coding tools + a built system prompt."""

    def __init__(
        self,
        *,
        cwd: str,
        model: Model,
        tools: list[AgentTool] | None = None,
        tool_definitions: list[ToolDefinition] | None = None,
        active_tool_names: list[str] | None = None,
        allowed_tool_names: list[str] | None = None,
        excluded_tool_names: list[str] | None = None,
        convert_to_llm: Optional[Callable[[list[AgentMessage]], list[Message]]] = None,
        custom_prompt: str | None = None,
        append_system_prompt: str | None = None,
        transform_context=None,
        thinking_level: str = "off",
        scoped_models: list[ScopedModel] | None = None,
        steering_mode: str = "one-at-a-time",
        follow_up_mode: str = "one-at-a-time",
        transport: str | None = None,
        thinking_budgets: dict[str, int] | None = None,
        max_retry_delay_ms: int | None = None,
        compaction_manager: CompactionManager | None = None,
        retry_enabled: bool = False,
        max_retries: int = 0,
        retry_delay_ms: int = 0,
        retryable_error_predicate: Callable[[AssistantMessage], bool] | None = None,
        session_path: str | None = None,
        parent_session_path: str | None = None,
        session_id: str | None = None,
        extension_runner: ExtensionRunner | None = None,
        session_start_event: dict[str, object] | None = None,
        resource_loader: DefaultResourceLoader | None = None,
        agent_dir: str | None = None,
        settings_manager: object | None = None,
        stream_fn=None,
        max_iterations: int = 90,
        tool_loop_guardrails: ToolCallGuardrailConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.cwd = cwd
        self.settings_manager = settings_manager or SettingsManager.inMemory()
        self._stream_fn = stream_fn
        self._allowed_tool_names = set(allowed_tool_names) if allowed_tool_names is not None else None
        self._excluded_tool_names = set(excluded_tool_names or [])
        self._tool_by_name: dict[str, AgentTool] = {}
        self._tool_definition_by_name: dict[str, ToolDefinition] = {}
        self._tool_source_info_by_name: dict[str, SourceInfo] = {}
        self._base_tool_by_name: dict[str, AgentTool] = {}
        self._base_definition_by_name: dict[str, ToolDefinition] = {}
        self._base_source_info_by_name: dict[str, SourceInfo] = {}
        self._extension_runner = extension_runner or ExtensionRunner()
        self._extension_error_unsubscribe: Callable[[], None] | None = None
        self._extension_error_listener: Callable[[dict[str, object]], None] | None = None
        if getattr(self._extension_runner, "_model_registry", None) is None:
            self._extension_runner._model_registry = _SessionModelRegistry(lambda: self.model)  # noqa: SLF001
        self._extension_ui_context: object | None = None
        self._extension_mode = "print"
        self._extension_command_context_actions: object | None = None
        self._extension_abort_handler: Callable[[], object] | None = None
        self._extension_shutdown_handler: Callable[[], object] | None = None
        self._extensions_bound = False
        self._extension_provider_original_models: dict[str, Model] = {}
        self._extension_provider_original_registry: dict[str, list[Model]] = {}
        self._event_listeners: list[Callable[[object], None]] = []
        self._subagent_observer_errors: list[str] = []
        self._model_subagents_spawned_this_turn = 0
        self.subagents = SubagentSupervisor(max_threads=3, max_depth=1, event_sink=self._handle_subagent_event)
        self.subagents.register_backend(CallableSubagentBackend("internal", self._run_internal_subagent))
        self.subagents.register_backend(
            CodexExecBackend(log_dir=self._default_subagent_log_dir(session_path=session_path, session_id=session_id))
        )
        self._steering_messages: list[str] = []
        self._follow_up_messages: list[str] = []
        self._pending_next_turn_messages: list[AgentMessage] = []
        self._pending_bash_messages: list[BashExecutionMessage] = []
        self._bash_signal: AbortSignal | None = None
        self._command_signal: AbortSignal | None = None
        self._tool_guardrails = ToolCallGuardrailController(
            _coerce_tool_guardrail_config(tool_loop_guardrails),
            cwd=self.cwd,
        )
        self._tool_guardrail_halt_decision: ToolGuardrailDecision | None = None
        self._tool_guardrail_halt_response_emitted = False
        self._tool_loop_recovery_steered_keys: set[tuple[str, str, int]] = set()
        self._scoped_models = list(scoped_models or [])
        self._convert_to_llm = convert_to_llm or default_convert_to_llm
        self._caller_transform_context = transform_context
        self._resource_loader = resource_loader
        if self._resource_loader is None:
            self._resource_loader = DefaultResourceLoader(
                cwd=cwd,
                agent_dir=agent_dir,
                system_prompt=custom_prompt,
                append_system_prompt=[append_system_prompt] if append_system_prompt else None,
            )
            self._resource_loader.reload()
        self._custom_prompt: str | None = None
        self._append_system_prompt: str | None = None
        self._context_files: list[tuple[str, str]] = []
        self._refresh_resource_prompt_inputs()
        self._compaction_manager = compaction_manager
        self._compaction_running = False
        self._session_name: str | None = None
        self._retry_enabled = retry_enabled
        self._max_retries = max(0, max_retries)
        self._retry_delay_ms = max(0, retry_delay_ms)
        self._retry_attempt = 0
        self._retry_signal: AbortSignal | None = None
        self._retryable_error_predicate = retryable_error_predicate
        self._session_store = (
            SessionStore(session_path, cwd=cwd, parent_session=parent_session_path, session_id=session_id)
            if session_path
            else None
        )
        self._session_start_event = session_start_event or {"type": "session_start", "reason": "startup"}
        restored_context = self._session_store.build_context(default_thinking_level=thinking_level) if self._session_store else None
        if restored_context:
            thinking_level = restored_context.thinking_level
            self._session_name = restored_context.session_name

        if tools is not None:
            base_tools = tools
            base_definitions = [create_tool_definition_from_agent_tool(tool) for tool in base_tools]
            base_source_infos = {
                definition.name: definition.source_info
                or create_synthetic_source_info(f"<sdk:{definition.name}>", source="sdk")
                for definition in base_definitions
            }
        elif tool_definitions is not None:
            base_tools = [
                wrap_tool_definition(definition, lambda: ToolContext(cwd=self.cwd, model=self.model))
                for definition in tool_definitions
            ]
            base_definitions = tool_definitions
            base_source_infos = {
                definition.name: definition.source_info
                or create_synthetic_source_info(f"<sdk:{definition.name}>", source="sdk")
                for definition in base_definitions
            }
        else:
            base_definitions = [
                *create_all_tool_definitions(cwd, self._builtin_tool_options()),
                *self._create_subagent_tool_definitions(),
            ]
            base_tools = [
                wrap_tool_definition(definition, lambda: ToolContext(cwd=self.cwd, model=self.model))
                for definition in base_definitions
            ]
            base_source_infos = {
                definition.name: create_synthetic_source_info(f"<builtin:{definition.name}>", source="builtin")
                for definition in base_definitions
            }
        self._base_tool_by_name = {tool.name: tool for tool in base_tools}
        self._base_definition_by_name = {definition.name: definition for definition in base_definitions}
        self._base_source_info_by_name = dict(base_source_infos)
        self.refresh_tools()

        initial_active_tool_names = (
            active_tool_names
            if active_tool_names is not None
            else [tool.name for tool in base_tools]
            if tools is not None
            else [definition.name for definition in base_definitions]
            if tool_definitions is not None
            else list(allowed_tool_names)
            if allowed_tool_names is not None
            else _DEFAULT_ACTIVE_TOOL_NAMES
        )
        self.system_prompt = self._build_system_prompt([])
        self.agent = Agent(
            system_prompt=self.system_prompt,
            model=model,
            thinking_level=thinking_level,
            convert_to_llm=self._convert_to_llm,
            tools=[],
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            should_stop_after_turn=self._should_stop_after_turn,
            transform_context=self._transform_context,
            steering_mode=steering_mode,
            follow_up_mode=follow_up_mode,
            transport=transport or "auto",
            thinking_budgets=thinking_budgets,
            max_retry_delay_ms=max_retry_delay_ms,
            on_payload=self._on_provider_payload,
            on_response=self._on_provider_response,
            session_id=self.session_id or None,
            max_iterations=max_iterations,
        )
        self._extension_runner.bind_provider_actions(
            self._register_extension_provider,
            self._unregister_extension_provider,
        )
        self._bind_extension_core()
        self._register_builtin_subagent_commands()
        self._unsubscribe_agent = self.agent.subscribe(self._handle_agent_event)
        self.set_active_tools_by_name(initial_active_tool_names)
        if restored_context:
            self.agent.state.messages = restored_context.messages
        self._extension_runner.emit(self._session_start_event)
        reason = "reload" if self._session_start_event.get("reason") == "reload" else "startup"
        if self._extend_resources_from_extensions(reason):
            self.set_active_tools_by_name(self.get_active_tool_names())

    def _default_subagent_log_dir(self, *, session_path: str | None, session_id: str | None) -> str:
        namespace = session_id or (Path(session_path).stem if session_path else "ephemeral")
        safe_namespace = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in namespace)
        base_dir = Path(session_path).parent if session_path else Path(self.cwd) / ".appv23"
        return str(base_dir / "subagents" / (safe_namespace or "ephemeral"))

    def _is_allowed_tool(self, name: str) -> bool:
        return (
            self._allowed_tool_names is None or name in self._allowed_tool_names
        ) and name not in self._excluded_tool_names

    def _settings_shell_command_prefix(self) -> str | None:
        return _settings_value(
            self.settings_manager,
            "getShellCommandPrefix",
            "get_shell_command_prefix",
            "shellCommandPrefix",
            "shell_command_prefix",
        )

    def _settings_shell_path(self) -> str | None:
        return _settings_value(
            self.settings_manager,
            "getShellPath",
            "get_shell_path",
            "shellPath",
            "shell_path",
        )

    def _skill_read_access(self) -> dict[str, list[str]]:
        if self._resource_loader is None:
            return {"roots": [], "files": []}
        roots: list[str] = []
        files: list[str] = []
        for skill in self._resource_loader.get_skills()["skills"]:
            file_path = getattr(skill, "file_path", None) or getattr(skill, "filePath", None)
            base_dir = getattr(skill, "base_dir", None) or getattr(skill, "baseDir", None)
            if not isinstance(file_path, str) or not file_path:
                continue
            if os.path.basename(file_path) == "SKILL.md" and isinstance(base_dir, str) and base_dir:
                roots.append(base_dir)
            else:
                files.append(file_path)
        return {"roots": roots, "files": files}

    def _builtin_tool_options(self) -> dict[str, dict[str, object]]:
        auto_resize_images = _settings_value(
            self.settings_manager,
            "getImageAutoResize",
            "get_image_auto_resize",
            "imageAutoResize",
            "image_auto_resize",
        )
        skill_read_access = self._skill_read_access()
        return {
            "read": {
                "auto_resize_images": True if auto_resize_images is None else bool(auto_resize_images),
                "allowed_read_roots": skill_read_access["roots"],
                "allowed_read_files": skill_read_access["files"],
            },
            "bash": {
                "command_prefix": self._settings_shell_command_prefix(),
                "shell_path": self._settings_shell_path(),
            },
        }

    def _refresh_tool_registry(self) -> None:
        definition_by_name = dict(self._base_definition_by_name)
        source_info_by_name = dict(self._base_source_info_by_name)
        tool_by_name = dict(self._base_tool_by_name)

        for registered in self._extension_runner.get_all_registered_tools():
            definition_by_name[registered.definition.name] = registered.definition
            source_info_by_name[registered.definition.name] = registered.source_info
            tool_by_name[registered.definition.name] = wrap_tool_definition(
                registered.definition,
                lambda: ToolContext(cwd=self.cwd, model=self.model),
            )

        self._tool_definition_by_name = {
            name: definition for name, definition in definition_by_name.items() if self._is_allowed_tool(name)
        }
        self._tool_source_info_by_name = {
            name: source_info
            for name, source_info in source_info_by_name.items()
            if name in self._tool_definition_by_name
        }
        self._tool_by_name = {tool.name: tool for tool in tool_by_name.values() if tool.name in self._tool_definition_by_name}

    def refresh_tools(self, *, include_all_extension_tools: bool = False) -> None:
        previous_active = self.get_active_tool_names() if hasattr(self, "agent") else []
        self._refresh_tool_registry()
        if not hasattr(self, "agent"):
            return
        next_active = list(previous_active)
        if include_all_extension_tools:
            for registered in self._extension_runner.get_all_registered_tools():
                name = registered.definition.name
                if self._is_allowed_tool(name) and name not in next_active:
                    next_active.append(name)
        self.set_active_tools_by_name(next_active)

    refreshTools = refresh_tools

    def _build_system_prompt(self, selected_tool_names: list[str]) -> str:
        selected_definitions = [
            self._tool_definition_by_name[name]
            for name in selected_tool_names
            if name in self._tool_definition_by_name
        ]
        snippets = {d.name: d.prompt_snippet for d in selected_definitions if d.prompt_snippet}
        guidelines: list[str] = []
        for definition in selected_definitions:
            guidelines.extend(definition.prompt_guidelines)
        return build_system_prompt(
            BuildSystemPromptOptions(
                cwd=self.cwd,
                custom_prompt=self._custom_prompt,
                selected_tools=[d.name for d in selected_definitions],
                tool_snippets=snippets,
                prompt_guidelines=guidelines,
                append_system_prompt=self._append_system_prompt,
                context_files=self._context_files,
                skills=self._resource_loader.get_skills()["skills"] if self._resource_loader else [],
            )
        )

    def _refresh_resource_prompt_inputs(self) -> None:
        append_prompts = self._resource_loader.get_append_system_prompt()
        self._custom_prompt = self._resource_loader.get_system_prompt()
        self._append_system_prompt = "\n\n".join(append_prompts) if append_prompts else None
        agents_files = self._resource_loader.get_agents_files()["agentsFiles"]
        self._context_files = [(entry["path"], entry["content"]) for entry in agents_files]

    def _extend_resources_from_extensions(self, reason: str) -> bool:
        if not self._extension_runner.has_handlers("resources_discover"):
            return False
        discovered = self._extension_runner.emit_resources_discover(self.cwd, reason)
        if not any(discovered.values()):
            return False
        self._resource_loader.extend_resources(
            {
                "skillPaths": [_extension_resource_path(entry) for entry in discovered["skillPaths"]],
                "promptPaths": [_extension_resource_path(entry) for entry in discovered["promptPaths"]],
                "themePaths": [_extension_resource_path(entry) for entry in discovered["themePaths"]],
            }
        )
        self._refresh_resource_prompt_inputs()
        return True

    def reload_resources(self, reason: str = "reload") -> None:
        self._resource_loader.reload()
        self._refresh_resource_prompt_inputs()
        self._extend_resources_from_extensions(reason)
        self.set_active_tools_by_name(self.get_active_tool_names())

    reloadResources = reload_resources

    def bind_extensions(self, bindings: dict[str, object] | None = None) -> None:
        bindings = bindings or {}
        if _has_binding(bindings, "uiContext", "ui_context"):
            self._extension_ui_context = _binding_value(bindings, "uiContext", "ui_context")
        if _has_binding(bindings, "mode"):
            self._extension_mode = str(_binding_value(bindings, "mode") or "print")
        if _has_binding(bindings, "commandContextActions", "command_context_actions"):
            self._extension_command_context_actions = _binding_value(
                bindings,
                "commandContextActions",
                "command_context_actions",
            )
        if _has_binding(bindings, "abortHandler", "abort_handler"):
            abort_handler = _binding_value(bindings, "abortHandler", "abort_handler")
            self._extension_abort_handler = abort_handler if callable(abort_handler) else None
        if _has_binding(bindings, "shutdownHandler", "shutdown_handler"):
            shutdown_handler = _binding_value(bindings, "shutdownHandler", "shutdown_handler")
            self._extension_shutdown_handler = shutdown_handler if callable(shutdown_handler) else None
        if _has_binding(bindings, "onError", "on_error"):
            error_listener = _binding_value(bindings, "onError", "on_error")
            self._extension_error_listener = error_listener if callable(error_listener) else None
        self._apply_extension_bindings()
        self._extensions_bound = True
        self._extension_runner.emit(self._session_start_event)
        reason = "reload" if self._session_start_event.get("reason") == "reload" else "startup"
        if self._extend_resources_from_extensions(reason):
            self.set_active_tools_by_name(self.get_active_tool_names())

    bindExtensions = bind_extensions

    def _apply_extension_bindings(self) -> None:
        self._extension_runner.set_ui_context(self._extension_ui_context, self._extension_mode)
        self._extension_runner.bind_command_context(self._extension_command_context_actions)
        self._extension_runner.set_abort_handler(self._extension_abort_handler)
        self._extension_runner.set_shutdown_handler(self._extension_shutdown_handler)
        if self._extension_error_unsubscribe is not None:
            self._extension_error_unsubscribe()
            self._extension_error_unsubscribe = None
        if self._extension_error_listener is not None:
            self._extension_error_unsubscribe = self._extension_runner.on_error(self._extension_error_listener)

    def reload(self) -> None:
        previous_flag_values = self._extension_runner.get_flag_values()
        emit_session_shutdown_event(self._extension_runner, {"type": "session_shutdown", "reason": "reload"})
        self._resource_loader.reload()
        for name, value in previous_flag_values.items():
            self._extension_runner.set_flag_value(name, value)
        self._refresh_resource_prompt_inputs()
        self.refresh_tools(include_all_extension_tools=True)
        self._apply_extension_bindings()
        if self._extensions_bound or self._extension_error_listener is not None:
            self._extension_runner.emit({"type": "session_start", "reason": "reload"})
            if self._extend_resources_from_extensions("reload"):
                self.set_active_tools_by_name(self.get_active_tool_names())

    def dispose(self) -> None:
        try:
            self.agent.abort()
        except Exception:
            pass
        if self._extension_error_unsubscribe is not None:
            self._extension_error_unsubscribe()
            self._extension_error_unsubscribe = None
        unsubscribe = getattr(self, "_unsubscribe_agent", None)
        if unsubscribe:
            unsubscribe()
            self._unsubscribe_agent = None
        self._event_listeners = []

    def prompt(
        self,
        text: str,
        stream_fn=None,
        *,
        streaming_behavior: str | None = None,
        preflight_result: Callable[[bool], None] | None = None,
        images: list[ImageContent] | None = None,
    ) -> list[AgentMessage]:
        current_text = text
        current_images = images
        if current_text.startswith("/"):
            command_result = self._try_execute_extension_command(current_text)
            if command_result is not None:
                if preflight_result:
                    preflight_result(True)
                return command_result
        if self._extension_runner.has_handlers("input"):
            input_result = self._extension_runner.emit_input(
                current_text,
                current_images,
                "interactive",
                streaming_behavior if self.is_streaming else None,
            )
            if input_result.get("action") == "handled":
                if preflight_result:
                    preflight_result(True)
                return []
            if input_result.get("action") == "transform":
                current_text = str(input_result.get("text", current_text))
                current_images = input_result.get("images", current_images)

        if self.is_streaming:
            try:
                if not streaming_behavior:
                    raise RuntimeError(
                        "Agent is already processing. Specify streamingBehavior ('steer' or 'followUp') to queue the message."
                    )
                if streaming_behavior == "followUp" or streaming_behavior == "follow_up":
                    self.follow_up(current_text, current_images)
                elif streaming_behavior == "steer":
                    self.steer(current_text, current_images)
                else:
                    raise ValueError("streaming_behavior must be 'steer' or 'followUp'")
            except Exception:
                if preflight_result:
                    preflight_result(False)
                raise
            if preflight_result:
                preflight_result(True)
            return []

        if preflight_result:
            preflight_result(True)
        self.agent.state.system_prompt = self.system_prompt
        self._reset_model_subagent_turn_budget()
        if self._pending_next_turn_messages:
            prompt_message = _user_message(current_text, current_images)
            pending_next_turn = list(self._pending_next_turn_messages)
            self._pending_next_turn_messages = []
            prompt_messages = [prompt_message, *pending_next_turn]
            prompt_messages = self._apply_before_agent_start(current_text, current_images, prompt_messages)
            return self._run_agent_prompt(prompt_messages, stream_fn=stream_fn)

        prompt_message = _user_message(current_text, current_images)
        prompt_message = self._apply_before_agent_start(current_text, current_images, prompt_message)
        return self._run_agent_prompt(prompt_message, stream_fn=stream_fn)

    def _reset_model_subagent_turn_budget(self) -> None:
        self._model_subagents_spawned_this_turn = 0

    def _try_execute_extension_command(self, text: str) -> list[AgentMessage] | None:
        parsed = self._parse_extension_command(text)
        if parsed is None:
            return None
        command, args = parsed
        def run_command(_signal: AbortSignal):
            try:
                return command.handler(args, self._extension_command_context())
            except TypeError:
                return command.handler(args)

        result = self._with_command_abort_signal(run_command)
        return result if isinstance(result, list) else []

    def _parse_extension_command(self, text: str):
        if not text.startswith("/"):
            return None
        command_text = text[1:]
        if not command_text:
            return None
        command_name, separator, args = command_text.partition(" ")
        command = self._extension_runner.get_registered_command(command_name)
        if command is None:
            return None
        return command, args if separator else ""

    def _raise_if_extension_command(self, text: str) -> None:
        parsed = self._parse_extension_command(text)
        if parsed is None:
            return
        command, _args = parsed
        raise RuntimeError(
            f'Extension command "/{command.name}" cannot be queued. Use prompt() or execute the command when not streaming.'
        )

    def _extension_command_context(self) -> ExtensionCommandContext:
        return ExtensionCommandContext(
            cwd=self.cwd,
            _get_system_prompt=lambda: self.system_prompt,
            _get_system_prompt_options=lambda: self._system_prompt_options_snapshot(),
            _send_message=self.send_custom_message,
            _send_user_message=self._extension_send_user_message,
            _append_entry=self.append_custom_entry,
            _set_session_name=self.set_session_name,
            _get_session_name=lambda: self.session_name,
            _get_active_tools=self.get_active_tool_names,
            _get_all_tools=self.get_all_tools,
            _set_active_tools=self.set_active_tools_by_name,
            _get_commands=self._extension_command_infos,
            _get_thinking_level=lambda: self.thinking_level,
            _set_thinking_level=self.set_thinking_level,
            _set_model=self._extension_set_model,
            _set_label=self.set_label,
            _exec=self._extension_exec,
            _wait_for_idle=self._extension_wait_for_idle,
            _get_signal=self._current_abort_signal,
            _compact=self._extension_compact,
            _spawn_subagent=self._extension_spawn_subagent,
            _list_subagents=lambda: self.subagents.list_tasks(),
            _get_subagent_result=self._extension_get_subagent_result,
            _cancel_subagent=self._extension_cancel_subagent,
        )

    def create_replaced_session_context(self) -> ExtensionCommandContext:
        return self._extension_command_context()

    createReplacedSessionContext = create_replaced_session_context

    def _extension_command_infos(self) -> list[dict]:
        return [
            {"name": command.name, "description": command.description}
            for command in self._extension_runner.get_all_registered_commands()
        ]

    def _register_builtin_subagent_commands(self) -> None:
        self._extension_runner.register_command(
            "agents",
            {
                "description": "List delegated subagents and their status",
                "handler": self._agents_command,
            },
        )
        self._extension_runner.register_command(
            "delegate",
            {
                "description": "Delegate a bounded task: /delegate <role> <task>",
                "handler": self._delegate_command,
            },
        )
        self._extension_runner.register_command(
            "cancel-agent",
            {
                "description": "Cancel a delegated subagent: /cancel-agent <task-id> [reason]",
                "handler": self._cancel_agent_command,
            },
        )

    def _agents_command(self, args: str = "", _ctx: object | None = None) -> list[AgentMessage]:
        tasks = self.subagents.list_tasks()
        if not tasks:
            content = "No subagents have been spawned in this session."
        else:
            lines = ["Subagents:"]
            for task in tasks:
                lines.append(
                    f"- {task['taskId']} [{task['backend']}] {task['role']}: {task['status']} - {task['goal']}"
                )
            content = "\n".join(lines)
        return self.send_custom_message({"customType": "subagent", "content": content, "display": True}, {"transient": True})

    def _delegate_command(self, args: str = "", _ctx: object | None = None) -> list[AgentMessage]:
        backend = "internal"
        remaining = args.strip()
        if remaining.startswith("--backend "):
            _, _, rest = remaining.partition(" ")
            backend, _, remaining = rest.partition(" ")
            backend = backend.strip() or "internal"
            remaining = remaining.strip()
        role, separator, goal = remaining.partition(" ")
        if not separator or not role.strip() or not goal.strip():
            return self.send_custom_message(
                {
                    "customType": "subagent",
                    "content": "Usage: /delegate [--backend codex|internal] <role> <task>",
                    "display": True,
                }
            )
        result = self._with_command_abort_signal(
            lambda signal: self._spawn_and_wait_for_subagent(
                role.strip(),
                goal.strip(),
                {"backend": backend},
                signal=signal,
            )
        )
        return self.send_custom_message(
            {
                "customType": "subagent",
                "content": self._format_subagent_result(result),
                "display": True,
                "details": _public_subagent_result_details(result),
            }
        )

    def _cancel_agent_command(self, args: str = "", _ctx: object | None = None) -> list[AgentMessage]:
        task_id, _separator, reason = args.strip().partition(" ")
        if not task_id:
            return self.send_custom_message(
                {
                    "customType": "subagent",
                    "content": "Usage: /cancel-agent <task-id> [reason]",
                    "display": True,
                },
                {"transient": True},
            )
        try:
            result = self.subagents.cancel(task_id, reason.strip() or "Cancelled by user.")
        except KeyError as error:
            message = str(error.args[0]) if error.args else str(error)
            return self.send_custom_message(
                {
                    "customType": "subagent",
                    "content": message,
                    "display": True,
                },
                {"transient": True},
            )
        return self.send_custom_message(
            {
                "customType": "subagent",
                "content": self._format_subagent_result(result),
                "display": True,
                "details": _public_subagent_result_details(result),
            },
            {"transient": True},
        )

    def _bind_extension_core(self) -> None:
        self._extension_runner._cwd = self.cwd
        self._extension_runner.bind_core(
            {
                "sendMessage": self.send_custom_message,
                "sendUserMessage": self._extension_send_user_message,
                "appendEntry": self.append_custom_entry,
                "setSessionName": self.set_session_name,
                "getSessionName": lambda: self.session_name,
                "setLabel": self.set_label,
                "getActiveTools": self.get_active_tool_names,
                "getAllTools": self.get_all_tools,
                "setActiveTools": self.set_active_tools_by_name,
                "refreshTools": self.refresh_tools,
                "getCommands": self._extension_command_infos,
                "setModel": self._extension_set_model,
                "getThinkingLevel": lambda: self.thinking_level,
                "setThinkingLevel": self.set_thinking_level,
                "spawnSubagent": self._extension_spawn_subagent,
                "listSubagents": lambda: self.subagents.list_tasks(),
                "getSubagentResult": self._extension_get_subagent_result,
                "cancelSubagent": self._extension_cancel_subagent,
            },
            {
                "getModel": lambda: self.model,
                "isIdle": lambda: not self.is_streaming,
                "isProjectTrusted": lambda: True,
                "getSignal": self._current_abort_signal,
                "abort": self._extension_abort,
                "hasPendingMessages": lambda: self.pending_message_count > 0,
                "shutdown": self._extension_shutdown,
                "getContextUsage": self.get_context_usage,
                "compact": self._extension_compact,
                "getSystemPrompt": lambda: self.system_prompt,
                "getSystemPromptOptions": self._system_prompt_options_snapshot,
            },
        )

    def _extension_spawn_subagent(self, role: str, goal: str, options: dict | None = None) -> dict:
        return self._with_command_abort_signal(
            lambda signal: _public_subagent_result_details(
                self._spawn_and_wait_for_subagent(role, goal, options, signal=signal)
            )
        )

    def _current_abort_signal(self) -> AbortSignal:
        if self.is_streaming or self._command_signal is not None:
            return self.agent.signal
        return self.agent.reset_abort_signal()

    def _with_command_abort_signal(self, callback: Callable[[AbortSignal], object]):
        signal = self._current_abort_signal()
        owns_signal = not self.is_streaming and self._command_signal is None
        if owns_signal:
            self._command_signal = signal
        try:
            return callback(signal)
        finally:
            if owns_signal and self._command_signal is signal:
                self._command_signal = None

    def _extension_get_subagent_result(self, task_id: str) -> dict | None:
        result = self.subagents.get_result(task_id)
        return _public_subagent_result_details(result) if result is not None else None

    def _extension_cancel_subagent(self, task_id: str, reason: str | None = None) -> dict:
        return _public_subagent_result_details(self.subagents.cancel(task_id, reason or "Cancelled by user."))

    def _subagent_allowed_tools_for_role(self, role: str) -> tuple[str, ...]:
        if self._resource_loader is None:
            return _DEFAULT_SUBAGENT_ALLOWED_TOOLS
        for skill in self._resource_loader.get_skills()["skills"]:
            if getattr(skill, "name", None) != role:
                continue
            raw_allowed_tools = getattr(skill, "allowed_tools", None) or getattr(skill, "allowedTools", None) or ()
            tools: list[str] = []
            for tool in raw_allowed_tools:
                if tool not in _SKILL_SUBAGENT_ALLOWED_TOOL_NAMES or tool in tools:
                    continue
                tools.append(tool)
            if tools:
                if "read" not in tools:
                    tools.insert(0, "read")
                return tuple(tools)
        return _DEFAULT_SUBAGENT_ALLOWED_TOOLS

    def _build_subagent_task(self, role: str, goal: str, options: dict | None = None) -> SubagentTask:
        options = options or {}
        if "cwd" in options:
            raise ValueError("Subagent safety overrides are not supported: cwd")
        sandbox = options.get("sandbox")
        if sandbox is not None and sandbox != "read_only":
            raise ValueError("Subagent safety overrides are not supported: sandbox")
        allowed_tools = options.get("allowedTools", options.get("allowed_tools"))
        if allowed_tools is not None and tuple(allowed_tools) != _DEFAULT_SUBAGENT_ALLOWED_TOOLS:
            raise ValueError("Subagent safety overrides are not supported: allowedTools")
        timeout_value = options.get("timeoutSeconds", options.get("timeout_seconds"))
        task_options = {
            "role": role,
            "goal": goal,
            "cwd": str(options.get("cwd") or self.cwd),
            "backend": str(options.get("backend") or "internal"),
            "sandbox": str(options.get("sandbox") or "read_only"),
            "model": options.get("model"),
            "reasoning": options.get("reasoning", self.thinking_level),
            "context_pack": str(options.get("contextPack", options.get("context_pack", "")) or ""),
            "timeout_seconds": _coerce_subagent_timeout_seconds(timeout_value, default=1800),
            "allowed_tools": tuple(allowed_tools) if allowed_tools is not None else self._subagent_allowed_tools_for_role(role),
            "parent_session_id": self.session_id,
            "parent_turn_id": options.get("parentTurnId", options.get("parent_turn_id")),
        }
        return SubagentTask(**task_options)

    def _spawn_subagent_task(self, role: str, goal: str, options: dict | None = None) -> tuple[str, SubagentTask]:
        task = self._build_subagent_task(role, goal, options)
        task_id = self.subagents.spawn(task)
        return task_id, task

    def _spawn_and_wait_for_subagent(
        self,
        role: str,
        goal: str,
        options: dict | None = None,
        *,
        signal: AbortSignal | None = None,
    ) -> SubagentResult:
        task_id, task = self._spawn_subagent_task(role, goal, options)
        return self.subagents.wait(
            task_id,
            timeout=task.timeout_seconds + 1,
            signal=signal,
            cancel_reason="Cancelled by parent abort.",
        )

    def _create_subagent_tool_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="spawn_subagent",
                label="spawn_subagent",
                description=(
                    "Spawn a delegated child coding agent for a bounded task. Returns child task id, role, "
                    "status, summary, and lifecycle-visible result details."
                ),
                parameters=_SPAWN_SUBAGENT_SCHEMA,
                prompt_snippet="Delegate bounded review, research, or implementation tasks to child subagents.",
                prompt_guidelines=[
                    "Use spawn_subagent when the user asks for a subagent, child agent, reviewer, researcher, or parallel delegation.",
                    "Report the returned taskId, role, status, and summary to the user.",
                ],
                execute=self._execute_spawn_subagent_tool,
            ),
            ToolDefinition(
                name="wait_subagent",
                label="wait_subagent",
                description="Wait for an existing subagent task to reach a terminal result.",
                parameters=_TASK_ID_SCHEMA,
                prompt_snippet="Wait for a delegated child task by task id.",
                execute=self._execute_wait_subagent_tool,
            ),
            ToolDefinition(
                name="list_subagents",
                label="list_subagents",
                description="List delegated subagents and their current statuses.",
                parameters=_LIST_SUBAGENTS_SCHEMA,
                prompt_snippet="Inspect active and completed child subagents.",
                execute=self._execute_list_subagents_tool,
            ),
            ToolDefinition(
                name="get_subagent_result",
                label="get_subagent_result",
                description="Return a completed subagent result if one is available.",
                parameters=_TASK_ID_SCHEMA,
                prompt_snippet="Fetch a child subagent result by task id without blocking indefinitely.",
                execute=self._execute_get_subagent_result_tool,
            ),
            ToolDefinition(
                name="cancel_subagent",
                label="cancel_subagent",
                description="Cancel a delegated subagent task by task id.",
                parameters=_CANCEL_SUBAGENT_SCHEMA,
                prompt_snippet="Cancel a child subagent that is no longer needed.",
                execute=self._execute_cancel_subagent_tool,
            ),
        ]

    def _execute_spawn_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        _reject_unexpected_args(
            args,
            {
                "role",
                "goal",
                "backend",
                "wait",
                "timeoutSeconds",
                "contextPack",
            },
        )
        role = _required_text_arg(args, "role")
        goal = _required_text_arg(args, "goal")
        context_pack = args.get("contextPack", "")
        self._reject_subagent_safety_override_text(role, goal, context_pack)
        wait_for_result = args.get("wait", True)
        if not isinstance(wait_for_result, bool):
            raise ValueError("wait must be a boolean")
        options: dict[str, object] = {
            "timeoutSeconds": _model_subagent_timeout_seconds_arg(args),
        }
        if "backend" in args:
            options["backend"] = args["backend"]
        if "contextPack" in args:
            options["contextPack"] = context_pack
        if self._model_subagents_spawned_this_turn >= _MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN:
            details = {
                "status": "blocked",
                "reason": "subagent_spawn_limit_per_turn",
                "limit": _MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN,
                "spawnedThisTurn": self._model_subagents_spawned_this_turn,
            }
            return self._subagent_tool_result(
                "Subagent spawn blocked: already spawned "
                f"{_MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN} subagents in this turn. "
                "Summarize the existing child results and ask the user before launching another wave.",
                details,
            )
        task_id, task = self._spawn_subagent_task(role, goal, options)
        self._model_subagents_spawned_this_turn += 1
        if wait_for_result:
            result = self.subagents.wait(
                task_id,
                timeout=task.timeout_seconds + 1,
                signal=signal,
                cancel_reason="Cancelled by parent abort.",
            )
            return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))
        details = {
            "taskId": task_id,
            "role": role,
            "backend": task.backend,
            "status": "queued",
            "goal": task.goal,
        }
        return self._subagent_tool_result(
            f"Spawned subagent {task_id}\nrole: {task.role}\nstatus: queued\nsummary: waiting for result",
            details,
        )

    def _reject_subagent_safety_override_text(self, *values: object) -> None:
        text = "\n".join(str(value) for value in values if value is not None).lower()
        markers = (
            "cwd=",
            "cwd:",
            "sandbox=",
            "sandbox:",
            "allowedtools=",
            "allowedtools:",
            "allowedtools[",
            "allowed_tools=",
            "allowed_tools:",
            "allowed_tools[",
            "full_access",
            "danger-full-access",
            "workspace_write",
            "full access mode",
        )
        for marker in markers:
            if marker in text:
                raise ValueError("Subagent safety overrides are not supported: prompt text")

    def _execute_wait_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        timeout = _optional_timeout_arg(args)
        result = self.subagents.wait(
            task_id,
            timeout=timeout,
            signal=signal,
            cancel_reason="Cancelled by parent abort.",
        )
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _execute_list_subagents_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        tasks = self.subagents.list_tasks()
        if not tasks:
            return self._subagent_tool_result("No subagents have been spawned in this session.", {"tasks": []})
        lines = ["Subagents:"]
        for task in tasks:
            lines.append(f"- {task['taskId']} [{task['backend']}] {task['role']}: {task['status']} - {task['goal']}")
        return self._subagent_tool_result("\n".join(lines), {"tasks": tasks})

    def _execute_get_subagent_result_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        result = self.subagents.get_result(task_id)
        if result is None:
            return self._subagent_tool_result(f"No result is available for subagent {task_id}.", {"taskId": task_id})
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _execute_cancel_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        reason = args.get("reason", "Cancelled by user.")
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        result = self.subagents.cancel(task_id, reason or "Cancelled by user.")
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _subagent_tool_result(self, content: str, details: dict[str, object]) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text=content)], details=details)

    def _install_subagent_tool_aliases(self, child: "AgentSession", allowed_tools: tuple[str, ...]) -> list[str]:
        active_tools = list(allowed_tools)
        if "run" in active_tools:
            return active_tools
        bash_definition = child.get_tool_definition("bash")
        bash_tool = child._tool_by_name.get("bash")  # noqa: SLF001 - same class scoped child setup.

        if "bash" in allowed_tools and bash_definition is not None and bash_tool is not None:
            run_execute = bash_definition.execute
            run_prepare_arguments = bash_definition.prepare_arguments
            run_execution_mode = bash_definition.execution_mode
            run_render_shell = bash_definition.render_shell
            run_render_call = bash_definition.render_call
            run_render_result = bash_definition.render_result
            description = "Compatibility alias for bash in delegated coding-agent sessions."
            prompt_snippet = "Run shell commands. This is a compatibility alias for bash."
            prompt_guidelines = [
                "The run tool is an alias for bash and is only available when bash is already allowed.",
            ]
            agent_execute = bash_tool.execute
        else:
            def run_execute(_tool_call_id, args, signal=None, on_update=None, ctx=None):
                command = args.get("command", "") if isinstance(args, Mapping) else ""
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=(
                                "Blocked: this subagent is read-only and cannot run shell commands. "
                                "Use read, grep, find, or ls, or report that the goal requires a bash-enabled skill role."
                            )
                        )
                    ],
                    details={
                        "blocked": True,
                        "reason": "subagent_run_requires_bash",
                        "command": command,
                        "allowedTools": list(allowed_tools),
                    },
                )

            run_prepare_arguments = None
            run_execution_mode = "default"
            run_render_shell = None
            run_render_call = lambda args, ctx=None: f"run {args.get('command', '')}" if isinstance(args, Mapping) else "run"
            run_render_result = None
            description = "Blocked compatibility shim for shell commands in read-only subagents."
            prompt_snippet = "Shell commands are blocked for this read-only subagent."
            prompt_guidelines = [
                "Do not use run unless bash is listed as an allowed tool.",
                "If a goal requires shell access and bash is not allowed, report the blocker instead of retrying.",
            ]
            agent_execute = run_execute

        run_definition = ToolDefinition(
            name="run",
            label="run",
            description=description,
            parameters=bash_definition.parameters if bash_definition is not None else BASH_SCHEMA,
            execute=run_execute,
            prompt_snippet=prompt_snippet,
            prompt_guidelines=prompt_guidelines,
            render_shell=run_render_shell,
            render_call=run_render_call,
            render_result=run_render_result,
            execution_mode=run_execution_mode,
            prepare_arguments=run_prepare_arguments,
            source_info=bash_definition.source_info if bash_definition is not None else None,
        )
        child._tool_definition_by_name["run"] = run_definition  # noqa: SLF001 - same class scoped child setup.
        child._tool_source_info_by_name["run"] = child._tool_source_info_by_name.get(  # noqa: SLF001
            "bash",
            create_synthetic_source_info("<builtin:run>", source="builtin"),
        )
        child._tool_by_name["run"] = AgentTool(  # noqa: SLF001 - same class scoped child setup.
            name="run",
            description=run_definition.description,
            parameters=run_definition.parameters,
            label="run",
            execute=agent_execute,
            prepare_arguments=run_prepare_arguments,
            execution_mode=run_execution_mode,
        )
        active_tools.append("run")
        return active_tools

    def _run_internal_subagent(self, task: SubagentTask) -> SubagentResult:
        started = int(time.time() * 1000)
        tool_trace: list[dict[str, object]] = []
        trace_by_call_id: dict[str, dict[str, object]] = {}
        child = AgentSession(
            cwd=task.cwd,
            model=self.model,
            active_tool_names=list(task.allowed_tools),
            allowed_tool_names=list(task.allowed_tools),
            thinking_level=self.thinking_level,
            stream_fn=self._stream_fn,
            max_iterations=12,
        )
        child_active_tools = self._install_subagent_tool_aliases(child, task.allowed_tools)
        if child_active_tools != list(task.allowed_tools):
            child.set_active_tools_by_name(child_active_tools)
        child.agent.subscribe(self._subagent_tool_trace_listener(task, child, tool_trace, trace_by_call_id))
        child.agent._after_tool_call = self._subagent_after_tool_call_tracer(  # noqa: SLF001 - parent observes delegated child tools.
            task,
            child,
            tool_trace,
            trace_by_call_id,
            child.agent._after_tool_call,  # noqa: SLF001
        )
        try:
            messages = child.prompt(task.prompt())
            self._reconcile_subagent_tool_results_from_messages(task, child, messages, tool_trace, trace_by_call_id)
            child_messages = list(child.agent.state.messages)
            self._reconcile_subagent_tool_results_from_messages(
                task,
                child,
                child_messages,
                tool_trace,
                trace_by_call_id,
            )
            summary = self._messages_to_summary(messages) or self._messages_to_summary(child_messages)
            guardrail = child._tool_guardrail_halt_decision.to_metadata() if child._tool_guardrail_halt_decision else None
            errors = []
            status = "completed"
            if guardrail:
                status = "failed"
                code = str(guardrail.get("code") or "tool_guardrail")
                tool = str(guardrail.get("tool_name") or guardrail.get("toolName") or "tool")
                errors.append(f"Subagent stopped by tool guardrail: {code} ({tool})")
                self._mark_subagent_trace_guardrail(task, tool_trace, guardrail)
            ended = int(time.time() * 1000)
            return SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status=status,
                summary=summary or "Internal subagent completed without a final message.",
                final_response=summary,
                errors=errors,
                tool_trace=tool_trace,
                guardrail=guardrail,
                child_session_id=child.session_id,
                started_at_ms=started,
                ended_at_ms=ended,
            )
        finally:
            child.shutdown()

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
            guardrail_code = _toolguard_code_from_text(result_preview)
            if guardrail_code:
                status = "guardrail_halt"
                entry["guardrailCode"] = guardrail_code
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
            if guardrail_code:
                self._handle_subagent_event(_subagent_tool_event(task, "subagent_tool_guardrail", entry))

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
        guardrail_code = _toolguard_code_from_text(result_preview)
        decision = child._tool_guardrail_halt_decision
        if decision is not None and decision.tool_name == tool_name:
            guardrail_code = decision.code
        if guardrail_code:
            status = "guardrail_halt"
            entry["guardrailCode"] = guardrail_code
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
        if guardrail_code:
            self._handle_subagent_event(_subagent_tool_event(task, "subagent_tool_guardrail", entry))

    def _mark_subagent_trace_guardrail(
        self,
        task: SubagentTask,
        tool_trace: list[dict[str, object]],
        guardrail: dict[str, object],
    ) -> None:
        if not tool_trace:
            return
        code = str(guardrail.get("code") or "tool_guardrail")
        tool = str(guardrail.get("tool_name") or "")
        for entry in reversed(tool_trace):
            if tool and entry.get("toolName") != tool:
                continue
            entry["status"] = "guardrail_halt"
            entry["guardrailCode"] = code
            self._handle_subagent_event(_subagent_tool_event(task, "subagent_tool_guardrail", entry))
            return

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
        heading = f"Subagent {result.task_id} {result.role} [{result.backend}] {result.status}"
        files_changed = ", ".join(result.files_changed) if result.files_changed else "none"
        errors = "; ".join(result.errors) if result.errors else "none"
        lines = [
            heading,
            _truncate_subagent_text(result.summary, limit=_SUBAGENT_RESULT_SUMMARY_LIMIT),
            f"filesChanged: {files_changed}",
            f"errors: {errors}",
        ]
        if result.guardrail:
            code = result.guardrail.get("code", "unknown")
            tool = result.guardrail.get("tool_name", result.guardrail.get("toolName", "tool"))
            count = result.guardrail.get("count", "?")
            lines.append(f"guardrail: {code} ({tool}, count={count})")
        if result.tool_trace:
            lines.append("toolTrace:")
            for entry in result.tool_trace[-_SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT:]:
                lines.append(f"- {_format_subagent_tool_trace_entry(entry)}")
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

    def _extension_abort(self) -> None:
        if self._extension_abort_handler is not None:
            self._extension_abort_handler()
            return
        self.agent.abort()

    def _extension_shutdown(self) -> None:
        if self._extension_shutdown_handler is not None:
            self._extension_shutdown_handler()

    def shutdown(self, reason: str = "quit", target_session_file: str | None = None) -> None:
        self.subagents.shutdown(wait=False, reason="Session shutdown.")
        event: dict[str, object] = {"type": "session_shutdown", "reason": reason}
        if target_session_file is not None:
            event["targetSessionFile"] = target_session_file
        emit_session_shutdown_event(self._extension_runner, event)
        if self._extension_error_unsubscribe is not None:
            self._extension_error_unsubscribe()
            self._extension_error_unsubscribe = None

    def set_label(self, entry_id: str, label: str | None) -> None:
        if self._session_store is None:
            return
        self._session_store.append_label_change(entry_id, label)

    setLabel = set_label

    def _extension_wait_for_idle(self) -> None:
        self.agent.wait_for_idle()

    def _extension_set_model(self, model: Model) -> bool:
        self.set_model(model)
        return True

    def _extension_exec(self, command: str, args: list[str], options: dict | None = None) -> dict:
        options = options or {}
        cwd = str(options.get("cwd") or self.cwd)
        timeout = options.get("timeout")
        env = _with_python_bin_on_path(os.environ.copy())
        try:
            completed = subprocess.run(
                [command, *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout / 1000 if isinstance(timeout, (int, float)) else None,
                check=False,
            )
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "code": completed.returncode,
                "killed": False,
            }
        except subprocess.TimeoutExpired as error:
            return {
                "stdout": error.stdout or "",
                "stderr": error.stderr or "",
                "code": -1,
                "killed": True,
            }

    def _extension_compact(self, options: dict | None = None) -> ExtensionCompactionResult | None:
        options = options or {}
        custom_instructions = options.get("customInstructions")
        on_complete = options.get("onComplete")
        on_error = options.get("onError")
        before_messages = list(self.messages)
        try:
            status = self.compact(str(custom_instructions) if custom_instructions is not None else None)
            result = ExtensionCompactionResult(
                summary=status.summary or _extract_compaction_result_summary(status.messages),
                first_kept_entry_id=status.first_kept_entry_id or "",
                tokens_before=status.tokens_before or estimate_tokens(before_messages),
                details={"status": status},
            )
        except Exception as error:  # noqa: BLE001 - mirrors Pi callback-based extension compaction failure.
            if callable(on_error):
                on_error(error)
                return None
            raise
        if callable(on_complete):
            on_complete(result)
        return result

    def _extension_send_user_message(
        self,
        content: str | list[TextContent | ImageContent],
        options: dict | None = None,
    ) -> list[AgentMessage] | None:
        options = options or {}
        text = _text_from_user_message_content(content)
        deliver_as = options.get("deliverAs", options.get("deliver_as"))
        if deliver_as == "steer":
            self.steer(text)
            return None
        if deliver_as == "followUp" or deliver_as == "follow_up":
            self.follow_up(text)
            return None
        return self.prompt(text)

    def _system_prompt_options_snapshot(self) -> BuildSystemPromptOptions:
        active_tool_names = self.get_active_tool_names()
        selected_definitions = [
            self._tool_definition_by_name[name]
            for name in active_tool_names
            if name in self._tool_definition_by_name
        ]
        snippets = {definition.name: definition.prompt_snippet for definition in selected_definitions if definition.prompt_snippet}
        guidelines: list[str] = []
        for definition in selected_definitions:
            guidelines.extend(definition.prompt_guidelines)
        return BuildSystemPromptOptions(
            cwd=self.cwd,
            custom_prompt=self._custom_prompt,
            selected_tools=[definition.name for definition in selected_definitions],
            tool_snippets=snippets,
            prompt_guidelines=guidelines,
            append_system_prompt=self._append_system_prompt,
            context_files=list(self._context_files),
            skills=self._resource_loader.get_skills()["skills"] if self._resource_loader else [],
        )

    def continue_(self, stream_fn=None) -> list[AgentMessage]:
        return self.agent.continue_(stream_fn=stream_fn or self._stream_fn)

    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]:
        self._event_listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return _unsubscribe

    def steer(self, text: str, images: list[ImageContent] | None = None) -> None:
        self._raise_if_extension_command(text)
        self._steering_messages.append(text)
        self._emit_queue_update()
        self.agent.steer(_user_message(text, images))

    def follow_up(self, text: str, images: list[ImageContent] | None = None) -> None:
        self._raise_if_extension_command(text)
        self._follow_up_messages.append(text)
        self._emit_queue_update()
        self.agent.follow_up(_user_message(text, images))

    followUp = follow_up

    def _steer_tool_loop_recovery(self, decision: ToolGuardrailDecision) -> None:
        if decision.action != "warn" or decision.code not in {
            "idempotent_consecutive_warning",
            "idempotent_no_progress_warning",
            "repeated_exact_failure_warning",
            "same_tool_failure_warning",
        }:
            return
        key = (decision.code, decision.tool_name, decision.count)
        if key in self._tool_loop_recovery_steered_keys:
            return
        self._tool_loop_recovery_steered_keys.add(key)
        self.agent.steer(
            _user_message(
                f"Tool loop recovery instruction ({decision.code}, count={decision.count}): "
                "the last tool result already contains the information "
                "or failure signal you need. Do not repeat the same tool call unchanged. If bash returned "
                "a directory listing, find output, rg output, grep output, or file preview, do not call bash "
                "again for the same inventory. For codebase scans, treat that output as inventory you already "
                "have; choose relevant paths from it, then use read with path/offset/limit for file contents. "
                "Use edit/write only for requested changes. If the result is insufficient, change the "
                "query/path/glob once, or explain the blocker without calling the same command again."
            )
        )

    def send_custom_message(self, message: dict, options: dict | None = None, stream_fn=None) -> list[AgentMessage]:
        options = options or {}
        app_message = CustomMessage(
            custom_type=message["customType"],
            content=message.get("content", ""),
            display=bool(message.get("display", True)),
            details=message.get("details"),
            timestamp=int(time.time() * 1000),
        )
        deliver_as = options.get("deliverAs", options.get("deliver_as"))
        if options.get("transient", options.get("local_only", False)):
            self._emit(MessageStartEvent(message=app_message))
            self._emit(MessageEndEvent(message=app_message))
            return [app_message]
        if deliver_as == "nextTurn":
            self._pending_next_turn_messages.append(app_message)
            return []
        if self.is_streaming:
            if deliver_as == "followUp" or deliver_as == "follow_up":
                self.agent.follow_up(app_message)
            else:
                self.agent.steer(app_message)
            return []
        if options.get("triggerTurn", options.get("trigger_turn", False)):
            return self._run_agent_prompt(app_message, stream_fn=stream_fn)

        self.agent.state.messages.append(app_message)
        if self._session_store:
            self._session_store.append_custom_message_entry(
                app_message.customType,
                app_message.content,
                app_message.display,
                app_message.details,
            )
        self._emit(MessageStartEvent(message=app_message))
        self._emit(MessageEndEvent(message=app_message))
        return [app_message]

    sendCustomMessage = send_custom_message

    def _apply_before_agent_start(self, text: str, images, prompt_message):
        if not self._extension_runner.has_handlers("before_agent_start"):
            return prompt_message
        result = self._extension_runner.emit_before_agent_start(text, images, self.system_prompt, None)
        if not result:
            return prompt_message
        if result.get("systemPrompt") is not None:
            self.agent.state.system_prompt = str(result["systemPrompt"])
        injected: list[AgentMessage] = []
        for message in result.get("messages", []) or []:
            if not isinstance(message, dict):
                continue
            injected.append(
                CustomMessage(
                    custom_type=message.get("customType", ""),
                    content=message.get("content", ""),
                    display=bool(message.get("display", True)),
                    details=message.get("details"),
                    timestamp=int(time.time() * 1000),
                )
            )
        if not injected:
            return prompt_message
        if isinstance(prompt_message, list):
            return [*prompt_message, *injected]
        return [prompt_message, *injected]

    def _transform_context(self, messages: list[AgentMessage], signal: AbortSignal | None = None) -> list[AgentMessage]:
        transformed = (
            self._caller_transform_context(messages, signal)
            if self._caller_transform_context is not None
            else messages
        )
        if self._extension_runner.has_handlers("context"):
            return self._extension_runner.emit_context(transformed)
        return transformed

    def _on_provider_payload(self, payload, model=None):
        if not self._extension_runner.has_handlers("before_provider_request"):
            return payload
        return self._extension_runner.emit_before_provider_request(payload)

    def _on_provider_response(self, response, model=None) -> None:
        if not self._extension_runner.has_handlers("after_provider_response"):
            return None
        status = response.get("status") if isinstance(response, dict) else getattr(response, "status", None)
        headers = response.get("headers") if isinstance(response, dict) else getattr(response, "headers", None)
        self._extension_runner.emit(
            {
                "type": "after_provider_response",
                "status": status,
                "headers": headers,
            }
        )
        return None

    def clear_queue(self) -> dict[str, list[str]]:
        steering = list(self._steering_messages)
        follow_up = list(self._follow_up_messages)
        self._steering_messages = []
        self._follow_up_messages = []
        self.agent.clear_all_queues()
        self._emit_queue_update()
        return {"steering": steering, "follow_up": follow_up}

    @property
    def pending_message_count(self) -> int:
        return len(self._steering_messages) + len(self._follow_up_messages)

    @property
    def has_pending_bash_messages(self) -> bool:
        return bool(self._pending_bash_messages)

    @property
    def hasPendingBashMessages(self) -> bool:
        return self.has_pending_bash_messages

    @property
    def is_bash_running(self) -> bool:
        return self._bash_signal is not None

    @property
    def isBashRunning(self) -> bool:
        return self.is_bash_running

    def get_steering_messages(self) -> list[str]:
        return list(self._steering_messages)

    def get_follow_up_messages(self) -> list[str]:
        return list(self._follow_up_messages)

    @property
    def is_streaming(self) -> bool:
        return self.agent.state.is_streaming

    @property
    def state(self):
        return self.agent.state

    @property
    def model(self) -> Model:
        return self.agent.state.model

    @property
    def thinking_level(self) -> str:
        return self.agent.state.thinking_level

    @property
    def thinkingLevel(self) -> str:
        return self.thinking_level

    @property
    def scoped_models(self) -> list[ScopedModel]:
        return list(self._scoped_models)

    @property
    def scopedModels(self) -> list[ScopedModel]:
        return self.scoped_models

    @property
    def retry_attempt(self) -> int:
        return self._retry_attempt

    @property
    def retryAttempt(self) -> int:
        return self._retry_attempt

    @property
    def is_retrying(self) -> bool:
        return self._retry_signal is not None

    @property
    def isRetrying(self) -> bool:
        return self.is_retrying

    @property
    def auto_retry_enabled(self) -> bool:
        return self._retry_enabled

    @property
    def autoRetryEnabled(self) -> bool:
        return self.auto_retry_enabled

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        self._retry_enabled = bool(enabled)

    setAutoRetryEnabled = set_auto_retry_enabled

    def abort_retry(self) -> None:
        if self._retry_signal is not None:
            self._retry_signal.abort()

    abortRetry = abort_retry

    @property
    def session_name(self) -> str | None:
        return self._session_name

    @property
    def sessionName(self) -> str | None:
        return self._session_name

    @property
    def extension_runner(self) -> ExtensionRunner:
        return self._extension_runner

    @property
    def extensionRunner(self) -> ExtensionRunner:
        return self._extension_runner

    @property
    def resource_loader(self) -> DefaultResourceLoader:
        return self._resource_loader

    @property
    def resourceLoader(self) -> DefaultResourceLoader:
        return self._resource_loader

    @property
    def prompt_templates(self) -> list[object]:
        return self._resource_loader.get_prompts()["prompts"]

    @property
    def promptTemplates(self) -> list[object]:
        return self.prompt_templates

    def has_extension_handlers(self, event_type: str) -> bool:
        return self._extension_runner.has_handlers(event_type)

    hasExtensionHandlers = has_extension_handlers

    @property
    def messages(self) -> list[AgentMessage]:
        return self.agent.state.messages

    @property
    def steering_mode(self) -> str:
        return self.agent.steering_mode

    @property
    def steeringMode(self) -> str:
        return self.steering_mode

    @property
    def follow_up_mode(self) -> str:
        return self.agent.follow_up_mode

    @property
    def followUpMode(self) -> str:
        return self.follow_up_mode

    def set_steering_mode(self, mode: str) -> None:
        self.agent.steering_mode = mode

    setSteeringMode = set_steering_mode

    def set_follow_up_mode(self, mode: str) -> None:
        self.agent.follow_up_mode = mode

    setFollowUpMode = set_follow_up_mode

    def get_active_tool_names(self) -> list[str]:
        return [tool.name for tool in self.agent.state.tools]

    getActiveToolNames = get_active_tool_names

    def get_all_tools(self) -> list[dict]:
        return [
            _tool_info(definition, self._tool_source_info_by_name[definition.name])
            for definition in self._tool_definition_by_name.values()
        ]

    getAllTools = get_all_tools

    def get_tool_definition(self, name: str) -> ToolDefinition | None:
        return self._tool_definition_by_name.get(name)

    getToolDefinition = get_tool_definition

    def set_active_tools_by_name(self, tool_names: list[str]) -> None:
        tools: list[AgentTool] = []
        valid_tool_names: list[str] = []
        for name in tool_names:
            tool = self._tool_by_name.get(name)
            if tool is None:
                continue
            tools.append(tool)
            valid_tool_names.append(name)
        self.agent.state.tools = tools
        self.system_prompt = self._build_system_prompt(valid_tool_names)
        self.agent.state.system_prompt = self.system_prompt

    setActiveToolsByName = set_active_tools_by_name

    def set_session_name(self, name: str | None) -> None:
        self._session_name = name
        if self._session_store:
            self._session_store.append_session_info(name)
        self._emit(SessionInfoChangedEvent(name=name))

    setSessionName = set_session_name

    def set_thinking_level(self, level: str) -> None:
        available_levels = self.get_available_thinking_levels()
        effective_level = level if level in available_levels else self._clamp_thinking_level(level, available_levels)
        previous = self.agent.state.thinking_level
        self.agent.state.thinking_level = effective_level
        if effective_level != previous:
            if self._session_store:
                self._session_store.append_thinking_level_change(effective_level)
            self._emit(ThinkingLevelChangedEvent(level=effective_level))

    setThinkingLevel = set_thinking_level

    def cycle_thinking_level(self) -> str | None:
        if not self.supports_thinking():
            return None
        levels = self.get_available_thinking_levels()
        if not levels:
            return None
        try:
            current_index = levels.index(self.thinking_level)
        except ValueError:
            current_index = -1
        next_level = levels[(current_index + 1) % len(levels)]
        self.set_thinking_level(next_level)
        return next_level

    cycleThinkingLevel = cycle_thinking_level

    def get_available_thinking_levels(self) -> list[str]:
        if not self.model:
            return list(_THINKING_LEVELS)
        return get_supported_thinking_levels(self.model)

    getAvailableThinkingLevels = get_available_thinking_levels

    def supports_thinking(self) -> bool:
        return bool(self.model and self.model.reasoning)

    supportsThinking = supports_thinking

    def _get_thinking_level_for_model_switch(self, explicit_level: str | None = None) -> str:
        if explicit_level is not None:
            return explicit_level
        if not self.supports_thinking():
            return "off"
        return self.thinking_level

    def _clamp_thinking_level(self, level: str, _available_levels: list[str]) -> str:
        return clamp_thinking_level(self.model, level) if self.model else "off"

    def set_model(self, model: Model) -> None:
        thinking_level = self._get_thinking_level_for_model_switch()
        self.agent.state.model = model
        if self._session_store:
            self._session_store.append_model_change(model.provider, model.id)
        self.set_thinking_level(thinking_level)

    setModel = set_model

    def set_scoped_models(self, scoped_models: list[ScopedModel]) -> None:
        self._scoped_models = list(scoped_models)

    setScopedModels = set_scoped_models

    def cycle_model(self, direction: str = "forward") -> ModelCycleResult | None:
        if self._scoped_models:
            return self._cycle_scoped_model(direction)
        return self._cycle_available_model(direction)

    cycleModel = cycle_model

    def _cycle_scoped_model(self, direction: str) -> ModelCycleResult | None:
        if len(self._scoped_models) <= 1:
            return None

        current_index = next(
            (
                index
                for index, scoped in enumerate(self._scoped_models)
                if scoped.model.provider == self.model.provider and scoped.model.id == self.model.id
            ),
            0,
        )
        count = len(self._scoped_models)
        if direction == "backward":
            next_index = (current_index - 1 + count) % count
        else:
            next_index = (current_index + 1) % count

        next_scoped = self._scoped_models[next_index]
        thinking_level = self._get_thinking_level_for_model_switch(next_scoped.thinking_level)
        self.set_model(next_scoped.model)
        self.set_thinking_level(thinking_level)
        return ModelCycleResult(model=next_scoped.model, thinking_level=self.thinking_level, is_scoped=True)

    def _cycle_available_model(self, direction: str) -> ModelCycleResult | None:
        available_models = _models_with_active_fallback(self.model)
        if len(available_models) <= 1:
            return None

        current_index = next(
            (
                index
                for index, model in enumerate(available_models)
                if model.provider == self.model.provider and model.id == self.model.id
            ),
            0,
        )
        count = len(available_models)
        if direction == "backward":
            next_index = (current_index - 1 + count) % count
        else:
            next_index = (current_index + 1) % count

        next_model = available_models[next_index]
        thinking_level = self._get_thinking_level_for_model_switch()
        self.set_model(next_model)
        self.set_thinking_level(thinking_level)
        return ModelCycleResult(model=next_model, thinking_level=self.thinking_level, is_scoped=False)

    def _register_extension_provider(self, name: str, config: dict) -> None:
        _validate_extension_provider_config(name, config)
        register_provider_auth_config(name, config)
        api = str(config.get("api") or name)
        stream_simple = config.get("streamSimple") or config.get("stream_simple")
        if callable(stream_simple):
            register_api_provider(ApiProvider(api=api, stream=stream_simple, stream_simple=stream_simple))

        model_configs = config.get("models")
        if isinstance(model_configs, list):
            self._extension_provider_original_registry.setdefault(name, list(get_models(name)))
            models = []
            for model_config in model_configs:
                if not isinstance(model_config, dict):
                    continue
                model = _model_from_provider_config(name, config, model_config)
                register_model_request_headers(name, model.id, model_config.get("headers"))
                models.append(model)
            set_provider_models(name, models)

        current = self.agent.state.model
        if current.provider != name:
            return
        self._extension_provider_original_models.setdefault(name, current)
        updated = _apply_provider_config_to_model(current, config)
        if updated != current:
            self.agent.state.model = updated

    def _unregister_extension_provider(self, name: str) -> None:
        unregister_provider_auth_config(name)
        original = self._extension_provider_original_models.pop(name, None)
        original_registry = self._extension_provider_original_registry.pop(name, None)
        if original_registry is None:
            unregister_provider_models(name)
        else:
            set_provider_models(name, original_registry)
        if original is not None and self.agent.state.model.provider == name:
            self.agent.state.model = original

    def compact(self, focus: str | None = None, summarizer=None, deep: bool = False):
        if self._compaction_manager is None:
            raise RuntimeError("No compaction manager configured")
        self._begin_compaction("manual")
        before_messages = list(self.messages)
        before_entry_ids = self._session_context_message_entry_ids()
        try:
            status = self._compaction_manager.compress_manual_with_status(
                self.messages,
                summarizer=summarizer,
                focus=focus,
                deep=deep,
            )
            if self._session_store and status.compressed:
                first_kept = self._first_kept_entry_id_for_status(status, before_entry_ids)
                summary = status.summary or _extract_compaction_result_summary(status.messages)
                tokens_before = status.tokens_before or estimate_tokens(before_messages)
                self._session_store.append_compaction(
                    summary,
                    first_kept,
                    tokens_before,
                )
                status.first_kept_entry_id = first_kept
                snapshot = self._session_store.build_context(default_thinking_level=self.thinking_level)
                self.agent.state.messages = snapshot.messages
                self.agent.state.thinking_level = snapshot.thinking_level
                self._session_name = snapshot.session_name
                status.messages = snapshot.messages
            else:
                self.agent.state.messages = status.messages
            self._end_compaction(reason="manual", result=status, aborted=False, will_retry=False)
            return status
        except Exception as error:  # noqa: BLE001
            message = str(error)
            aborted = message == "Compaction cancelled"
            self._end_compaction(
                reason="manual",
                result=None,
                aborted=aborted,
                will_retry=False,
                error_message=None if aborted else f"Compaction failed: {message}",
            )
            raise

    def _first_kept_entry_id_for_status(self, status, context_entry_ids: list[str]) -> str:
        index = status.first_kept_message_index
        if index is not None and 0 <= index < len(context_entry_ids):
            return context_entry_ids[index]
        if self._session_store:
            return self._session_store.leaf_id or ""
        return ""

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

    def set_compaction_manager(self, manager: CompactionManager | None) -> None:
        self._compaction_manager = manager

    setCompactionManager = set_compaction_manager

    @property
    def is_compacting(self) -> bool:
        return self._compaction_running

    @property
    def isCompacting(self) -> bool:
        return self.is_compacting

    def _begin_compaction(self, reason: str) -> None:
        self._compaction_running = True
        self._emit(CompactionStartEvent(reason=reason))

    def _end_compaction(
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
            self._compaction_running = False

    def execute_bash(self, command: str, on_chunk=None, options: dict | None = None) -> BashResult:
        options = options or {}
        command_prefix = options.get("commandPrefix")
        if command_prefix is None:
            command_prefix = options.get("command_prefix")
        if command_prefix is None:
            command_prefix = self._settings_shell_command_prefix()
        shell_path = options.get("shellPath")
        if shell_path is None:
            shell_path = options.get("shell_path")
        if shell_path is None:
            shell_path = self._settings_shell_path()
        operations: BashOperations = options.get("operations") or create_local_bash_operations(shell_path=shell_path)
        resolved_command = f"{command_prefix}\n{command}" if command_prefix else command
        output = OutputAccumulator(temp_file_prefix="pi-user-bash")
        self._bash_signal = AbortSignal()

        def handle_data(data: bytes) -> None:
            output.append(data)
            if on_chunk:
                on_chunk(data.decode("utf-8", errors="replace"))

        exit_code: int | None = None
        cancelled = False
        try:
            result = operations.exec(
                resolved_command,
                self.cwd,
                BashExecOptions(on_data=handle_data, signal=self._bash_signal),
            )
            exit_code = result.get("exit_code")
        except RuntimeError as error:
            cancelled = str(error) == "aborted"
            if not cancelled:
                raise
        finally:
            output.finish()
            self._bash_signal = None
        snapshot = output.snapshot(persist_if_truncated=True)
        output.close_temp_file()
        bash_result = BashResult(
            output=snapshot.content,
            exit_code=exit_code,
            cancelled=cancelled,
            truncated=bool(snapshot.truncation.truncated),
            full_output_path=snapshot.full_output_path,
        )
        self.record_bash_result(command, bash_result, options)
        return bash_result

    executeBash = execute_bash

    def abort_bash(self) -> None:
        if self._bash_signal is not None:
            self._bash_signal.abort()

    abortBash = abort_bash

    def record_bash_result(self, command: str, result: BashResult, options: dict | None = None) -> None:
        options = options or {}
        message = BashExecutionMessage(
            command=command,
            output=result.output,
            exit_code=result.exit_code,
            cancelled=result.cancelled,
            truncated=result.truncated,
            full_output_path=result.full_output_path,
            timestamp=int(time.time() * 1000),
            exclude_from_context=options.get("excludeFromContext", options.get("exclude_from_context")),
        )
        if self.is_streaming:
            self._pending_bash_messages.append(message)
            return
        self._append_bash_message(message)

    def _append_bash_message(self, message: BashExecutionMessage) -> None:
        self.agent.state.messages.append(message)
        if self._session_store:
            self._session_store.append_message(message)

    recordBashResult = record_bash_result

    def _flush_pending_bash_messages(self) -> None:
        if not self._pending_bash_messages:
            return
        for message in self._pending_bash_messages:
            self._append_bash_message(message)
        self._pending_bash_messages = []

    def _run_agent_prompt(self, prompt_message, stream_fn=None) -> list[AgentMessage]:
        self._flush_pending_bash_messages()
        self._tool_guardrails.reset_for_turn()
        self._tool_guardrail_halt_decision = None
        self._tool_guardrail_halt_response_emitted = False
        self._tool_loop_recovery_steered_keys = set()
        active_stream_fn = stream_fn or self._stream_fn
        try:
            new_messages = list(self.agent.prompt(prompt_message, stream_fn=active_stream_fn))
            while self._prepare_retry(_last_assistant_message(new_messages)):
                retry_messages = list(self.agent.continue_(stream_fn=active_stream_fn))
                new_messages.extend(retry_messages)
                latest = _last_assistant_message(retry_messages)
                if latest and latest.stop_reason != "error":
                    self._emit(AutoRetryEndEvent(success=True, attempt=self._retry_attempt))
                    self._retry_attempt = 0
                    break
                if latest and latest.stop_reason == "error" and self._retry_attempt >= self._max_retries:
                    self._emit(
                        AutoRetryEndEvent(
                            success=False,
                            attempt=self._retry_attempt,
                            final_error=latest.error_message,
                        )
                    )
                    self._retry_attempt = 0
                    break
            return new_messages
        finally:
            self._flush_pending_bash_messages()

    def _prepare_retry(self, message: AssistantMessage | None) -> bool:
        if not self._retry_enabled or message is None or message.stop_reason != "error":
            return False
        if self._retry_attempt >= self._max_retries:
            if self._retry_attempt > 0:
                self._emit(
                    AutoRetryEndEvent(
                        success=False,
                        attempt=self._retry_attempt,
                        final_error=message.error_message,
                    )
                )
                self._retry_attempt = 0
            return False
        if not self._is_retryable_error(message):
            return False
        self._retry_attempt += 1
        self._retry_signal = AbortSignal()
        self._emit(
            AutoRetryStartEvent(
                attempt=self._retry_attempt,
                max_attempts=self._max_retries,
                delay_ms=self._retry_delay_ms * (2 ** (self._retry_attempt - 1)),
                error_message=message.error_message or "",
            )
        )
        if self.messages and isinstance(self.messages[-1], AssistantMessage):
            self.agent.state.messages = self.messages[:-1]
        delay_ms = self._retry_delay_ms * (2 ** (self._retry_attempt - 1))
        try:
            if delay_ms > 0 and _wait_for_retry_abort(self._retry_signal, delay_ms):
                attempt = self._retry_attempt
                self._retry_attempt = 0
                self._emit(
                    AutoRetryEndEvent(
                        success=False,
                        attempt=attempt,
                        final_error="Retry cancelled",
                    )
                )
                return False
        finally:
            self._retry_signal = None
        return True

    def _is_retryable_error(self, message: AssistantMessage) -> bool:
        if self._retryable_error_predicate:
            return self._retryable_error_predicate(message)
        error = (message.error_message or "").lower()
        if any(marker in error for marker in _NON_RETRYABLE_PROVIDER_LIMIT_MARKERS):
            return False
        return any(marker in error for marker in _RETRYABLE_ERROR_MARKERS)

    def _wait_for_retry_abort(self, signal: AbortSignal, delay_ms: int) -> bool:
        return _wait_for_retry_abort(signal, delay_ms)

    def _before_tool_call(self, context, signal=None) -> BeforeToolCallResult | None:
        if self._extension_runner.has_handlers("tool_call"):
            result = self._extension_runner.emit_tool_call(
                {
                    "type": "tool_call",
                    "toolName": context.tool_call.name,
                    "toolCallId": context.tool_call.id,
                    "input": context.args,
                }
            )
            if result and result.get("block", False):
                return BeforeToolCallResult(
                    block=True,
                    reason=str(result.get("reason")) if result.get("reason") is not None else None,
                )

        decision = self._tool_guardrails.before_call(context.tool_call.name, context.args)
        if not decision.allows_execution:
            self._tool_guardrail_halt_decision = decision
            return BeforeToolCallResult(block=True, reason=toolguard_synthetic_result(decision))
        return None

    def _after_tool_call(self, context, signal=None) -> AfterToolCallResult | None:
        content = context.result.content
        details = context.result.details
        is_error = context.is_error
        content_changed = False
        details_changed = False
        is_error_changed = False

        if self._extension_runner.has_handlers("tool_result"):
            result = self._extension_runner.emit_tool_result(
                {
                    "type": "tool_result",
                    "toolName": context.tool_call.name,
                    "toolCallId": context.tool_call.id,
                    "input": context.args,
                    "content": content,
                    "details": details,
                    "isError": is_error,
                }
            )
            if result:
                if result.get("content") is not None:
                    content = result.get("content")
                    content_changed = True
                if result.get("details") is not None:
                    details = result.get("details")
                    details_changed = True
                if result.get("isError") is not None:
                    is_error = bool(result.get("isError"))
                    is_error_changed = True

        result_text = _tool_result_text(content)
        if not is_error:
            detected_failure, _ = classify_tool_failure(context.tool_call.name, result_text)
            if detected_failure:
                is_error = True
                is_error_changed = True

        decision = self._tool_guardrails.after_call(
            context.tool_call.name,
            context.args,
            result_text,
            failed=is_error,
        )
        if decision.action in {"warn", "halt"}:
            content = _append_toolguard_content(content, decision)
            content_changed = True
            self._steer_tool_loop_recovery(decision)
        if decision.should_halt:
            self._tool_guardrail_halt_decision = decision
            if not is_error:
                is_error = True
                is_error_changed = True

        if not (content_changed or details_changed or is_error_changed or decision.should_halt):
            return None
        return AfterToolCallResult(
            content=content if content_changed else None,
            details=details if details_changed else None,
            is_error=is_error if is_error_changed else None,
            terminate=True if decision.should_halt else None,
        )

    def _should_stop_after_turn(self, context) -> bool:
        decision = self._tool_guardrail_halt_decision
        if decision is None:
            return False
        if not self._tool_guardrail_halt_response_emitted:
            self._emit_toolguard_controlled_halt_response(context, decision)
        return True

    def _emit_toolguard_controlled_halt_response(self, context, decision: ToolGuardrailDecision) -> None:
        self._tool_guardrail_halt_response_emitted = True
        message = AssistantMessage(
            content=[TextContent(text=self._toolguard_controlled_halt_response(decision))],
            api=self.model.api,
            provider=self.model.provider,
            model=self.model.id,
            usage=Usage(),
            stop_reason="stop",
        )
        context.context.messages.append(message)
        context.new_messages.append(message)

        start_event = MessageStartEvent(message=message)
        self.agent._process_event(start_event)
        self._handle_agent_event(start_event)

        end_event = MessageEndEvent(message=message)
        self.agent._process_event(end_event)
        self._handle_agent_event(end_event)

    def _toolguard_controlled_halt_response(self, decision: ToolGuardrailDecision) -> str:
        tool = decision.tool_name or "a tool"
        return (
            f"I stopped retrying {tool} because it hit the tool-call guardrail "
            f"({decision.code}) after {decision.count} repeated non-progressing "
            "attempts. The last tool result explains the blocker; the next step is "
            "to change strategy instead of repeating the same call."
        )

    def _handle_agent_event(self, event) -> None:
        if event.type == "message_end" and self._extension_runner.has_handlers("message_end"):
            replacement = self._extension_runner.emit_message_end({"type": "message_end", "message": event.message})
            if replacement is not None:
                _replace_message_in_place(event.message, replacement)
        if event.type == "message_start" and getattr(event.message, "role", None) == "user":
            message_text = _get_user_message_text(event.message)
            if message_text:
                if message_text in self._steering_messages:
                    self._steering_messages.remove(message_text)
                    self._emit_queue_update()
                elif message_text in self._follow_up_messages:
                    self._follow_up_messages.remove(message_text)
                    self._emit_queue_update()
        if event.type == "agent_end":
            setattr(event, "will_retry", self._will_retry_after_agent_end(event))
            setattr(event, "willRetry", getattr(event, "will_retry"))
        if event.type == "message_end" and self._session_store:
            message_role = getattr(event.message, "role", None)
            if message_role == "custom":
                self._session_store.append_custom_message_entry(
                    event.message.customType,
                    event.message.content,
                    event.message.display,
                    event.message.details,
                )
            elif message_role in ("user", "assistant", "toolResult"):
                self._session_store.append_message(event.message)
        self._emit(event)

    def _will_retry_after_agent_end(self, event) -> bool:
        if not self._retry_enabled or self._retry_attempt >= self._max_retries:
            return False
        for message in reversed(event.messages):
            if isinstance(message, AssistantMessage):
                return message.stop_reason == "error" and self._is_retryable_error(message)
        return False

    def _emit_queue_update(self) -> None:
        self._emit(
            QueueUpdateEvent(
                steering=list(self._steering_messages),
                follow_up=list(self._follow_up_messages),
            )
        )

    def _emit(self, event) -> None:
        for listener in list(self._event_listeners):
            listener(event)

    @property
    def session_entries(self) -> list[dict]:
        return self._session_store.entries if self._session_store else []

    def get_session_entry(self, entry_id: str) -> dict | None:
        return self._session_store.get_entry(entry_id) if self._session_store else None

    getSessionEntry = get_session_entry

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        return self._session_store.create_branched_session(leaf_id, path=path)

    createBranchedSession = create_branched_session

    def export_to_jsonl(self, output_path: str | None = None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        return self._session_store.export_to_jsonl(output_path)

    exportToJsonl = export_to_jsonl

    def export_to_html(self, output_path: str | dict | None = None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        from appv23.coding_agent.export_html import export_session_to_html

        return export_session_to_html(self._session_store, self.agent.state, output_path)

    exportToHtml = export_to_html

    def append_custom_entry(self, custom_type: str, data=None) -> str:
        if self._session_store is None:
            raise RuntimeError("No session store configured")
        return self._session_store.append_custom_entry(custom_type, data)

    appendCustomEntry = append_custom_entry

    @property
    def session_path(self) -> str | None:
        return str(self._session_store.path) if self._session_store else None

    @property
    def session_file(self) -> str | None:
        return self.session_path

    @property
    def sessionFile(self) -> str | None:
        return self.session_path

    @property
    def session_id(self) -> str:
        return str(self._session_store.header.get("id", "")) if self._session_store else ""

    @property
    def sessionId(self) -> str:
        return self.session_id

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

    navigateTree = navigate_tree

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

    getUserMessagesForForking = get_user_messages_for_forking

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

    getLastAssistantText = get_last_assistant_text

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

    getSessionStats = get_session_stats

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

    getContextUsage = get_context_usage


def _wait_for_retry_abort(signal: AbortSignal, delay_ms: int) -> bool:
    deadline = time.monotonic() + max(0, delay_ms) / 1000
    while True:
        if signal.aborted:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return signal.aborted
        time.sleep(min(remaining, 0.05))


def _with_python_bin_on_path(env: dict[str, str]) -> dict[str, str]:
    python_bin = os.path.dirname(sys.executable)
    current_path = env.get("PATH", "")
    if python_bin and python_bin not in current_path.split(os.pathsep):
        env = dict(env)
        env["PATH"] = python_bin + (os.pathsep + current_path if current_path else "")
    return env


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


def _toolguard_code_from_text(text: str) -> str | None:
    match = re.search(r"\[Tool loop hard stop:\s*([^;\]]+)", text or "")
    if match:
        return match.group(1).strip()
    return None


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
    if entry.get("guardrailCode"):
        payload["guardrailCode"] = entry["guardrailCode"]
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
        if entry.get("guardrailCode"):
            public_entry["guardrailCode"] = str(entry["guardrailCode"])
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
        "guardrail": dict(result.guardrail) if result.guardrail is not None else None,
    }
    return details


def _format_subagent_tool_trace_entry(entry: Mapping[str, object]) -> str:
    tool = str(entry.get("toolName") or "tool")
    status = str(entry.get("status") or "unknown")
    args = _truncate_preview(str(entry.get("argsPreview") or "").strip(), limit=80)
    result = _truncate_preview(str(entry.get("resultPreview") or "").strip(), limit=120)
    guardrail = str(entry.get("guardrailCode") or "").strip()
    parts = [tool, status]
    if guardrail:
        parts.append(guardrail)
    if args:
        parts.append(args)
    if result:
        parts.append(f"=> {result}")
    return " ".join(parts)


def _text_from_user_message_content(content: str | list[TextContent | ImageContent]) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.text for block in content if isinstance(block, TextContent))
    return ""


def _extract_compaction_result_summary(messages: list[Message]) -> str:
    for message in messages:
        text = _message_content_text(getattr(message, "content", ""))
        if text.startswith(SUMMARY_PREFIX) or text.startswith(LEGACY_SUMMARY_PREFIX):
            for prefix in (SUMMARY_PREFIX, LEGACY_SUMMARY_PREFIX):
                if text.startswith(prefix):
                    text = text[len(prefix) :]
                    break
            marker_index = text.find(SUMMARY_END_MARKER)
            if marker_index >= 0:
                text = text[:marker_index]
            return text.strip()
    return ""


def _validate_extension_provider_config(provider_name: str, config: dict) -> None:
    stream_simple = config.get("streamSimple") or config.get("stream_simple")
    if callable(stream_simple) and not config.get("api"):
        raise RuntimeError(f'Provider {provider_name}: "api" is required when registering streamSimple.')

    model_configs = config.get("models")
    if not isinstance(model_configs, list) or not model_configs:
        return

    if not config.get("baseUrl") and not config.get("base_url"):
        raise RuntimeError(f'Provider {provider_name}: "baseUrl" is required when defining models.')
    has_oauth = "oauth" in config and config.get("oauth") is not None
    if not config.get("apiKey") and not config.get("api_key") and not has_oauth:
        raise RuntimeError(f'Provider {provider_name}: "apiKey" or "oauth" is required when defining models.')

    for model_config in model_configs:
        if not isinstance(model_config, dict):
            continue
        if not model_config.get("api") and not config.get("api"):
            raise RuntimeError(f'Provider {provider_name}, model {model_config.get("id")}: no "api" specified.')


def _apply_provider_config_to_model(model: Model, config: dict) -> Model:
    updates = {}
    base_url = config.get("baseUrl", config.get("base_url"))
    if base_url is not None:
        updates["base_url"] = str(base_url)
    api = config.get("api")
    if api is not None:
        updates["api"] = str(api)
    return replace(model, **updates) if updates else model


def _model_from_provider_config(provider: str, provider_config: dict, model_config: dict) -> Model:
    api = str(model_config.get("api") or provider_config.get("api") or provider)
    base_url = str(
        model_config.get("baseUrl")
        or model_config.get("base_url")
        or provider_config.get("baseUrl")
        or provider_config.get("base_url")
        or ""
    )
    return Model(
        id=str(model_config["id"]),
        name=str(model_config.get("name") or model_config["id"]),
        api=api,
        provider=provider,
        base_url=base_url,
        reasoning=bool(model_config.get("reasoning", False)),
        thinking_level_map=model_config.get("thinkingLevelMap") or model_config.get("thinking_level_map"),
        input=list(model_config.get("input") or ["text"]),
        cost=_cost_from_provider_model_config(model_config.get("cost")),
        context_window=int(model_config.get("contextWindow") or model_config.get("context_window") or 0),
        max_tokens=int(model_config.get("maxTokens") or model_config.get("max_tokens") or 0),
    )


def _cost_from_provider_model_config(cost: object) -> Cost:
    if not isinstance(cost, dict):
        return Cost()
    return Cost(
        input=float(cost.get("input", 0.0)),
        output=float(cost.get("output", 0.0)),
        cache_read=float(cost.get("cacheRead", cost.get("cache_read", 0.0))),
        cache_write=float(cost.get("cacheWrite", cost.get("cache_write", 0.0))),
    )


def _last_assistant_message(messages: list[AgentMessage]) -> AssistantMessage | None:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return message
    return None


def _replace_message_in_place(target: AgentMessage, replacement: AgentMessage) -> None:
    if target is replacement:
        return
    if hasattr(target, "__dict__") and hasattr(replacement, "__dict__"):
        target.__dict__.clear()
        target.__dict__.update(replacement.__dict__)


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


def _tool_info(definition: ToolDefinition, source_info: SourceInfo) -> dict:
    prompt_guidelines = list(definition.prompt_guidelines)
    source_info_dict = source_info.to_dict()
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters": definition.parameters,
        "promptGuidelines": prompt_guidelines,
        "prompt_guidelines": prompt_guidelines,
        "sourceInfo": source_info_dict,
        "source_info": source_info_dict,
    }


def _has_binding(bindings: dict[str, object], *names: str) -> bool:
    return any(name in bindings for name in names)


def _binding_value(bindings: dict[str, object], *names: str) -> object | None:
    for name in names:
        if name in bindings:
            return bindings[name]
    return None


def _extension_resource_path(entry: dict[str, str]) -> dict[str, object]:
    extension_path = entry.get("extensionPath", "<python-extension>")
    extension_name = extension_path.strip("<>").split(":", 1)[-1] if extension_path.startswith("<") else extension_path
    return {
        "path": entry["path"],
        "metadata": {
            "source": f"extension:{extension_name}",
            "scope": "temporary",
            "origin": "top-level",
        },
    }


def create_agent_session(
    *,
    cwd: str,
    model: Model,
    tools: list[AgentTool] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
    active_tool_names: list[str] | None = None,
    allowed_tool_names: list[str] | None = None,
    excluded_tool_names: list[str] | None = None,
    convert_to_llm: Optional[Callable[[list[AgentMessage]], list[Message]]] = None,
    extension_runner: ExtensionRunner | None = None,
    session_start_event: dict[str, object] | None = None,
    resource_loader: DefaultResourceLoader | None = None,
    agent_dir: str | None = None,
    settings_manager: object | None = None,
) -> AgentSession:
    return AgentSession(
        cwd=cwd,
        model=model,
        tools=tools,
        tool_definitions=tool_definitions,
        active_tool_names=active_tool_names,
        allowed_tool_names=allowed_tool_names,
        excluded_tool_names=excluded_tool_names,
        convert_to_llm=convert_to_llm,
        extension_runner=extension_runner,
        session_start_event=session_start_event,
        resource_loader=resource_loader,
        agent_dir=agent_dir,
        settings_manager=settings_manager,
    )
