"""Typed ownership bundle for coding-session collaborators."""

from __future__ import annotations

from dataclasses import dataclass

from travis.coding_agent.session_bash import SessionBashController
from travis.coding_agent.session_events import SessionEventController
from travis.coding_agent.session_extensions import SessionExtensionController
from travis.coding_agent.session_generation_params import SessionGenerationParams
from travis.coding_agent.session_models import SessionModelController
from travis.coding_agent.session_operations import SessionOperationController
from travis.coding_agent.session_persistence import SessionPersistence
from travis.coding_agent.session_policy_controller import SessionPolicyController
from travis.coding_agent.session_subagents import SessionSubagentController
from travis.coding_agent.session_tooling import SessionToolController
from travis.coding_agent.session_turns import SessionTurnController
from travis.coding_agent.subagent_trace import SessionSubagentTraceController
from travis.controller_ports import (
    ControllerBindingRegistry,
    install_explicit_port_attributes,
)


@dataclass(frozen=True, slots=True)
class SessionControllers:
    events: SessionEventController
    models: SessionModelController
    generation: SessionGenerationParams
    persistence: SessionPersistence
    bash: SessionBashController
    policy: SessionPolicyController
    operations: SessionOperationController
    tools: SessionToolController
    extensions: SessionExtensionController
    subagents: SessionSubagentController
    subagent_trace: SessionSubagentTraceController
    turns: SessionTurnController


