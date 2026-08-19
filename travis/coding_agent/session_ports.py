"""Explicit dependency records used by coding-session collaborators."""

from __future__ import annotations

from dataclasses import dataclass, field

from travis.coding_agent.session_state import SessionPresentationState, SessionTurnState
from travis.controller_ports import ControllerBinding, ExplicitController


class SessionPortBoundController[DependenciesT](ExplicitController[DependenciesT]):
    """Base for a controller with one responsibility-specific dependency record."""

    __slots__ = ()


@dataclass(slots=True)
class SessionRuntimeBindings:
    """Explicit cells owned by the session composition root."""

    abort_bash: ControllerBinding[object] = field(default_factory=ControllerBinding)
    reload: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _spawn_subagent_task: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _defer_session_start: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _event_listeners: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extend_resources_from_extensions: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_runner: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _is_retryable_error: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _max_retries: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_record_persisted_message: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _restore_unacknowledged_turn_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _retry_attempt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _retry_enabled: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _session_start_event: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _session_store: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _tool_policy_event_sink: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _turn_index: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _turn_mailbox: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_active_tool_names: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_follow_up_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_steering_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_active_tools_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _bash_signals: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _bash_signals_lock: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _emit: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _model_change_listener: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _pending_bash_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _resource_loader: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _retry_signal: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _scoped_models: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _session_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    agent: ControllerBinding[object] = field(default_factory=ControllerBinding)
    model_registry: ControllerBinding[object] = field(default_factory=ControllerBinding)
    model_role_router: ControllerBinding[object] = field(default_factory=ControllerBinding)
    settings_manager: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _generation_param_overrides: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _artifacts: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _compaction_adapter: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _compaction_coordinator: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _compaction_manager: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _compaction_transactions: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _convert_to_llm: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_continue: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _restore_generation_param_overrides: ControllerBinding[object] = field(default_factory=ControllerBinding)
    messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    model: ControllerBinding[object] = field(default_factory=ControllerBinding)
    system_prompt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    thinking_level: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _settings_shell_command_prefix: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _settings_shell_path: ControllerBinding[object] = field(default_factory=ControllerBinding)
    cwd: ControllerBinding[object] = field(default_factory=ControllerBinding)
    is_streaming: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _coordination_runtime_guard_active: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _emit_tool_policy_decision: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_record_tools_settled: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_tool_effects: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_tool_effects_lock: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _tool_policy_engine: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_tool_definition: ControllerBinding[object] = field(default_factory=ControllerBinding)
    operation_coordinator: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _disable_operation_journal: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_assistant_sequence: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_role: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_start_message_count: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_task_id: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_turn_active: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_turn_sequence: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_usage_keys: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _stream_fn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_session_leaf_id: ControllerBinding[object] = field(default_factory=ControllerBinding)
    session_id: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _allowed_tool_names: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _append_system_prompt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _base_definition_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _base_source_info_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _base_tool_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _context_files: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _custom_prompt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _excluded_tool_names: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _tool_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _tool_definition_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _tool_source_info_by_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _workspace: ControllerBinding[object] = field(default_factory=ControllerBinding)
    execution_backend: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _language_services: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _memory_settings: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _memory_tool_runtime: ControllerBinding[object] = field(default_factory=ControllerBinding)
    process_owner: ControllerBinding[object] = field(default_factory=ControllerBinding)
    process_service: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _command_signal: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_abort_handler: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_command_context_actions: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_error_listener: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_error_unsubscribe: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_has_ui: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_mode: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_provider_original_models: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_provider_registrations: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_shutdown_handler: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_ui_context: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extensions_bound: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _format_subagent_result: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _prepare_public_subagent_result: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _refresh_resource_prompt_inputs: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _spawn_and_wait_for_subagent: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_agent: ControllerBinding[object] = field(default_factory=ControllerBinding)
    append_custom_entry: ControllerBinding[object] = field(default_factory=ControllerBinding)
    compact: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_all_tools: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_context_usage: ControllerBinding[object] = field(default_factory=ControllerBinding)
    pending_message_count: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    refresh_tools: ControllerBinding[object] = field(default_factory=ControllerBinding)
    send_custom_message: ControllerBinding[object] = field(default_factory=ControllerBinding)
    session_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_model: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_session_name: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_thinking_level: ControllerBinding[object] = field(default_factory=ControllerBinding)
    subagents: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _messages_to_summary: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _model_subagent_spawn_signatures_this_turn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _model_subagents_spawned_this_turn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _public_subagent_results: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _reconcile_subagent_tool_results_from_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _session_factory: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _subagent_after_tool_call_tracer: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _subagent_artifact_promotions: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _subagent_log_dir: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _subagent_tool_trace_listener: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _tool_approval_broker: ControllerBinding[object] = field(default_factory=ControllerBinding)
    operation_runtime: ControllerBinding[object] = field(default_factory=ControllerBinding)
    resolve_model_role: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _promote_declared_subagent_artifacts: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _subagent_observer_errors: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _build_system_prompt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _caller_transform_context: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _defer_agent_settled: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _emit_queue_update: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _flush_pending_bash_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_finish_turn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_invoke_provider: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _operation_start_turn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _parse_extension_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _partial_stream_continue_retries: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _pending_next_turn_messages: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _raise_if_extension_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _retry_delay_ms: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _retryable_error_predicate: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _try_execute_extension_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_templates: ControllerBinding[object] = field(default_factory=ControllerBinding)


