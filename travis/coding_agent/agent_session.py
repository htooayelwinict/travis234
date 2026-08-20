"""AgentSession composition facade."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import travis.coding_agent.session_types as _session_types
from travis.agent.agent import Agent
from travis.agent.types import (
    AbortSignal,
    AgentMessage,
    AgentTool,
    QueueMode,
    ThinkingLevel,
)
from travis.ai.model_resolver import ScopedModel
from travis.ai.providers.params import GenerationParams
from travis.ai.types import AssistantMessage, Message, Model
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.capabilities import WorkspaceCapability
from travis.coding_agent.compaction_adapter import (
    CompactionSessionState,
    SessionCompactionAdapter,
)
from travis.coding_agent.compaction_coordinator import (
    CompactionCoordinator,
    CompactionTransactionCoordinator,
)
from travis.coding_agent.config import get_agent_dir, get_packaged_context_paths
from travis.coding_agent.eval_trace import SecretRedactor
from travis.coding_agent.execution_backend import select_execution_backend
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.language_services.manager import LanguageServiceManager
from travis.coding_agent.language_services.tool import create_lsp_tool_definition
from travis.coding_agent.language_services.types import LanguageServerConfig
from travis.coding_agent.mailbox import CodingTurnMailbox
from travis.coding_agent.memory import (
    MemorySettings,
    MemoryStore,
    MemoryStoreUnavailable,
    project_key_for_path,
)
from travis.coding_agent.memory.tool import (
    MemoryToolRuntime,
    create_memory_tool_definition,
)
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.model_roles import ModelRole, ModelRoleRouter
from travis.coding_agent.operations import NullOperationCoordinator, OperationRuntime
from travis.coding_agent.policy import (
    ToolApprovalBroker,
    ToolEffect,
    ToolPolicyEngine,
    ToolPolicyMode,
    ToolPolicySettings,
)
from travis.coding_agent.process_context import ProcessContextResolver
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_bash import SessionBashController
from travis.coding_agent.session_contracts import (
    SessionRuntimePort,
    validate_session_runtime_port,
)
from travis.coding_agent.session_controllers import (
    SESSION_CONTROLLER_DELEGATES,
    SESSION_DELEGATED_NAMES,
    SessionControllers,
    bind_session_controller_owners,
)
from travis.coding_agent.session_events import SessionEventController
from travis.coding_agent.session_extensions import SessionExtensionController
from travis.coding_agent.session_generation_params import SessionGenerationParams
from travis.coding_agent.session_index import SessionIndex
from travis.coding_agent.session_models import SessionModelController
from travis.coding_agent.session_operations import SessionOperationController
from travis.coding_agent.session_persistence import SessionPersistence
from travis.coding_agent.session_policy_controller import SessionPolicyController
from travis.coding_agent.session_ports import (
    SessionBashDependencies,
    SessionEventDependencies,
    SessionExtensionDependencies,
    SessionGenerationDependencies,
    SessionModelDependencies,
    SessionOperationDependencies,
    SessionPersistenceDependencies,
    SessionPolicyDependencies,
    SessionRuntimeBindings,
    SessionSubagentDependencies,
    SessionSubagentTraceDependencies,
    SessionToolDependencies,
    SessionTurnDependencies,
)
from travis.coding_agent.session_state import SessionPresentationState, SessionTurnState
from travis.coding_agent.session_store import (
    BashExecutionMessage,
    SessionStore,
)
from travis.coding_agent.session_subagents import SessionSubagentController
from travis.coding_agent.session_tooling import SessionToolController
from travis.coding_agent.session_turns import SessionTurnController
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.subagent_trace import SessionSubagentTraceController
from travis.coding_agent.subagents import (
    CallableSubagentBackend,
    CodexExecBackend,
    SubagentResult,
    SubagentSupervisor,
)
from travis.coding_agent.tools import create_all_tool_definitions
from travis.coding_agent.tools.process import create_process_tool_definition
from travis.coding_agent.tools.types import (
    ToolContext,
    ToolDefinition,
    create_tool_definition_from_agent_tool,
    wrap_tool_definition,
)
from travis.compaction.timing import CompactionManager
from travis.controller_ports import (
    compose_controller_dependencies,
    install_controller_delegates,
    install_runtime_binding_attributes,
)
from travis.runtime_facade import RuntimeFacade

_BRANCH_SUMMARY_PREFIX = _session_types._BRANCH_SUMMARY_PREFIX
_BRANCH_SUMMARY_SUFFIX = _session_types._BRANCH_SUMMARY_SUFFIX
_CANCEL_SUBAGENT_SCHEMA = _session_types._CANCEL_SUBAGENT_SCHEMA
_COMPACTION_SUMMARY_PREFIX = _session_types._COMPACTION_SUMMARY_PREFIX
_COMPACTION_SUMMARY_SUFFIX = _session_types._COMPACTION_SUMMARY_SUFFIX
_DEFAULT_ACTIVE_TOOL_NAMES = _session_types._DEFAULT_ACTIVE_TOOL_NAMES
_DEFAULT_SUBAGENT_ALLOWED_TOOLS = _session_types._DEFAULT_SUBAGENT_ALLOWED_TOOLS
_EXPAND_SUBAGENT_RESULT_SCHEMA = _session_types._EXPAND_SUBAGENT_RESULT_SCHEMA
_LIST_SUBAGENTS_SCHEMA = _session_types._LIST_SUBAGENTS_SCHEMA
_MALFORMED_STREAM_RECOVERY_PREFIX = _session_types._MALFORMED_STREAM_RECOVERY_PREFIX
_MALFORMED_STREAMED_TOOL_ARGS_MARKER = _session_types._MALFORMED_STREAMED_TOOL_ARGS_MARKER
_MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE = _session_types._MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE
_MAX_PARTIAL_STREAM_CONTINUATIONS = _session_types._MAX_PARTIAL_STREAM_CONTINUATIONS
_MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN = _session_types._MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN
_MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT = _session_types._MODEL_SUBAGENT_TIMEOUT_SECONDS_DEFAULT
_MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX = _session_types._MODEL_SUBAGENT_TIMEOUT_SECONDS_MAX
_NON_RETRYABLE_PROVIDER_LIMIT_MARKERS = _session_types._NON_RETRYABLE_PROVIDER_LIMIT_MARKERS
_PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE = _session_types._PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE
_PARTIAL_STREAM_STUB_ID = _session_types._PARTIAL_STREAM_STUB_ID
_RETRYABLE_ERROR_MARKERS = _session_types._RETRYABLE_ERROR_MARKERS
_SKILL_SUBAGENT_ALLOWED_TOOL_NAMES = _session_types._SKILL_SUBAGENT_ALLOWED_TOOL_NAMES
_SPAWN_SUBAGENT_SCHEMA = _session_types._SPAWN_SUBAGENT_SCHEMA
_SUBAGENT_EXPANSION_BUDGETS = _session_types._SUBAGENT_EXPANSION_BUDGETS
_SUBAGENT_OPT_IN_TERMS = _session_types._SUBAGENT_OPT_IN_TERMS
_SUBAGENT_OPT_OUT_TERMS = _session_types._SUBAGENT_OPT_OUT_TERMS
_SUBAGENT_RESULT_SUMMARY_LIMIT = _session_types._SUBAGENT_RESULT_SUMMARY_LIMIT
_SUBAGENT_TOOL_NAMES = _session_types._SUBAGENT_TOOL_NAMES
_SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT = _session_types._SUBAGENT_TOOL_TRACE_DISPLAY_LIMIT
_SUBAGENT_VISIBLE_SUMMARY_LIMIT = _session_types._SUBAGENT_VISIBLE_SUMMARY_LIMIT
_TASK_ID_SCHEMA = _session_types._TASK_ID_SCHEMA
_THINKING_LEVELS = _session_types._THINKING_LEVELS
AgentSettledEvent = _session_types.AgentSettledEvent
AutoRetryEndEvent = _session_types.AutoRetryEndEvent
AutoRetryStartEvent = _session_types.AutoRetryStartEvent
BashResult = _session_types.BashResult
CompactionResult = _session_types.CompactionResult
ExtensionCommandContext = _session_types.ExtensionCommandContext
ExtensionCompactionResult = _session_types.ExtensionCompactionResult
ModelCycleResult = _session_types.ModelCycleResult
QueueUpdateEvent = _session_types.QueueUpdateEvent
SessionInfoChangedEvent = _session_types.SessionInfoChangedEvent
ThinkingLevelChangedEvent = _session_types.ThinkingLevelChangedEvent
_prompt_rejects_subagent_tools = _session_types._prompt_rejects_subagent_tools
_prompt_requests_subagent_tools = _session_types._prompt_requests_subagent_tools
_tool_result_text = _session_types._tool_result_text
default_convert_to_llm = _session_types.default_convert_to_llm


class _SessionRuntime:
    """Internal runtime assembled from focused behavior owners."""

    _after_tool_call: Callable
    _before_tool_call: Callable
    _bind_extension_core: Callable
    _build_system_prompt: Callable
    _builtin_tool_options: Callable
    _create_subagent_tool_definitions: Callable
    _default_active_tool_names: Callable
    _default_subagent_log_dir: Callable
    _emit: Callable
    _handle_agent_event: Callable
    _handle_subagent_event: Callable
    _initialize_session_operations: Callable
    _is_allowed_tool: Callable
    _on_provider_headers: Callable
    _on_provider_payload: Callable
    _on_provider_response: Callable
    _prepare_next_turn: Callable
    _refresh_resource_prompt_inputs: Callable
    _register_builtin_subagent_commands: Callable
    _register_extension_provider: Callable
    _run_internal_subagent: Callable
    _skill_read_access: Callable
    _transform_context: Callable
    _unregister_extension_provider: Callable
    model: Model
    refresh_tools: Callable
    session_id: str
    set_active_tools_by_name: Callable
    set_compaction_manager: Callable

    def _initialize_controllers(self) -> None:
        self._session_bindings = SessionRuntimeBindings()
        self.turn_state = SessionTurnState()
        self.presentation_state = SessionPresentationState()
        self._session_bindings._pending_next_turn_messages.bind_attribute(self.turn_state, "pending_next_turn_messages")
        self._session_bindings._retry_attempt.bind_attribute(self.turn_state, "retry_attempt")
        self._session_bindings._retry_enabled.bind_attribute(self.turn_state, "retry_enabled")
        self._session_bindings._session_name.bind_attribute(self.presentation_state, "session_name")
        self._session_bindings._generation_param_overrides.bind_attribute(
            self.presentation_state, "generation_overrides"
        )
        self.controllers = SessionControllers(
            events=SessionEventController(
                compose_controller_dependencies(SessionEventDependencies, self._session_bindings)
            ),
            models=SessionModelController(
                compose_controller_dependencies(
                    SessionModelDependencies, self._session_bindings, presentation_state=self.presentation_state
                )
            ),
            generation=SessionGenerationParams(
                compose_controller_dependencies(SessionGenerationDependencies, self._session_bindings)
            ),
            persistence=SessionPersistence(
                compose_controller_dependencies(SessionPersistenceDependencies, self._session_bindings)
            ),
            bash=SessionBashController(
                compose_controller_dependencies(SessionBashDependencies, self._session_bindings)
            ),
            policy=SessionPolicyController(
                compose_controller_dependencies(SessionPolicyDependencies, self._session_bindings)
            ),
            operations=SessionOperationController(
                compose_controller_dependencies(SessionOperationDependencies, self._session_bindings)
            ),
            tools=SessionToolController(
                compose_controller_dependencies(SessionToolDependencies, self._session_bindings)
            ),
            extensions=SessionExtensionController(
                compose_controller_dependencies(SessionExtensionDependencies, self._session_bindings)
            ),
            subagents=SessionSubagentController(
                compose_controller_dependencies(SessionSubagentDependencies, self._session_bindings)
            ),
            subagent_trace=SessionSubagentTraceController(
                compose_controller_dependencies(SessionSubagentTraceDependencies, self._session_bindings)
            ),
            turns=SessionTurnController(
                compose_controller_dependencies(
                    SessionTurnDependencies, self._session_bindings, turn_state=self.turn_state
                )
            ),
        )
        bind_session_controller_owners(self._session_bindings, self.controllers)

    def _initialize_process_context(
        self,
        process_service: ProcessSessionService | None,
        process_owner: ProcessOwner | None,
    ) -> None:
        if (process_service is None) != (process_owner is None):
            raise ValueError("process_service and process_owner must be provided together")
        self.process_service = process_service
        self.process_owner = process_owner
        self._process_context = (
            ProcessContextResolver(process_service, process_owner)
            if process_service is not None and process_owner is not None
            else None
        )

    def _initialize_tool_policy(
        self,
        *,
        tool_approval_broker: ToolApprovalBroker | None,
        tool_policy_event_sink: Callable[[dict[str, object]], None] | None,
        tool_policy_redactor: SecretRedactor | None,
    ) -> None:
        tool_policy_getter = getattr(self.settings_manager, "get_tool_policy_settings", None)
        tool_policy_raw = cast(
            dict[str, object],
            tool_policy_getter() if callable(tool_policy_getter) else {"mode": "audit", "autoAllowEffects": ["read"]},
        )
        self._tool_policy_engine = ToolPolicyEngine(
            ToolPolicySettings(
                mode=cast(ToolPolicyMode, tool_policy_raw["mode"]),
                auto_allow_effects=frozenset(cast(list[ToolEffect], tool_policy_raw["autoAllowEffects"])),
            ),
            broker=tool_approval_broker,
            redactor=tool_policy_redactor,
        )
        self._tool_approval_broker = tool_approval_broker
        self._tool_policy_event_sink = tool_policy_event_sink

    def _initialize_resource_loader(
        self,
        *,
        cwd: str,
        agent_dir: str | None,
        custom_prompt: str | None,
        append_system_prompt: str | None,
        extension_runner: ExtensionRunner | None,
        resource_loader: DefaultResourceLoader | None,
    ) -> None:
        self._resource_loader = resource_loader
        if self._resource_loader is None:
            self._resource_loader = DefaultResourceLoader(
                cwd=cwd,
                agent_dir=agent_dir,
                system_prompt=custom_prompt,
                append_system_prompt=[append_system_prompt] if append_system_prompt else None,
            )
            self._resource_loader.reload()
        if extension_runner is None:
            loaded_runner = self._resource_loader.get_extensions().get("runtime")
            if isinstance(loaded_runner, ExtensionRunner):
                self._extension_runner = loaded_runner
                self._extension_runner._model_registry = self.model_registry  # noqa: SLF001

    def _initialize_memory_runtime(
        self,
        *,
        cwd: str,
        agent_dir: str | None,
        tools: list[AgentTool] | None,
        active_tool_names: list[str] | None,
        session_id: str | None,
    ) -> None:
        memory_getter = getattr(self.settings_manager, "get_memory_settings", None)
        self._memory_settings = cast(MemorySettings, memory_getter() if callable(memory_getter) else MemorySettings())
        self._memory_project_key = project_key_for_path(cwd)
        memory_requested = (
            tools is None
            and self._memory_settings.enabled
            and self._is_allowed_tool("memory")
            and (active_tool_names is None or "memory" in active_tool_names)
        )
        self._memory_store: MemoryStore | None = None
        if memory_requested:
            try:
                self._memory_store = MemoryStore(
                    Path(agent_dir or get_agent_dir()).expanduser().resolve() / "memory.sqlite3",
                    settings=self._memory_settings,
                )
            except MemoryStoreUnavailable:
                self._memory_store = None
        self._memory_tool_runtime = (
            MemoryToolRuntime(
                self._memory_store,
                settings=self._memory_settings,
                project_key=self._memory_project_key,
                session_id=self.session_id or session_id,
                artifacts=self._artifacts,
                spill_dir=Path(agent_dir or get_agent_dir()).expanduser().resolve() / "memory-spill",
                redactor=self._tool_policy_engine.redactor,
            )
            if memory_requested
            else None
        )

    def _initialize_language_services(
        self,
        *,
        cwd: str,
        tools: list[AgentTool] | None,
        tool_definitions: list[ToolDefinition] | None,
    ) -> None:
        self._language_services: LanguageServiceManager | None = None
        language_server_getter = getattr(self.settings_manager, "get_language_server_configs", None)
        language_server_configs = cast(
            list[LanguageServerConfig],
            language_server_getter() if callable(language_server_getter) else [],
        )
        if tools is None and tool_definitions is None and language_server_configs and self._is_allowed_tool("lsp"):
            self._language_services = LanguageServiceManager(cwd, language_server_configs)

    def _create_base_tools(
        self,
        *,
        cwd: str,
        tools: list[AgentTool] | None,
        tool_definitions: list[ToolDefinition] | None,
    ) -> tuple[list[AgentTool], list[ToolDefinition], dict[str, SourceInfo]]:
        if tools is not None:
            definitions = [create_tool_definition_from_agent_tool(tool) for tool in tools]
            source_infos = {
                definition.name: definition.source_info
                or create_synthetic_source_info(f"<sdk:{definition.name}>", source="sdk")
                for definition in definitions
            }
            return tools, definitions, source_infos
        if tool_definitions is not None:
            wrapped_tools = [
                wrap_tool_definition(definition, lambda: ToolContext(cwd=self.cwd, model=self.model))
                for definition in tool_definitions
            ]
            source_infos = {
                definition.name: definition.source_info
                or create_synthetic_source_info(f"<sdk:{definition.name}>", source="sdk")
                for definition in tool_definitions
            }
            return wrapped_tools, tool_definitions, source_infos

        definitions = [
            *create_all_tool_definitions(cwd, self._builtin_tool_options()),
            *self._create_subagent_tool_definitions(),
        ]
        if self._language_services is not None:
            definitions.append(create_lsp_tool_definition(self._language_services, self._artifacts, cwd))
        if (
            self.process_service is not None
            and self.process_owner is not None
            and self._is_allowed_tool("process")
        ):
            definitions.append(create_process_tool_definition(self.process_service, self.process_owner, self._artifacts))
        wrapped_tools = [
            wrap_tool_definition(definition, lambda: ToolContext(cwd=self.cwd, model=self.model))
            for definition in definitions
        ]
        source_infos = {
            definition.name: create_synthetic_source_info(f"<builtin:{definition.name}>", source="builtin")
            for definition in definitions
        }
        return wrapped_tools, definitions, source_infos

    def _append_memory_tool(
        self,
        *,
        tools_were_supplied: bool,
        base_tools: list[AgentTool],
        base_definitions: list[ToolDefinition],
        base_source_infos: dict[str, SourceInfo],
    ) -> None:
        if tools_were_supplied or self._memory_tool_runtime is None or any(
            definition.name == "memory" for definition in base_definitions
        ):
            return
        memory_definition = create_memory_tool_definition(self._memory_tool_runtime)
        base_definitions.append(memory_definition)
        base_tools.append(wrap_tool_definition(memory_definition, lambda: ToolContext(cwd=self.cwd, model=self.model)))
        base_source_infos["memory"] = create_synthetic_source_info("<builtin:memory>", source="builtin")

    def _initial_active_tool_names(
        self,
        *,
        active_tool_names: list[str] | None,
        allowed_tool_names: list[str] | None,
        additional_active_tool_names: list[str] | None,
        tools: list[AgentTool] | None,
        tool_definitions: list[ToolDefinition] | None,
        base_tools: list[AgentTool],
        base_definitions: list[ToolDefinition],
    ) -> list[str]:
        if active_tool_names is not None:
            initial_names = active_tool_names
        elif tools is not None:
            initial_names = [tool.name for tool in base_tools]
        elif tool_definitions is not None:
            initial_names = [definition.name for definition in base_definitions]
        elif allowed_tool_names is not None:
            initial_names = list(allowed_tool_names)
        else:
            initial_names = self._default_active_tool_names()
        for name in additional_active_tool_names or []:
            if name not in initial_names:
                initial_names.append(name)
        return initial_names

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
        additional_active_tool_names: list[str] | None = None,
        convert_to_llm: Callable[[list[AgentMessage]], list[Message]] | None = None,
        custom_prompt: str | None = None,
        append_system_prompt: str | None = None,
        transform_context=None,
        thinking_level: ThinkingLevel = "off",
        scoped_models: list[ScopedModel] | None = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
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
        defer_agent_settled: bool = False,
        resource_loader: DefaultResourceLoader | None = None,
        agent_dir: str | None = None,
        session_index: SessionIndex | None = None,
        settings_manager: SettingsManager | None = None,
        stream_fn=None,
        model_registry: ModelRegistry | None = None,
        process_service: ProcessSessionService | None = None,
        process_owner: ProcessOwner | None = None,
        model_change_listener: Callable[[Model, Model], None] | None = None,
        model_role_bindings: Mapping[ModelRole, ScopedModel] | None = None,
        model_role_event_sink: Callable[[dict[str, object]], None] | None = None,
        tool_approval_broker: ToolApprovalBroker | None = None,
        tool_policy_event_sink: Callable[[dict[str, object]], None] | None = None,
        tool_policy_redactor: SecretRedactor | None = None,
        operation_runtime: OperationRuntime | None = None,
        owns_operation_runtime: bool = False,
        operation_role: str | None = None,
        operation_task_id: str | None = None,
    ) -> None:
        self._initialize_controllers()
        self.cwd = cwd
        self.model_registry = model_registry or ModelRegistry.create(AuthStorage.create())
        self.model_registry.ensure_model(model)
        self.auth_storage = self.model_registry.auth_storage
        self._workspace = WorkspaceCapability(Path(cwd))
        self.execution_backend = select_execution_backend(cwd)
        self._initialize_process_context(process_service, process_owner)
        self.settings_manager = settings_manager or SettingsManager.in_memory()
        self._initialize_tool_policy(
            tool_approval_broker=tool_approval_broker,
            tool_policy_event_sink=tool_policy_event_sink,
            tool_policy_redactor=tool_policy_redactor,
        )
        self._stream_fn = stream_fn or self.model_registry.stream_simple
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
        self._extension_has_ui = False
        self._extension_mode = "print"
        self._extension_command_context_actions: object | None = None
        self._extension_abort_handler: Callable[[], object] | None = None
        self._extension_shutdown_handler: Callable[[], object] | None = None
        self._extensions_bound = False
        self._extension_provider_original_models: dict[str, Model] = {}
        self._extension_provider_registrations: dict[str, object] = {}
        self._event_listeners: list[Callable[[object], None]] = []
        self._operation_tool_effects: dict[str, object] = {}
        self._operation_tool_effects_lock = threading.RLock()
        self._turn_index = 0
        self._model_change_listener = model_change_listener
        self._subagent_observer_errors: list[str] = []
        self._subagent_artifact_promotions: dict[
            str,
            tuple[tuple[str, ...], list[str], list[str], dict[str, str]],
        ] = {}
        self._public_subagent_results: dict[str, SubagentResult] = {}
        self._model_subagents_spawned_this_turn = 0
        self._model_subagent_spawn_signatures_this_turn: set[tuple[str, str, str]] = set()
        self._subagent_log_dir = Path(self._default_subagent_log_dir(session_path=session_path, session_id=session_id))
        self.subagents = SubagentSupervisor(event_sink=self._handle_subagent_event)
        self.subagents.register_backend(CallableSubagentBackend("internal", self._run_internal_subagent))
        self.subagents.register_backend(CodexExecBackend(log_dir=self._subagent_log_dir))
        self._turn_mailbox = CodingTurnMailbox()
        self._pending_next_turn_messages: list[AgentMessage] = []
        self._pending_bash_messages: list[BashExecutionMessage] = []
        self._bash_signals: set[AbortSignal] = set()
        self._bash_signals_lock = threading.RLock()
        self._command_signal: AbortSignal | None = None
        self._scoped_models = list(scoped_models or [])
        self._convert_to_llm = convert_to_llm or default_convert_to_llm
        self._caller_transform_context = transform_context
        self._initialize_resource_loader(
            cwd=cwd,
            agent_dir=agent_dir,
            custom_prompt=custom_prompt,
            append_system_prompt=append_system_prompt,
            extension_runner=extension_runner,
            resource_loader=resource_loader,
        )
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
        self._generation_param_overrides = GenerationParams()
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
        self.operation_runtime = operation_runtime
        self._owns_operation_runtime = bool(owns_operation_runtime)
        self.operation_coordinator = (
            operation_runtime.for_session(
                self.session_id or session_id,
                diagnostic_sink=self._emit,
            )
            if operation_runtime is not None
            else NullOperationCoordinator()
        )
        self._initialize_session_operations(
            role=operation_role,
            task_id=operation_task_id,
        )
        from travis.coding_agent.agent_session_services import (
            create_session_artifact_registry,
        )

        self._artifacts = create_session_artifact_registry(
            session_path=session_path,
            agent_dir=str(Path(agent_dir or get_agent_dir()).expanduser().resolve()),
            settings_manager=self.settings_manager,
        )
        self._initialize_memory_runtime(
            cwd=cwd,
            agent_dir=agent_dir,
            tools=tools,
            active_tool_names=active_tool_names,
            session_id=session_id,
        )
        self._initialize_language_services(
            cwd=cwd,
            tools=tools,
            tool_definitions=tool_definitions,
        )
        self._session_start_event = session_start_event or {"type": "session_start", "reason": "startup"}
        self._defer_session_start = bool(defer_session_start)
        self._defer_agent_settled = bool(defer_agent_settled)
        restored_context = self._session_store.build_context(default_thinking_level=thinking_level) if self._session_store else None
        if restored_context:
            thinking_level = cast(ThinkingLevel, restored_context.thinking_level)
            self._session_name = restored_context.session_name
            self._generation_param_overrides = restored_context.generation_params

        self.model_role_router = ModelRoleRouter(
            self.model_registry,
            self.settings_manager,
            ScopedModel(model, thinking_level),
            session_bindings=model_role_bindings,
            event_sink=model_role_event_sink,
        )

        base_tools, base_definitions, base_source_infos = self._create_base_tools(
            cwd=cwd,
            tools=tools,
            tool_definitions=tool_definitions,
        )
        self._append_memory_tool(
            tools_were_supplied=tools is not None,
            base_tools=base_tools,
            base_definitions=base_definitions,
            base_source_infos=base_source_infos,
        )
        self._base_tool_by_name = {tool.name: tool for tool in base_tools}
        self._base_definition_by_name = {definition.name: definition for definition in base_definitions}
        self._base_source_info_by_name = dict(base_source_infos)
        self.refresh_tools()

        initial_active_tool_names = self._initial_active_tool_names(
            active_tool_names=active_tool_names,
            allowed_tool_names=allowed_tool_names,
            additional_active_tool_names=additional_active_tool_names,
            tools=tools,
            tool_definitions=tool_definitions,
            base_tools=base_tools,
            base_definitions=base_definitions,
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
            prepare_next_turn_with_context=self._prepare_next_turn,
            transform_context=self._transform_context,
            steering_mode=steering_mode,
            follow_up_mode=follow_up_mode,
            transport=transport or "auto",
            thinking_budgets=thinking_budgets,
            max_retry_delay_ms=max_retry_delay_ms,
            on_payload=self._on_provider_payload,
            on_headers=self._on_provider_headers,
            on_response=self._on_provider_response,
            session_id=self.session_id or None,
            stream_fn=self._stream_fn,
        )
        self._compaction_coordinator = CompactionCoordinator(self.agent)
        self._compaction_adapter = SessionCompactionAdapter(
            session_store=self._session_store,
            state=cast(CompactionSessionState, self.agent.state),
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
        self._unsubscribe_agent = self.agent._subscribe_internal(  # noqa: SLF001 - session is the critical reducer.
            self._handle_agent_event
        )
        self.set_active_tools_by_name(initial_active_tool_names)
        if restored_context:
            self.agent.state.messages = restored_context.messages


install_runtime_binding_attributes(
    _SessionRuntime,
    binding_record_attribute="_session_bindings",
    binding_type=SessionRuntimeBindings,
    delegated_names=SESSION_DELEGATED_NAMES,
)
install_controller_delegates(
    _SessionRuntime,
    SESSION_CONTROLLER_DELEGATES,
)


class AgentSession(RuntimeFacade):
    """Stable public facade over the composed coding-session runtime."""

    _validated_runtime_port: SessionRuntimePort
    _validated_runtime_source: object

    def __init__(self, *args, **kwargs) -> None:
        runtime = _SessionRuntime(*args, **kwargs)
        runtime_port = validate_session_runtime_port(runtime)
        object.__setattr__(runtime, "_session_factory", type(self))
        object.__setattr__(self, "_runtime", runtime)
        object.__setattr__(self, "_validated_runtime_source", runtime)
        object.__setattr__(self, "_validated_runtime_port", runtime_port)

    def _session_runtime_port(self) -> SessionRuntimePort:
        runtime = self._runtime
        if (
            "_validated_runtime_source" not in self.__dict__
            or self._validated_runtime_source is not runtime
        ):
            runtime_port = validate_session_runtime_port(runtime)
            object.__setattr__(self, "_validated_runtime_source", runtime)
            object.__setattr__(self, "_validated_runtime_port", runtime_port)
        return self._validated_runtime_port

    @property
    def cwd(self) -> str:
        return self._session_runtime_port().cwd

    @property
    def extension_runner(self) -> ExtensionRunner:
        return self._session_runtime_port().extension_runner

    @property
    def session_path(self) -> str | None:
        return self._session_runtime_port().session_path

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str:
        return self._session_runtime_port().create_branched_session(leaf_id, path)

    def emit_deferred_session_start(self) -> None:
        self._session_runtime_port().emit_deferred_session_start()

    def get_session_entry(self, entry_id: str) -> dict[str, object] | None:
        return self._session_runtime_port().get_session_entry(entry_id)

    def get_session_leaf_id(self) -> str | None:
        return self._session_runtime_port().get_session_leaf_id()

    def dispose(self) -> None:
        try:
            self._runtime.dispose()
        finally:
            self._close_optional_owners()

    def shutdown(self, *args, **kwargs) -> None:
        try:
            self._runtime.shutdown(*args, **kwargs)
        finally:
            self._close_optional_owners()

    def _close_optional_owners(self) -> None:
        memory_store = getattr(self._runtime, "_memory_store", None)
        if memory_store is not None:
            memory_store.close()
        self._close_operation_journal()

    def _close_operation_journal(self) -> None:
        self._runtime.operation_coordinator.close()
        if self._runtime._owns_operation_runtime:
            self._runtime.operation_runtime.close()

def create_agent_session(
    *,
    cwd: str,
    model: Model,
    tools: list[AgentTool] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
    active_tool_names: list[str] | None = None,
    allowed_tool_names: list[str] | None = None,
    excluded_tool_names: list[str] | None = None,
    convert_to_llm: Callable[[list[AgentMessage]], list[Message]] | None = None,
    extension_runner: ExtensionRunner | None = None,
    session_start_event: dict[str, object] | None = None,
    defer_session_start: bool = False,
    resource_loader: DefaultResourceLoader | None = None,
    agent_dir: str | None = None,
    session_index: SessionIndex | None = None,
    settings_manager: object | None = None,
    model_role_bindings: Mapping[ModelRole, ScopedModel] | None = None,
    model_role_event_sink: Callable[[dict[str, object]], None] | None = None,
    tool_approval_broker: ToolApprovalBroker | None = None,
    tool_policy_event_sink: Callable[[dict[str, object]], None] | None = None,
    tool_policy_redactor: SecretRedactor | None = None,
    operation_runtime: object | None = None,
    owns_operation_runtime: bool = False,
    operation_role: str | None = None,
    operation_task_id: str | None = None,
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
        model_role_bindings=model_role_bindings,
        model_role_event_sink=model_role_event_sink,
        tool_approval_broker=tool_approval_broker,
        tool_policy_event_sink=tool_policy_event_sink,
        tool_policy_redactor=tool_policy_redactor,
        operation_runtime=operation_runtime,
        owns_operation_runtime=owns_operation_runtime,
        operation_role=operation_role,
        operation_task_id=operation_task_id,
    )
