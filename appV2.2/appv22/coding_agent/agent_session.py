"""AgentSession composition root. Port of pi coding-agent core/sdk.ts + agent-session.ts (subset)."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from dataclasses import replace
from typing import Callable, Optional

from appv22.agent.agent import Agent
from appv22.agent.types import AbortSignal
from appv22.agent.types import AfterToolCallResult
from appv22.agent.types import AgentTool
from appv22.agent.types import AgentMessage
from appv22.agent.types import BeforeToolCallResult
from appv22.agent.types import MessageEndEvent, MessageStartEvent
from appv22.ai.model_resolver import ScopedModel
from appv22.ai.models import (
    clamp_thinking_level,
    get_supported_thinking_levels,
    get_providers,
    get_models,
    register_model_request_headers,
    register_provider_auth_config,
    set_provider_models,
    unregister_provider_auth_config,
    unregister_provider_models,
)
from appv22.ai.stream import ApiProvider, register_api_provider
from appv22.ai.types import AssistantMessage, Cost, ImageContent, Message, Model, TextContent, UserMessage
from appv22.ai.types import ToolCall, ToolResultMessage, Usage
from appv22.compaction.compressor import LEGACY_SUMMARY_PREFIX, SUMMARY_END_MARKER, SUMMARY_PREFIX, estimate_tokens
from appv22.compaction.timing import CompactionManager
from appv22.coding_agent.branch_summarization import generate_branch_summary
from appv22.coding_agent.extensions import ExtensionRunner, emit_session_shutdown_event
from appv22.coding_agent.resource_loader import DefaultResourceLoader
from appv22.coding_agent.session_store import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CustomMessage,
    SessionStore,
    deserialize_message,
)
from appv22.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from appv22.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from appv22.coding_agent.tools import create_all_tool_definitions
from appv22.coding_agent.tools.bash import BashExecOptions, BashOperations, create_local_bash_operations
from appv22.coding_agent.tools.output_accumulator import OutputAccumulator
from appv22.coding_agent.tools.types import (
    ToolContext,
    ToolDefinition,
    create_tool_definition_from_agent_tool,
    wrap_tool_definition,
)

_DEFAULT_ACTIVE_TOOL_NAMES = ["read", "bash", "edit", "write"]
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
    _compact: Callable[[dict | None], ExtensionCompactionResult | None]

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

    def compact(self, options: dict | None = None) -> ExtensionCompactionResult | None:
        return self._compact(options)


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Convert Pi coding-agent custom messages to provider-safe ai Messages."""
    out: list[Message] = []
    for message in messages:
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
        compaction_manager: CompactionManager | None = None,
        retry_enabled: bool = False,
        max_retries: int = 0,
        retry_delay_ms: int = 0,
        retryable_error_predicate: Callable[[AssistantMessage], bool] | None = None,
        session_path: str | None = None,
        parent_session_path: str | None = None,
        extension_runner: ExtensionRunner | None = None,
        session_start_event: dict[str, object] | None = None,
        resource_loader: DefaultResourceLoader | None = None,
        agent_dir: str | None = None,
    ) -> None:
        self.cwd = cwd
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
        self._extension_ui_context: object | None = None
        self._extension_mode = "print"
        self._extension_command_context_actions: object | None = None
        self._extension_abort_handler: Callable[[], object] | None = None
        self._extension_shutdown_handler: Callable[[], object] | None = None
        self._extensions_bound = False
        self._extension_provider_original_models: dict[str, Model] = {}
        self._extension_provider_original_registry: dict[str, list[Model]] = {}
        self._event_listeners: list[Callable[[object], None]] = []
        self._steering_messages: list[str] = []
        self._follow_up_messages: list[str] = []
        self._pending_next_turn_messages: list[AgentMessage] = []
        self._pending_bash_messages: list[BashExecutionMessage] = []
        self._bash_signal: AbortSignal | None = None
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
        self._session_name: str | None = None
        self._retry_enabled = retry_enabled
        self._max_retries = max(0, max_retries)
        self._retry_delay_ms = max(0, retry_delay_ms)
        self._retry_attempt = 0
        self._retry_signal: AbortSignal | None = None
        self._retryable_error_predicate = retryable_error_predicate
        self._session_store = (
            SessionStore(session_path, cwd=cwd, parent_session=parent_session_path) if session_path else None
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
            base_definitions = create_all_tool_definitions(cwd)
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
            transform_context=self._transform_context,
            steering_mode=steering_mode,
            follow_up_mode=follow_up_mode,
            on_payload=self._on_provider_payload,
            on_response=self._on_provider_response,
        )
        self._extension_runner.bind_provider_actions(
            self._register_extension_provider,
            self._unregister_extension_provider,
        )
        self._bind_extension_core()
        self._unsubscribe_agent = self.agent.subscribe(self._handle_agent_event)
        self.set_active_tools_by_name(initial_active_tool_names)
        if restored_context:
            self.agent.state.messages = restored_context.messages
        self._extension_runner.emit(self._session_start_event)
        reason = "reload" if self._session_start_event.get("reason") == "reload" else "startup"
        if self._extend_resources_from_extensions(reason):
            self.set_active_tools_by_name(self.get_active_tool_names())

    def _is_allowed_tool(self, name: str) -> bool:
        return (
            self._allowed_tool_names is None or name in self._allowed_tool_names
        ) and name not in self._excluded_tool_names

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
        if self._pending_next_turn_messages:
            prompt_message = _user_message(current_text, current_images)
            pending_next_turn = list(self._pending_next_turn_messages)
            self._pending_next_turn_messages = []
            prompt_messages = [prompt_message, *pending_next_turn]
            prompt_messages = self._apply_before_agent_start(current_text, current_images, prompt_messages)
            return self._run_agent_prompt(prompt_messages, stream_fn=stream_fn)

        prompt_message = _user_message(current_text, current_images) if current_images else current_text
        prompt_message = self._apply_before_agent_start(current_text, current_images, prompt_message)
        return self._run_agent_prompt(prompt_message, stream_fn=stream_fn)

    def _try_execute_extension_command(self, text: str) -> list[AgentMessage] | None:
        parsed = self._parse_extension_command(text)
        if parsed is None:
            return None
        command, args = parsed
        try:
            result = command.handler(args, self._extension_command_context())
        except TypeError:
            result = command.handler(args)
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
            _compact=self._extension_compact,
        )

    def create_replaced_session_context(self) -> ExtensionCommandContext:
        return self._extension_command_context()

    createReplacedSessionContext = create_replaced_session_context

    def _extension_command_infos(self) -> list[dict]:
        return [
            {"name": command.name, "description": command.description}
            for command in self._extension_runner.get_all_registered_commands()
        ]

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
            },
            {
                "getModel": lambda: self.model,
                "isIdle": lambda: not self.is_streaming,
                "isProjectTrusted": lambda: True,
                "getSignal": lambda: self.agent.signal if self.is_streaming else None,
                "abort": self._extension_abort,
                "hasPendingMessages": lambda: self.pending_message_count > 0,
                "shutdown": self._extension_shutdown,
                "getContextUsage": self.get_context_usage,
                "compact": self._extension_compact,
                "getSystemPrompt": lambda: self.system_prompt,
                "getSystemPromptOptions": self._system_prompt_options_snapshot,
            },
        )

    def _extension_abort(self) -> None:
        if self._extension_abort_handler is not None:
            self._extension_abort_handler()
            return
        self.agent.abort()

    def _extension_shutdown(self) -> None:
        if self._extension_shutdown_handler is not None:
            self._extension_shutdown_handler()

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
        try:
            completed = subprocess.run(
                [command, *args],
                cwd=cwd,
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
                summary=_extract_compaction_result_summary(status.messages),
                first_kept_entry_id=self._session_store.leaf_id if self._session_store else "",
                tokens_before=estimate_tokens(before_messages),
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
        return self.agent.continue_(stream_fn=stream_fn)

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
        available_models = [model for provider in get_providers() for model in get_models(provider)]
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

    def compact(self, focus: str | None = None, summarizer=None):
        if self._compaction_manager is None:
            raise RuntimeError("No compaction manager configured")
        self._emit(CompactionStartEvent(reason="manual"))
        try:
            status = self._compaction_manager.compress_manual_with_status(
                self.messages,
                summarizer=summarizer,
                focus=focus,
            )
            self.agent.state.messages = status.messages
            if self._session_store:
                first_kept = self._session_store.leaf_id or ""
                self._session_store.append_compaction(
                    getattr(status.messages[0].content[0], "text", "") if status.messages else "",
                    first_kept,
                    0,
                )
            self._emit(
                CompactionEndEvent(
                    reason="manual",
                    result=status,
                    aborted=False,
                    will_retry=False,
                )
            )
            return status
        except Exception as error:  # noqa: BLE001
            message = str(error)
            aborted = message == "Compaction cancelled"
            self._emit(
                CompactionEndEvent(
                    reason="manual",
                    result=None,
                    aborted=aborted,
                    will_retry=False,
                    error_message=None if aborted else f"Compaction failed: {message}",
                )
            )
            raise

    def execute_bash(self, command: str, on_chunk=None, options: dict | None = None) -> BashResult:
        options = options or {}
        operations: BashOperations = options.get("operations") or create_local_bash_operations()
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
                command,
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
        try:
            new_messages = list(self.agent.prompt(prompt_message, stream_fn=stream_fn))
            while self._prepare_retry(_last_assistant_message(new_messages)):
                retry_messages = list(self.agent.continue_(stream_fn=stream_fn))
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
        return any(marker in error for marker in _RETRYABLE_ERROR_MARKERS)

    def _wait_for_retry_abort(self, signal: AbortSignal, delay_ms: int) -> bool:
        return _wait_for_retry_abort(signal, delay_ms)

    def _before_tool_call(self, context, signal=None) -> BeforeToolCallResult | None:
        if not self._extension_runner.has_handlers("tool_call"):
            return None
        result = self._extension_runner.emit_tool_call(
            {
                "type": "tool_call",
                "toolName": context.tool_call.name,
                "toolCallId": context.tool_call.id,
                "input": context.args,
            }
        )
        if not result:
            return None
        return BeforeToolCallResult(
            block=bool(result.get("block", False)),
            reason=str(result.get("reason")) if result.get("reason") is not None else None,
        )

    def _after_tool_call(self, context, signal=None) -> AfterToolCallResult | None:
        if not self._extension_runner.has_handlers("tool_result"):
            return None
        result = self._extension_runner.emit_tool_result(
            {
                "type": "tool_result",
                "toolName": context.tool_call.name,
                "toolCallId": context.tool_call.id,
                "input": context.args,
                "content": context.result.content,
                "details": context.result.details,
                "isError": context.is_error,
            }
        )
        if not result:
            return None
        return AfterToolCallResult(
            content=result.get("content"),
            details=result.get("details"),
            is_error=result.get("isError"),
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
        from appv22.coding_agent.export_html import export_session_to_html

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
                return {"tokens": None, "contextWindow": context_window, "percent": None}

        tokens = _estimate_context_tokens(self.messages)
        return {
            "tokens": tokens,
            "contextWindow": context_window,
            "percent": (tokens / context_window) * 100,
        }

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
    if images:
        return UserMessage(content=[TextContent(text=text), *images])
    return UserMessage(content=text)


def _get_user_message_text(message: UserMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))


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
    )
