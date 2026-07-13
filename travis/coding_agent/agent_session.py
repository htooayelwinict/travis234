"""AgentSession composition facade."""

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

from travis.coding_agent.session_types import *  # noqa: F403
from travis.runtime_facade import RuntimeFacade

from travis.coding_agent.session_events import SessionEventController
from travis.coding_agent.session_tooling import SessionToolController
from travis.coding_agent.session_extensions import SessionExtensionController
from travis.coding_agent.session_turns import SessionTurnController
from travis.coding_agent.session_subagents import SessionSubagentController
from travis.coding_agent.subagent_trace import SessionSubagentTraceController
from travis.coding_agent.session_models import SessionModelController
from travis.coding_agent.session_persistence import SessionPersistence
from travis.coding_agent.session_bash import SessionBashController
from travis.coding_agent.session_policy_controller import SessionPolicyController

from travis.coding_agent.session_policy_controller import _coerce_tool_guardrail_config
from travis.coding_agent.session_types import default_convert_to_llm

class _SessionRuntime(
        SessionEventController,
        SessionToolController,
        SessionExtensionController,
        SessionTurnController,
        SessionSubagentController,
        SessionSubagentTraceController,
        SessionModelController,
        SessionPersistence,
        SessionBashController,
        SessionPolicyController,
):
    """Internal runtime assembled from focused behavior owners."""

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
        defer_session_start: bool = False,
        resource_loader: DefaultResourceLoader | None = None,
        agent_dir: str | None = None,
        session_index: SessionIndex | None = None,
        settings_manager: object | None = None,
        stream_fn=None,
        max_iterations: int = 90,
        tool_loop_guardrails: ToolCallGuardrailConfig | Mapping[str, object] | None = None,
        provider_control_plane: ProviderControlPlane | None = None,
        process_service: ProcessSessionService | None = None,
        process_owner: ProcessOwner | None = None,
        model_change_listener: Callable[[Model, Model], None] | None = None,
    ) -> None:
        self.cwd = cwd
        self.provider_control_plane = provider_control_plane or ProviderControlPlane.create_default()
        self.provider_control_plane.ensure_model(model)
        self.model_registry = self.provider_control_plane.models
        self.auth_storage = self.provider_control_plane.auth
        self._workspace = WorkspaceCapability(Path(cwd))
        self._artifacts = ArtifactRegistry()
        self.execution_backend = select_execution_backend(cwd)
        if (process_service is None) != (process_owner is None):
            raise ValueError("process_service and process_owner must be provided together")
        self.process_service = process_service
        self.process_owner = process_owner
        self._process_context = (
            ProcessContextResolver(process_service, process_owner)
            if process_service is not None and process_owner is not None
            else None
        )
        self.settings_manager = settings_manager or SettingsManager.in_memory()
        self._stream_fn = stream_fn or self.provider_control_plane.stream_simple
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
            self._extension_runner._model_registry = self.model_registry  # noqa: SLF001
        self._extension_ui_context: object | None = None
        self._extension_mode = "print"
        self._extension_command_context_actions: object | None = None
        self._extension_abort_handler: Callable[[], object] | None = None
        self._extension_shutdown_handler: Callable[[], object] | None = None
        self._extensions_bound = False
        self._extension_provider_original_models: dict[str, Model] = {}
        self._extension_provider_registrations: dict[str, object] = {}
        self._event_listeners: list[Callable[[object], None]] = []
        self._model_change_listener = model_change_listener
        self._subagent_observer_errors: list[str] = []
        self._model_subagents_spawned_this_turn = 0
        self._model_subagent_spawn_signatures_this_turn: set[tuple[str, str, str]] = set()
        self._subagent_log_dir = Path(self._default_subagent_log_dir(session_path=session_path, session_id=session_id))
        self.subagents = SubagentSupervisor(max_threads=3, max_depth=1, event_sink=self._handle_subagent_event)
        self.subagents.register_backend(CallableSubagentBackend("internal", self._run_internal_subagent))
        self.subagents.register_backend(CodexExecBackend(log_dir=self._subagent_log_dir))
        self._turn_mailbox = CodingTurnMailbox()
        self._pending_next_turn_messages: list[AgentMessage] = []
        self._pending_bash_messages: list[BashExecutionMessage] = []
        self._bash_signal: AbortSignal | None = None
        self._command_signal: AbortSignal | None = None
        self._tool_guardrails = ToolCallGuardrailController(
            _coerce_tool_guardrail_config(tool_loop_guardrails),
            cwd=self.cwd,
        )
        self._turn_capabilities = TurnCapabilities()
        self._policy_pipeline = PolicyPipeline(
            [PackageMutationPolicy(), ToolLoopPolicy(self._tool_guardrails)]
        )
        self._policy_run_id = session_id or "ephemeral"
        self._policy_turn_number = 0
        self._tool_guardrail_halt_decision: ToolGuardrailDecision | None = None
        self._tool_guardrail_halt_response_emitted = False
        self._tool_loop_recovery_steered_keys: set[tuple[str, str, int]] = set()
        self._bash_signatures_this_assistant_turn: set[str] = set()
        self._process_limit_recovery_steered = False
        self._process_limit_halt_message: str | None = None
        self._process_limit_halt_response_emitted = False
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
        skill_access = self._skill_read_access()
        external_read_paths = [
            *skill_access["roots"],
            *skill_access["files"],
            *(path for path, _content in self._context_files),
            *get_packaged_context_paths(),
        ]
        self._workspace = WorkspaceCapability(
            Path(cwd),
            tuple(Path(path) for path in external_read_paths),
        )
        self._compaction_manager = compaction_manager
        self._session_name: str | None = None
        self._retry_enabled = retry_enabled
        self._max_retries = max(0, max_retries)
        self._retry_delay_ms = max(0, retry_delay_ms)
        self._retry_attempt = 0
        self._retry_signal: AbortSignal | None = None
        self._retryable_error_predicate = retryable_error_predicate
        self._partial_stream_continue_retries = 0
        self._session_store = (
            SessionStore(
                session_path,
                cwd=cwd,
                parent_session=parent_session_path,
                session_id=session_id,
                index=session_index,
            )
            if session_path
            else None
        )
        self._session_start_event = session_start_event or {"type": "session_start", "reason": "startup"}
        self._defer_session_start = bool(defer_session_start)
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
            if self.process_service is not None and self.process_owner is not None and self._is_allowed_tool("process"):
                base_definitions.append(
                    create_process_tool_definition(self.process_service, self.process_owner, self._artifacts)
                )
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
            else self._default_active_tool_names()
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
            prepare_next_turn_with_context=self._prepare_next_turn,
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
            on_iteration_limit=coding_iteration_limit_message,
        )
        self._compaction_coordinator = CompactionCoordinator(self.agent)
        self._compaction_adapter = SessionCompactionAdapter(
            session_store=self._session_store,
            state=self.agent.state,
            process_context=self._process_context,
            emit=self._emit,
            set_session_name=lambda value: setattr(self, "_session_name", value),
        )
        self._compaction_transactions: CompactionTransactionCoordinator | None = None
        self.set_compaction_manager(self._compaction_manager)
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
        if not self._defer_session_start:
            self._emit_session_start_event()


class AgentSession(RuntimeFacade):
    """Stable public facade over the composed coding-session runtime."""

    def __init__(self, *args, **kwargs) -> None:
        runtime = _SessionRuntime(*args, **kwargs)
        runtime._session_factory = type(self)
        object.__setattr__(self, "_runtime", runtime)

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
    defer_session_start: bool = False,
    resource_loader: DefaultResourceLoader | None = None,
    agent_dir: str | None = None,
    session_index: SessionIndex | None = None,
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
        defer_session_start=defer_session_start,
        resource_loader=resource_loader,
        agent_dir=agent_dir,
        session_index=session_index,
        settings_manager=settings_manager,
    )
