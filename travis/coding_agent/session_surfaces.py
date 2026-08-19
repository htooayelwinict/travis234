"""Static, responsibility-specific controller attribute surfaces."""

from __future__ import annotations

from contextlib import AbstractContextManager

from travis.coding_agent.session_ports import (
    SessionBashDependencies,
    SessionEventDependencies,
    SessionExtensionDependencies,
    SessionGenerationDependencies,
    SessionModelDependencies,
    SessionOperationDependencies,
    SessionPersistenceDependencies,
    SessionPolicyDependencies,
    SessionPortBoundController,
    SessionSubagentDependencies,
    SessionSubagentTraceDependencies,
    SessionToolDependencies,
    SessionTurnDependencies,
)


class SessionEventControllerSurface(SessionPortBoundController[SessionEventDependencies]):
    """Attribute surface for the events controller."""

    __slots__ = (
        "_defer_session_start",
        "_event_listeners",
        "_extend_resources_from_extensions",
        "_extension_runner",
        "_is_retryable_error",
        "_max_retries",
        "_operation_record_persisted_message",
        "_restore_unacknowledged_turn_messages",
        "_retry_attempt",
        "_retry_enabled",
        "_session_start_event",
        "_session_store",
        "_tool_policy_event_sink",
        "_turn_index",
        "_turn_mailbox",
        "get_active_tool_names",
        "get_follow_up_messages",
        "get_steering_messages",
        "set_active_tools_by_name",
    )


class SessionModelControllerSurface(SessionPortBoundController[SessionModelDependencies]):
    """Attribute surface for the models controller."""

    __slots__ = (
        "_bash_signals",
        "_bash_signals_lock",
        "_emit",
        "_extension_runner",
        "_model_change_listener",
        "_pending_bash_messages",
        "_resource_loader",
        "_retry_attempt",
        "_retry_enabled",
        "_retry_signal",
        "_scoped_models",
        "_session_name",
        "_session_store",
        "_turn_mailbox",
        "agent",
        "model_registry",
        "model_role_router",
        "settings_manager",
    )
    _bash_signals_lock: AbstractContextManager[object]


class SessionGenerationControllerSurface(SessionPortBoundController[SessionGenerationDependencies]):
    """Attribute surface for the generation controller."""

    __slots__ = (
        "_generation_param_overrides",
        "_session_store",
    )


class SessionPersistenceControllerSurface(SessionPortBoundController[SessionPersistenceDependencies]):
    """Attribute surface for the persistence controller."""

    __slots__ = (
        "_artifacts",
        "_compaction_adapter",
        "_compaction_coordinator",
        "_compaction_manager",
        "_compaction_transactions",
        "_convert_to_llm",
        "_extension_runner",
        "_operation_continue",
        "_restore_generation_param_overrides",
        "_session_name",
        "_session_store",
        "agent",
        "messages",
        "model",
        "model_registry",
        "system_prompt",
        "thinking_level",
    )


class SessionBashControllerSurface(SessionPortBoundController[SessionBashDependencies]):
    """Attribute surface for the bash controller."""

    __slots__ = (
        "_artifacts",
        "_bash_signals",
        "_bash_signals_lock",
        "_pending_bash_messages",
        "_session_store",
        "_settings_shell_command_prefix",
        "_settings_shell_path",
        "agent",
        "cwd",
        "is_streaming",
    )
    _bash_signals_lock: AbstractContextManager[object]


class SessionPolicyControllerSurface(SessionPortBoundController[SessionPolicyDependencies]):
    """Attribute surface for the policy controller."""

    __slots__ = (
        "_coordination_runtime_guard_active",
        "_emit_tool_policy_decision",
        "_extension_runner",
        "_operation_record_tools_settled",
        "_operation_tool_effects",
        "_operation_tool_effects_lock",
        "_tool_policy_engine",
        "get_tool_definition",
        "operation_coordinator",
    )
    _operation_tool_effects_lock: AbstractContextManager[object]


class SessionOperationControllerSurface(SessionPortBoundController[SessionOperationDependencies]):
    """Attribute surface for the operations controller."""

    __slots__ = (
        "_disable_operation_journal",
        "_operation_assistant_sequence",
        "_operation_role",
        "_operation_start_message_count",
        "_operation_task_id",
        "_operation_turn_active",
        "_operation_turn_sequence",
        "_operation_usage_keys",
        "_stream_fn",
        "agent",
        "get_session_leaf_id",
        "messages",
        "operation_coordinator",
        "session_id",
    )


class SessionToolControllerSurface(SessionPortBoundController[SessionToolDependencies]):
    """Attribute surface for the tools controller."""

    __slots__ = (
        "_allowed_tool_names",
        "_append_system_prompt",
        "_artifacts",
        "_base_definition_by_name",
        "_base_source_info_by_name",
        "_base_tool_by_name",
        "_context_files",
        "_custom_prompt",
        "_excluded_tool_names",
        "_extension_runner",
        "_resource_loader",
        "_tool_by_name",
        "_tool_definition_by_name",
        "_tool_source_info_by_name",
        "_workspace",
        "agent",
        "cwd",
        "execution_backend",
        "_language_services",
        "_memory_settings",
        "_memory_tool_runtime",
        "process_owner",
        "process_service",
        "session_id",
        "settings_manager",
        "system_prompt",
    )