@dataclass(frozen=True, slots=True)
class SessionEventDependencies:
    """Dependencies consumed only by the events controller."""

    _defer_session_start: ControllerBinding[object]
    _event_listeners: ControllerBinding[object]
    _extend_resources_from_extensions: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _is_retryable_error: ControllerBinding[object]
    _max_retries: ControllerBinding[object]
    _operation_record_persisted_message: ControllerBinding[object]
    _restore_unacknowledged_turn_messages: ControllerBinding[object]
    _retry_attempt: ControllerBinding[object]
    _retry_enabled: ControllerBinding[object]
    _session_start_event: ControllerBinding[object]
    _session_store: ControllerBinding[object]
    _tool_policy_event_sink: ControllerBinding[object]
    _turn_index: ControllerBinding[object]
    _turn_mailbox: ControllerBinding[object]
    get_active_tool_names: ControllerBinding[object]
    get_follow_up_messages: ControllerBinding[object]
    get_steering_messages: ControllerBinding[object]
    set_active_tools_by_name: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionModelDependencies:
    """Dependencies consumed only by the models controller."""

    _bash_signals: ControllerBinding[object]
    _bash_signals_lock: ControllerBinding[object]
    _emit: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _model_change_listener: ControllerBinding[object]
    _pending_bash_messages: ControllerBinding[object]
    _resource_loader: ControllerBinding[object]
    _retry_attempt: ControllerBinding[object]
    _retry_enabled: ControllerBinding[object]
    _retry_signal: ControllerBinding[object]
    _scoped_models: ControllerBinding[object]
    _session_name: ControllerBinding[object]
    _session_store: ControllerBinding[object]
    _turn_mailbox: ControllerBinding[object]
    agent: ControllerBinding[object]
    model_registry: ControllerBinding[object]
    model_role_router: ControllerBinding[object]
    settings_manager: ControllerBinding[object]
    presentation_state: SessionPresentationState