SESSION_CONTROLLER_DELEGATES = {
    "bash": (
        "execute_bash", "abort_bash", "record_bash_result", "_append_bash_message",
        "_flush_pending_bash_messages",
    ),
    "events": (
        "_emit_session_start_event", "emit_deferred_session_start", "subscribe",
        "_handle_agent_event", "_emit_extension_event", "_will_retry_after_agent_end",
        "_emit_queue_update", "_emit_tool_policy_decision", "_emit",
    ),
    "generation": (
        "generation_param_overrides", "set_generation_param_override",
        "reset_generation_param_override", "reset_generation_param_overrides",
        "_publish_generation_param_overrides", "_restore_generation_param_overrides",
    ),
    "persistence": (
        "compact", "set_compaction_manager", "compaction_transactions", "compaction_adapter",
        "is_compacting", "session_entries", "get_session_entry", "get_session_leaf_id",
        "session_tree", "create_branched_session", "export_to_jsonl", "export_to_html",
        "append_custom_entry", "session_path", "session_file", "session_id", "branch",
        "navigate_tree", "get_user_messages_for_forking", "get_last_assistant_text",
        "get_session_stats", "get_context_usage", "_current_context_usage_estimate",
    ),
    "models": (
        "pending_message_count", "has_pending_bash_messages", "is_bash_running",
        "get_steering_messages", "get_follow_up_messages", "is_streaming", "state", "model",
        "thinking_level", "scoped_models", "retry_attempt", "is_retrying",
        "auto_retry_enabled", "set_auto_retry_enabled", "abort_retry", "session_name",
        "extension_runner", "resource_loader", "prompt_templates", "has_extension_handlers",
        "messages", "steering_mode", "follow_up_mode", "set_steering_mode",
        "set_follow_up_mode", "set_session_name", "rename_session", "set_thinking_level",
        "resolve_model_role", "cycle_thinking_level", "get_available_thinking_levels",
        "supports_thinking", "_get_thinking_level_for_model_switch", "_clamp_thinking_level",
        "set_model", "with_model_overrides", "set_scoped_models", "cycle_model",
        "_cycle_scoped_model", "_cycle_available_model",
    ),
    "operations": (
        "_initialize_session_operations", "_operation_start_turn", "_operation_finish_turn",
        "_operation_continue", "_operation_stream_fn", "_operation_invoke_provider",
        "_operation_observe_provider_stream", "_operation_settle_provider",
        "_operation_record_persisted_message", "_operation_record_tools_settled",
        "_operation_advance",
    ),
    "policy": (
        "_before_tool_call", "_after_tool_call", "_journal_tool_intent",
        "_settle_tool_effect", "_disable_operation_journal",
    ),
    "extensions": (
        "bind_extensions", "_apply_extension_bindings", "reload", "dispose",
        "_try_execute_extension_command", "_parse_extension_command",
        "_raise_if_extension_command", "_extension_command_context",
        "create_replaced_session_context", "_extension_command_infos",
        "_register_builtin_subagent_commands", "_register_skill_commands", "_agents_command",
        "_delegate_command", "_cancel_agent_command", "_bind_extension_core",
        "_extension_spawn_subagent", "_current_abort_signal", "_with_command_abort_signal",
        "_extension_get_subagent_result", "_extension_cancel_subagent", "_extension_abort",
        "_extension_shutdown", "shutdown", "set_label", "_extension_wait_for_idle",
        "_extension_set_model", "_extension_exec", "_extension_compact",
        "_extension_send_user_message", "_system_prompt_options_snapshot",
        "_register_extension_provider", "_unregister_extension_provider",
    ),
    "subagents": (
        "_subagent_allowed_tools_for_role", "_normalize_subagent_role", "_build_subagent_task",
        "_spawn_subagent_task", "_spawn_and_wait_for_subagent",
        "_create_subagent_tool_definitions", "_execute_spawn_subagent_tool",
        "_execute_wait_subagent_tool", "_execute_list_subagents_tool",
        "_execute_get_subagent_result_tool", "_execute_expand_subagent_result_tool",
        "_execute_cancel_subagent_tool", "_prepare_public_subagent_result",
        "_promote_declared_subagent_artifacts", "_promote_subagent_trace",
        "_subagent_tool_result", "_subagent_process_owner", "_kill_active_subagent_processes",
        "_run_internal_subagent", "_safe_write_internal_subagent_result_pack",
    ),
    "subagent_trace": (
        "_subagent_tool_trace_listener", "_reconcile_subagent_tool_results_from_messages",
        "_subagent_after_tool_call_tracer", "_record_subagent_tool_end", "_messages_to_summary",
        "_format_subagent_result", "_handle_subagent_event", "subagent_observer_errors",
    ),
    "tools": (
        "_default_subagent_log_dir", "_is_allowed_tool", "_settings_shell_command_prefix",
        "_default_active_tool_names", "_settings_shell_path", "_skill_read_access",
        "_builtin_tool_options", "_refresh_tool_registry", "refresh_tools",
        "_build_system_prompt", "_refresh_resource_prompt_inputs",
        "_extend_resources_from_extensions", "reload_resources", "get_active_tool_names",
        "get_all_tools", "get_known_tool_names", "get_tool_definition",
        "set_active_tools_by_name",
    ),
    "turns": (
        "prompt", "_reset_model_subagent_turn_budget", "continue_", "emit_agent_settled",
        "steer", "follow_up", "_queue_turn_input", "_expand_user_references",
        "_flush_turn_mailbox", "_flush_turn_mailbox_kind",
        "_restore_unacknowledged_turn_messages", "send_custom_message",
        "_apply_before_agent_start", "_transform_context", "_on_provider_payload",
        "_on_provider_headers", "_on_provider_response", "_prepare_next_turn",
        "_queue_partial_stream_continuation_if_needed", "clear_queue", "_run_agent_prompt",
        "_prepare_retry", "_is_retryable_error", "_wait_for_retry_abort",
        "_latest_user_message_text",
    ),
}