class SessionExtensionControllerSurface(SessionPortBoundController[SessionExtensionDependencies]):
    """Attribute surface for the extensions controller."""

    __slots__ = (
        "_append_system_prompt",
        "_artifacts",
        "_command_signal",
        "_context_files",
        "_custom_prompt",
        "_defer_session_start",
        "_event_listeners",
        "_extend_resources_from_extensions",
        "_extension_abort_handler",
        "_extension_command_context_actions",
        "_extension_error_listener",
        "_extension_error_unsubscribe",
        "_extension_has_ui",
        "_extension_mode",
        "_extension_provider_original_models",
        "_extension_provider_registrations",
        "_extension_runner",
        "_extension_shutdown_handler",
        "_extension_ui_context",
        "_extensions_bound",
        "_format_subagent_result",
        "_prepare_public_subagent_result",
        "_refresh_resource_prompt_inputs",
        "_resource_loader",
        "_session_start_event",
        "_session_store",
        "_spawn_and_wait_for_subagent",
        "_tool_definition_by_name",
        "_turn_mailbox",
        "_unsubscribe_agent",
        "agent",
        "append_custom_entry",
        "compact",
        "cwd",
        "get_active_tool_names",
        "get_all_tools",
        "get_context_usage",
        "is_streaming",
        "messages",
        "model",
        "model_registry",
        "pending_message_count",
        "prompt",
        "refresh_tools",
        "send_custom_message",
        "session_name",
        "set_active_tools_by_name",
        "set_model",
        "set_session_name",
        "set_thinking_level",
        "settings_manager",
        "subagents",
        "system_prompt",
        "thinking_level",
    )


class SessionSubagentControllerSurface(SessionPortBoundController[SessionSubagentDependencies]):
    """Attribute surface for the subagents controller."""

    __slots__ = (
        "_artifacts",
        "_format_subagent_result",
        "_messages_to_summary",
        "_model_subagent_spawn_signatures_this_turn",
        "_model_subagents_spawned_this_turn",
        "_public_subagent_results",
        "_reconcile_subagent_tool_results_from_messages",
        "_resource_loader",
        "_session_factory",
        "_stream_fn",
        "_subagent_after_tool_call_tracer",
        "_subagent_artifact_promotions",
        "_subagent_log_dir",
        "_subagent_tool_trace_listener",
        "_tool_approval_broker",
        "_tool_definition_by_name",
        "_tool_policy_engine",
        "_tool_policy_event_sink",
        "_workspace",
        "cwd",
        "get_active_tool_names",
        "model_registry",
        "operation_runtime",
        "process_owner",
        "process_service",
        "resolve_model_role",
        "session_id",
        "settings_manager",
        "subagents",
        "thinking_level",
    )


class SessionSubagentTraceControllerSurface(SessionPortBoundController[SessionSubagentTraceDependencies]):
    """Attribute surface for the subagent trace controller."""

    __slots__ = (
        "_emit",
        "_extension_runner",
        "_promote_declared_subagent_artifacts",
        "_subagent_observer_errors",
        "subagents",
    )


class SessionTurnControllerSurface(SessionPortBoundController[SessionTurnDependencies]):
    """Attribute surface for the turns controller."""

    __slots__ = (
        "_build_system_prompt",
        "_caller_transform_context",
        "_coordination_runtime_guard_active",
        "_defer_agent_settled",
        "_emit",
        "_emit_queue_update",
        "_extension_runner",
        "_flush_pending_bash_messages",
        "_max_retries",
        "_model_subagent_spawn_signatures_this_turn",
        "_model_subagents_spawned_this_turn",
        "_operation_continue",
        "_operation_finish_turn",
        "_operation_invoke_provider",
        "_operation_start_turn",
        "_parse_extension_command",
        "_partial_stream_continue_retries",
        "_pending_next_turn_messages",
        "_raise_if_extension_command",
        "_resource_loader",
        "_retry_attempt",
        "_retry_delay_ms",
        "_retry_enabled",
        "_retry_signal",
        "_retryable_error_predicate",
        "_session_store",
        "_stream_fn",
        "_try_execute_extension_command",
        "_turn_mailbox",
        "agent",
        "cwd",
        "get_active_tool_names",
        "is_streaming",
        "messages",
        "prompt_templates",
        "resolve_model_role",
        "set_active_tools_by_name",
        "system_prompt",
    )


__all__ = [
    "SessionEventControllerSurface",
    "SessionModelControllerSurface",
    "SessionGenerationControllerSurface",
    "SessionPersistenceControllerSurface",
    "SessionBashControllerSurface",
    "SessionPolicyControllerSurface",
    "SessionOperationControllerSurface",
    "SessionToolControllerSurface",
    "SessionExtensionControllerSurface",
    "SessionSubagentControllerSurface",
    "SessionSubagentTraceControllerSurface",
    "SessionTurnControllerSurface",
]