@dataclass(frozen=True, slots=True)
class SessionGenerationDependencies:
    """Dependencies consumed only by the generation controller."""

    _generation_param_overrides: ControllerBinding[object]
    _session_store: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionPersistenceDependencies:
    """Dependencies consumed only by the persistence controller."""

    _artifacts: ControllerBinding[object]
    _compaction_adapter: ControllerBinding[object]
    _compaction_coordinator: ControllerBinding[object]
    _compaction_manager: ControllerBinding[object]
    _compaction_transactions: ControllerBinding[object]
    _convert_to_llm: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _operation_continue: ControllerBinding[object]
    _restore_generation_param_overrides: ControllerBinding[object]
    _session_name: ControllerBinding[object]
    _session_store: ControllerBinding[object]
    agent: ControllerBinding[object]
    messages: ControllerBinding[object]
    model: ControllerBinding[object]
    model_registry: ControllerBinding[object]
    system_prompt: ControllerBinding[object]
    thinking_level: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionBashDependencies:
    """Dependencies consumed only by the bash controller."""

    _artifacts: ControllerBinding[object]
    _bash_signals: ControllerBinding[object]
    _bash_signals_lock: ControllerBinding[object]
    _pending_bash_messages: ControllerBinding[object]
    _session_store: ControllerBinding[object]
    _settings_shell_command_prefix: ControllerBinding[object]
    _settings_shell_path: ControllerBinding[object]
    agent: ControllerBinding[object]
    cwd: ControllerBinding[object]
    is_streaming: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionPolicyDependencies:
    """Dependencies consumed only by the policy controller."""

    _coordination_runtime_guard_active: ControllerBinding[object]
    _emit_tool_policy_decision: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _operation_record_tools_settled: ControllerBinding[object]
    _operation_tool_effects: ControllerBinding[object]
    _operation_tool_effects_lock: ControllerBinding[object]
    _tool_policy_engine: ControllerBinding[object]
    get_tool_definition: ControllerBinding[object]
    operation_coordinator: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionOperationDependencies:
    """Dependencies consumed only by the operations controller."""

    _disable_operation_journal: ControllerBinding[object]
    _operation_assistant_sequence: ControllerBinding[object]
    _operation_role: ControllerBinding[object]
    _operation_start_message_count: ControllerBinding[object]
    _operation_task_id: ControllerBinding[object]
    _operation_turn_active: ControllerBinding[object]
    _operation_turn_sequence: ControllerBinding[object]
    _operation_usage_keys: ControllerBinding[object]
    _stream_fn: ControllerBinding[object]
    agent: ControllerBinding[object]
    get_session_leaf_id: ControllerBinding[object]
    messages: ControllerBinding[object]
    operation_coordinator: ControllerBinding[object]
    session_id: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionToolDependencies:
    """Dependencies consumed only by the tools controller."""

    _allowed_tool_names: ControllerBinding[object]
    _append_system_prompt: ControllerBinding[object]
    _artifacts: ControllerBinding[object]
    _base_definition_by_name: ControllerBinding[object]
    _base_source_info_by_name: ControllerBinding[object]
    _base_tool_by_name: ControllerBinding[object]
    _context_files: ControllerBinding[object]
    _custom_prompt: ControllerBinding[object]
    _excluded_tool_names: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _resource_loader: ControllerBinding[object]
    _tool_by_name: ControllerBinding[object]
    _tool_definition_by_name: ControllerBinding[object]
    _tool_source_info_by_name: ControllerBinding[object]
    _workspace: ControllerBinding[object]
    agent: ControllerBinding[object]
    cwd: ControllerBinding[object]
    execution_backend: ControllerBinding[object]
    _language_services: ControllerBinding[object]
    _memory_settings: ControllerBinding[object]
    _memory_tool_runtime: ControllerBinding[object]
    process_owner: ControllerBinding[object]
    process_service: ControllerBinding[object]
    session_id: ControllerBinding[object]
    settings_manager: ControllerBinding[object]
    system_prompt: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionExtensionDependencies:
    """Dependencies consumed only by the extensions controller."""

    _append_system_prompt: ControllerBinding[object]
    _artifacts: ControllerBinding[object]
    _command_signal: ControllerBinding[object]
    _context_files: ControllerBinding[object]
    _custom_prompt: ControllerBinding[object]
    _defer_session_start: ControllerBinding[object]
    _event_listeners: ControllerBinding[object]
    _extend_resources_from_extensions: ControllerBinding[object]
    _extension_abort_handler: ControllerBinding[object]
    _extension_command_context_actions: ControllerBinding[object]
    _extension_error_listener: ControllerBinding[object]
    _extension_error_unsubscribe: ControllerBinding[object]
    _extension_has_ui: ControllerBinding[object]
    _extension_mode: ControllerBinding[object]
    _extension_provider_original_models: ControllerBinding[object]
    _extension_provider_registrations: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _extension_shutdown_handler: ControllerBinding[object]
    _extension_ui_context: ControllerBinding[object]
    _extensions_bound: ControllerBinding[object]
    _format_subagent_result: ControllerBinding[object]
    _prepare_public_subagent_result: ControllerBinding[object]
    _refresh_resource_prompt_inputs: ControllerBinding[object]
    _resource_loader: ControllerBinding[object]
    _session_start_event: ControllerBinding[object]
    _session_store: ControllerBinding[object]
    _spawn_and_wait_for_subagent: ControllerBinding[object]
    _tool_definition_by_name: ControllerBinding[object]
    _turn_mailbox: ControllerBinding[object]
    _unsubscribe_agent: ControllerBinding[object]
    agent: ControllerBinding[object]
    append_custom_entry: ControllerBinding[object]
    compact: ControllerBinding[object]
    cwd: ControllerBinding[object]
    get_active_tool_names: ControllerBinding[object]
    get_all_tools: ControllerBinding[object]
    get_context_usage: ControllerBinding[object]
    is_streaming: ControllerBinding[object]
    messages: ControllerBinding[object]
    model: ControllerBinding[object]
    model_registry: ControllerBinding[object]
    pending_message_count: ControllerBinding[object]
    prompt: ControllerBinding[object]
    refresh_tools: ControllerBinding[object]
    send_custom_message: ControllerBinding[object]
    session_name: ControllerBinding[object]
    set_active_tools_by_name: ControllerBinding[object]
    set_model: ControllerBinding[object]
    set_session_name: ControllerBinding[object]
    set_thinking_level: ControllerBinding[object]
    settings_manager: ControllerBinding[object]
    subagents: ControllerBinding[object]
    system_prompt: ControllerBinding[object]
    thinking_level: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionSubagentDependencies:
    """Dependencies consumed only by the subagents controller."""

    _artifacts: ControllerBinding[object]
    _format_subagent_result: ControllerBinding[object]
    _messages_to_summary: ControllerBinding[object]
    _model_subagent_spawn_signatures_this_turn: ControllerBinding[object]
    _model_subagents_spawned_this_turn: ControllerBinding[object]
    _public_subagent_results: ControllerBinding[object]
    _reconcile_subagent_tool_results_from_messages: ControllerBinding[object]
    _resource_loader: ControllerBinding[object]
    _session_factory: ControllerBinding[object]
    _spawn_subagent_task: ControllerBinding[object]
    _stream_fn: ControllerBinding[object]
    _subagent_after_tool_call_tracer: ControllerBinding[object]
    _subagent_artifact_promotions: ControllerBinding[object]
    _subagent_log_dir: ControllerBinding[object]
    _subagent_tool_trace_listener: ControllerBinding[object]
    _tool_approval_broker: ControllerBinding[object]
    _tool_definition_by_name: ControllerBinding[object]
    _tool_policy_engine: ControllerBinding[object]
    _tool_policy_event_sink: ControllerBinding[object]
    _workspace: ControllerBinding[object]
    cwd: ControllerBinding[object]
    get_active_tool_names: ControllerBinding[object]
    model_registry: ControllerBinding[object]
    operation_runtime: ControllerBinding[object]
    process_owner: ControllerBinding[object]
    process_service: ControllerBinding[object]
    resolve_model_role: ControllerBinding[object]
    session_id: ControllerBinding[object]
    settings_manager: ControllerBinding[object]
    subagents: ControllerBinding[object]
    thinking_level: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionSubagentTraceDependencies:
    """Dependencies consumed only by the subagent trace controller."""

    _emit: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _promote_declared_subagent_artifacts: ControllerBinding[object]
    _subagent_observer_errors: ControllerBinding[object]
    subagents: ControllerBinding[object]