SESSION_CONTROLLER_PORT_ATTRIBUTES = {
    "events": (
        "_defer_session_start", "_event_listeners", "_extend_resources_from_extensions",
        "_extension_runner", "_is_retryable_error", "_max_retries",
        "_operation_record_persisted_message", "_restore_unacknowledged_turn_messages",
        "_retry_attempt", "_retry_enabled", "_session_start_event", "_session_store",
        "_tool_policy_event_sink", "_turn_index", "_turn_mailbox", "get_active_tool_names",
        "get_follow_up_messages", "get_steering_messages", "set_active_tools_by_name",
    ),
    "models": (
        "_bash_signals", "_bash_signals_lock", "_emit", "_extension_runner",
        "_model_change_listener", "_pending_bash_messages", "_resource_loader",
        "_retry_attempt", "_retry_enabled",
        "_retry_signal", "_scoped_models", "_session_name", "_session_store",
        "_turn_mailbox", "agent", "model_registry", "model_role_router", "settings_manager",
    ),
    "generation": ("_generation_param_overrides", "_session_store"),
    "persistence": (
        "_artifacts", "_compaction_adapter", "_compaction_coordinator", "_compaction_manager",
        "_compaction_transactions", "_convert_to_llm", "_extension_runner",
        "_operation_continue", "_restore_generation_param_overrides", "_session_name",
        "_session_store", "agent", "messages", "model", "model_registry", "system_prompt",
        "thinking_level",
    ),
    "bash": (
        "_artifacts", "_bash_signals", "_bash_signals_lock", "_pending_bash_messages",
        "_session_store", "_settings_shell_command_prefix", "_settings_shell_path", "agent",
        "cwd", "is_streaming",
    ),
    "policy": (
        "_coordination_runtime_guard_active", "_emit_tool_policy_decision",
        "_extension_runner", "_operation_record_tools_settled",
        "_operation_tool_effects", "_operation_tool_effects_lock", "_tool_policy_engine",
        "get_tool_definition", "operation_coordinator",
    ),
    "operations": (
        "_disable_operation_journal", "_operation_assistant_sequence", "_operation_role",
        "_operation_start_message_count", "_operation_task_id", "_operation_turn_active",
        "_operation_turn_sequence", "_operation_usage_keys", "_stream_fn", "agent",
        "get_session_leaf_id", "messages", "operation_coordinator", "session_id",
    ),
    "tools": (
        "_allowed_tool_names", "_append_system_prompt", "_artifacts", "_base_definition_by_name",
        "_base_source_info_by_name", "_base_tool_by_name", "_context_files", "_custom_prompt",
        "_excluded_tool_names", "_extension_runner", "_resource_loader", "_tool_by_name",
        "_tool_definition_by_name", "_tool_source_info_by_name", "_workspace", "agent", "cwd",
        "execution_backend", "_language_services", "_memory_settings", "_memory_tool_runtime",
        "process_owner", "process_service", "session_id", "settings_manager", "system_prompt",
    ),
    "extensions": (
        "_append_system_prompt", "_artifacts", "_command_signal", "_context_files",
        "_custom_prompt", "_defer_session_start", "_event_listeners",
        "_extend_resources_from_extensions", "_extension_abort_handler",
        "_extension_command_context_actions", "_extension_error_listener",
        "_extension_error_unsubscribe", "_extension_has_ui", "_extension_mode",
        "_extension_provider_original_models", "_extension_provider_registrations",
        "_extension_runner", "_extension_shutdown_handler", "_extension_ui_context",
        "_extensions_bound", "_format_subagent_result", "_prepare_public_subagent_result",
        "_refresh_resource_prompt_inputs", "_resource_loader", "_session_start_event",
        "_session_store", "_spawn_and_wait_for_subagent", "_tool_definition_by_name",
        "_turn_mailbox", "_unsubscribe_agent", "agent", "append_custom_entry", "compact", "cwd",
        "get_active_tool_names", "get_all_tools", "get_context_usage", "is_streaming", "messages",
        "model", "model_registry", "pending_message_count", "prompt", "refresh_tools",
        "send_custom_message", "session_name", "set_active_tools_by_name", "set_model",
        "set_session_name", "set_thinking_level", "settings_manager", "subagents",
        "system_prompt", "thinking_level",
    ),
    "subagents": (
        "_artifacts", "_format_subagent_result", "_messages_to_summary",
        "_model_subagent_spawn_signatures_this_turn", "_model_subagents_spawned_this_turn",
        "_public_subagent_results", "_reconcile_subagent_tool_results_from_messages",
        "_resource_loader", "_session_factory", "_stream_fn", "_subagent_after_tool_call_tracer",
        "_subagent_artifact_promotions", "_subagent_log_dir", "_subagent_tool_trace_listener",
        "_tool_approval_broker", "_tool_definition_by_name", "_tool_policy_engine",
        "_tool_policy_event_sink", "_workspace", "cwd", "get_active_tool_names",
        "model_registry", "operation_runtime", "process_owner", "process_service",
        "resolve_model_role", "session_id", "settings_manager", "subagents", "thinking_level",
    ),
    "subagent_trace": (
        "_emit", "_extension_runner", "_promote_declared_subagent_artifacts",
        "_subagent_observer_errors", "subagents",
    ),
    "turns": (
        "_build_system_prompt", "_caller_transform_context", "_coordination_runtime_guard_active",
        "_defer_agent_settled", "_emit", "_emit_queue_update", "_extension_runner",
        "_flush_pending_bash_messages", "_max_retries",
        "_model_subagent_spawn_signatures_this_turn", "_model_subagents_spawned_this_turn",
        "_operation_continue", "_operation_finish_turn", "_operation_invoke_provider",
        "_operation_start_turn", "_parse_extension_command", "_partial_stream_continue_retries",
        "_pending_next_turn_messages", "_raise_if_extension_command", "_resource_loader",
        "_retry_attempt", "_retry_delay_ms", "_retry_enabled", "_retry_signal",
        "_retryable_error_predicate", "_session_store", "_stream_fn",
        "_try_execute_extension_command", "_turn_mailbox", "agent", "cwd",
        "get_active_tool_names", "is_streaming", "messages", "prompt_templates",
        "resolve_model_role", "set_active_tools_by_name", "system_prompt",
    ),
}

_SESSION_CONTROLLER_TYPES = {
    "events": SessionEventController,
    "models": SessionModelController,
    "generation": SessionGenerationParams,
    "persistence": SessionPersistence,
    "bash": SessionBashController,
    "policy": SessionPolicyController,
    "operations": SessionOperationController,
    "tools": SessionToolController,
    "extensions": SessionExtensionController,
    "subagents": SessionSubagentController,
    "subagent_trace": SessionSubagentTraceController,
    "turns": SessionTurnController,
}
for _controller_name, _attribute_names in SESSION_CONTROLLER_PORT_ATTRIBUTES.items():
    install_explicit_port_attributes(_SESSION_CONTROLLER_TYPES[_controller_name], _attribute_names)

SESSION_CONTROLLER_STATE_NAMES = frozenset(
    name for names in SESSION_CONTROLLER_PORT_ATTRIBUTES.values() for name in names
)
SESSION_DELEGATED_NAMES = frozenset(
    name for names in SESSION_CONTROLLER_DELEGATES.values() for name in names
)


def bind_session_controller_owners(
    registry: ControllerBindingRegistry,
    controllers: SessionControllers,
) -> None:
    for controller_name, names in SESSION_CONTROLLER_DELEGATES.items():
        controller = getattr(controllers, controller_name)
        for name in names:
            if name in SESSION_CONTROLLER_STATE_NAMES:
                registry.bind_owner(name, controller)


__all__ = [
    "SESSION_CONTROLLER_DELEGATES",
    "SESSION_CONTROLLER_PORT_ATTRIBUTES",
    "SESSION_CONTROLLER_STATE_NAMES",
    "SESSION_DELEGATED_NAMES",
    "SessionControllers",
    "bind_session_controller_owners",
]