@dataclass(frozen=True, slots=True)
class SessionTurnDependencies:
    """Dependencies consumed only by the turns controller."""

    _build_system_prompt: ControllerBinding[object]
    _caller_transform_context: ControllerBinding[object]
    _coordination_runtime_guard_active: ControllerBinding[object]
    _defer_agent_settled: ControllerBinding[object]
    _emit: ControllerBinding[object]
    _emit_queue_update: ControllerBinding[object]
    _extension_runner: ControllerBinding[object]
    _flush_pending_bash_messages: ControllerBinding[object]
    _max_retries: ControllerBinding[object]
    _model_subagent_spawn_signatures_this_turn: ControllerBinding[object]
    _model_subagents_spawned_this_turn: ControllerBinding[object]
    _operation_continue: ControllerBinding[object]
    _operation_finish_turn: ControllerBinding[object]
    _operation_invoke_provider: ControllerBinding[object]
    _operation_start_turn: ControllerBinding[object]
    _parse_extension_command: ControllerBinding[object]
    _partial_stream_continue_retries: ControllerBinding[object]
    _pending_next_turn_messages: ControllerBinding[object]
    _raise_if_extension_command: ControllerBinding[object]
    _resource_loader: ControllerBinding[object]
    _retry_attempt: ControllerBinding[object]
    _retry_delay_ms: ControllerBinding[object]
    _retry_enabled: ControllerBinding[object]
    _retry_signal: ControllerBinding[object]
    _retryable_error_predicate: ControllerBinding[object]
    _session_store: ControllerBinding[object]
    _stream_fn: ControllerBinding[object]
    _try_execute_extension_command: ControllerBinding[object]
    _turn_mailbox: ControllerBinding[object]
    agent: ControllerBinding[object]
    cwd: ControllerBinding[object]
    get_active_tool_names: ControllerBinding[object]
    is_streaming: ControllerBinding[object]
    messages: ControllerBinding[object]
    prompt_templates: ControllerBinding[object]
    resolve_model_role: ControllerBinding[object]
    set_active_tools_by_name: ControllerBinding[object]
    system_prompt: ControllerBinding[object]
    turn_state: SessionTurnState



__all__ = [
    "SessionEventDependencies",
    "SessionModelDependencies",
    "SessionGenerationDependencies",
    "SessionPersistenceDependencies",
    "SessionBashDependencies",
    "SessionPolicyDependencies",
    "SessionOperationDependencies",
    "SessionToolDependencies",
    "SessionExtensionDependencies",
    "SessionSubagentDependencies",
    "SessionSubagentTraceDependencies",
    "SessionTurnDependencies",
    "SessionPortBoundController",
    "SessionRuntimeBindings",
]
