"""Typed ownership bundle for interactive collaborators."""

from __future__ import annotations

from dataclasses import dataclass

from travis.controller_ports import ControllerBindingRegistry, install_explicit_port_attributes
from travis.tui.interactive_command_dispatcher import InteractiveCommandDispatcher
from travis.tui.interactive_extensions import InteractiveExtensions
from travis.tui.interactive_lsp import InteractiveLsp
from travis.tui.interactive_memory import InteractiveMemory
from travis.tui.interactive_model_auth import InteractiveModelAuth
from travis.tui.interactive_motion import InteractiveMotion
from travis.tui.interactive_operations import InteractiveOperations
from travis.tui.interactive_params import InteractiveParams
from travis.tui.interactive_process_commands import InteractiveProcessCommands
from travis.tui.interactive_session_commands import InteractiveSessionCommands
from travis.tui.interactive_shutdown import InteractiveShutdown
from travis.tui.interactive_subagents import InteractiveSubagents
from travis.tui.interactive_turn_controller import InteractiveTurnController
from travis.tui.interactive_view import InteractiveView
from travis.tui.interactive_services import InteractiveSessionRebindController


@dataclass(frozen=True, slots=True)
class InteractiveControllers:
    command_dispatch: InteractiveCommandDispatcher
    view: InteractiveView
    model_auth: InteractiveModelAuth
    params: InteractiveParams
    processes: InteractiveProcessCommands
    lsp: InteractiveLsp
    memory: InteractiveMemory
    operations: InteractiveOperations
    subagents: InteractiveSubagents
    sessions: InteractiveSessionCommands
    extensions: InteractiveExtensions
    turns: InteractiveTurnController
    shutdown: InteractiveShutdown
    motion: InteractiveMotion

    def rebind_session(self, session: object) -> None:
        rebound: list[tuple[InteractiveSessionRebindController, object]] = []
        for controller in (self.view, self.params, self.processes):
            try:
                previous = controller.rebind_session(session)
            except Exception:
                for earlier, previous in reversed(rebound):
                    earlier.rebind_session(previous)
                raise
            rebound.append((controller, previous))

    def _rebind_controller_sessions(self, session: object) -> None:
        self.rebind_session(session)


INTERACTIVE_CONTROLLER_DELEGATES = {
    "command_dispatch": ("run", "_run_motion_command"),
    "extensions": (
        "_extension_bindings", "_reset_extension_ui", "_run_reload_command",
        "_run_reload_body", "_run_package_command", "_dispatch_extension_shortcut",
        "_dispatch_extension_command", "_extension_command_executor",
        "_finish_extension_command", "_is_registered_extension_command",
        "_is_registered_prompt_template", "_extension_shortcut_context", "_extension_compact",
    ),
    "lsp": ("_run_lsp_status_command",),
    "memory": ("_run_memory_status_command",),
    "model_auth": (
        "_get_model_candidates", "_update_available_provider_count", "_run_auth_command",
        "_run_model_command", "_complete_model_command", "_show_model_list", "_cycle_model",
        "_switch_model", "_show_model_switched", "_trace_model_picker_ready",
        "_emit_pending_model_picker_trace", "_run_login", "_run_oauth_login",
        "_run_api_key_login", "_run_logout", "_select_oauth_provider",
        "_api_key_provider_options", "_oauth_provider_options", "_stored_auth_provider_options",
        "_oauth_login_callbacks", "_show_oauth_auth", "_show_oauth_device_code",
        "_show_oauth_select", "_show_status",
    ),
    "motion": (
        "set_extension_status", "_set_motion_signal", "_clear_motion_signal",
        "_refresh_extension_motion_signal", "set_working_message", "set_working_visible",
        "set_working_indicator",
    ),
    "operations": ("_run_operations_command",),
    "params": (
        "_session_generation_param_overrides", "_effective_generation_params",
        "_refresh_generation_param_state", "_stream_with_session_generation_params",
        "_run_params_command", "_show_params", "_set_session_param",
        "_reset_generation_param", "_reset_all_generation_params", "_effective_param_display",
        "_reject_active_param_write", "_show_unknown_param",
    ),
    "processes": (
        "_run_processes_command", "_process_actions", "_process_label",
        "_render_process_snapshot", "_handle_process_event", "_rebind_session_ui",
        "_run_bash_command", "_resolve_user_command", "_run_custom_user_command",
        "_append_user_command_output", "_finish_user_command",
        "_backfill_user_command_process_event", "_fail_user_command",
        "_flush_completed_user_command_records",
    ),
    "sessions": (
        "_command_executor", "_run_session_command", "_startup_text", "_session_candidates",
        "_session_label", "_run_resume_command", "_run_new_session_command",
        "_run_session_info_command", "_run_name_command", "_run_fork_command",
        "_run_clone_command", "_run_tree_command", "_session_path_argument",
        "_run_export_command", "_run_import_command", "_run_copy_command",
        "_run_share_command", "_run_theme_command", "_run_help_command",
        "_run_trust_command", "_run_unknown_command", "_run_manual_compress",
    ),
    "shutdown": (
        "_defer_sigint", "_install_sigint_handler", "_restore_sigint_handler",
        "_wait_for_active_turn", "_abort_active_turn_for_shutdown", "_request_shutdown",
        "_shutdown_tool_approvals",
    ),
    "subagents": (
        "_current_subagent_snapshot", "_bind_subagent_supervisor", "_apply_subagent_snapshot",
        "_rebind_subagent_supervisor", "_shutdown_subagent_ui", "_run_agents_command",
        "_inspect_subagent",
    ),
    "turns": (
        "_read_prompt_from_tui", "_read_prompt_from_line_input",
        "_handle_tui_terminal_input", "_handle_sigint", "_is_recently_finished_turn",
        "_is_turn_active", "_start_turn_thread", "_run_turn_thread", "_finish_turn_thread",
        "_trace_turn_ready", "_handle_active_turn_prompt", "_handle_editor_escape",
        "_show_post_response_compaction_status",
    ),
    "view": (
        "init", "_ensure_builtin_themes", "_reload_resource_themes",
        "_populate_existing_history", "_custom_message_renderers",
        "create_base_autocomplete_provider", "setup_autocomplete_provider",
        "add_autocomplete_provider", "get_autocomplete_suggestions", "_handle_session_event",
        "_render_subagent_lifecycle_event", "_render_subagent_tool_event",
        "_handle_footer_branch_change", "_render_auto_compaction_notice", "_refresh_footer",
        "set_hidden_thinking_label", "set_terminal_title", "set_editor_text",
        "get_editor_text", "paste_to_editor", "set_extension_footer",
        "set_extension_header", "set_extension_widget", "_render_widgets",
        "_render_widget_container", "prompt_extension_input", "prompt_extension_editor",
        "prompt_extension_select", "_prompt_tui_theme_select", "_prompt_tui_value",
        "prompt_extension_confirm", "prompt_extension_custom", "add_terminal_input_listener",
        "_dispatch_terminal_input",
    ),
}


INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES = {
    "command_dispatch": (
        "_abort_active_turn_for_shutdown", "_dispatch_extension_command",
        "_dispatch_extension_shortcut", "_dispatch_terminal_input", "_extension_commands",
        "_extension_host", "_handle_active_turn_prompt", "_handle_editor_escape",
        "_install_sigint_handler", "_is_registered_extension_command",
        "_is_registered_prompt_template", "_is_turn_active", "_line_input_mode",
        "_open_resume_picker", "_read_prompt_from_line_input", "_read_prompt_from_tui",
        "_refresh_footer", "_restore_sigint_handler", "_run_agents_command",
        "_run_auth_command", "_run_bash_command", "_run_clone_command", "_run_copy_command",
        "_run_export_command", "_run_fork_command", "_run_help_command", "_run_import_command",
        "_run_loop_active", "_run_lsp_status_command", "_run_manual_compress",
        "_run_memory_status_command", "_run_model_command", "_run_name_command",
        "_run_new_session_command", "_run_operations_command", "_run_package_command",
        "_run_params_command", "_run_processes_command", "_run_reload_command",
        "_run_resume_command", "_run_session_info_command", "_run_share_command",
        "_run_theme_command", "_run_tree_command", "_run_trust_command",
        "_run_unknown_command", "_session_commands", "_set_motion_signal",
        "_shutdown_requested", "_shutdown_subagent_ui", "_start_turn_thread",
        "_unsubscribe_app_session_rebound", "_unsubscribe_footer_branch_change",
        "_unsubscribe_process_events", "_unsubscribe_session_events",
        "_unsubscribe_tui_scroll_change", "_unsubscribe_tui_terminal_input", "_user_commands",
        "_wait_for_active_turn", "active_editor", "app", "autocomplete_provider",
        "editor_container", "editor_text", "footer_data_provider", "history", "init",
        "motion_controller", "prompt_history", "prompt_label", "status", "theme_context", "tui",
    ),
    "view": (
        "MAX_WIDGET_LINES", "_clear_motion_signal", "_emit_pending_model_picker_trace",
        "_builtin_theme_records", "_ensure_builtin_themes", "_extension_bindings", "_extension_host",
        "_handle_process_event", "_handle_tui_terminal_input", "_history_populated",
        "_initialized", "_is_turn_active", "_last_compaction_failure_notice_key",
        "_line_input_mode", "_read_prompt_from_line_input", "_reload_resource_themes",
        "_set_motion_signal", "_shutdown_requested", "_terminal_input_listeners",
        "_unsubscribe_footer_branch_change", "_unsubscribe_process_events",
        "_unsubscribe_session_events", "_unsubscribe_tui_terminal_input",
        "_update_available_provider_count", "active_editor", "app", "autocomplete_provider",
        "autocomplete_provider_wrappers", "built_in_header", "custom_footer", "custom_header",
        "default_hidden_thinking_label", "editor_container", "editor_text",
        "extension_statuses", "extension_widgets_above", "extension_widgets_below", "footer",
        "footer_container", "footer_data_provider", "header_container", "hidden_thinking_label",
        "hide_thinking_block", "history", "input_fn", "status", "theme_context",
        "theme_controller", "theme_registry", "tui", "widget_container_above",
        "widget_container_below",
    ),
    "model_auth": (
        "_pending_model_picker_trace", "_refresh_footer", "_refresh_generation_param_state",
        "_run_session_command", "app", "footer_data_provider", "history",
        "prompt_extension_input", "prompt_extension_select", "tui",
    ),
    "params": (
        "_is_turn_active", "_refresh_footer", "_show_status", "app",
        "generation_param_warnings", "generation_params", "startup_generation_params", "tui",
    ),
    "processes": (
        "_clear_motion_signal", "_command_executor", "_completed_user_commands",
        "_handle_session_event", "_history_populated", "_initialized", "_is_turn_active",
        "_notified_processes", "_populate_existing_history", "_process_cursors",
        "_rebind_controller_sessions", "_rebind_subagent_supervisor", "_refresh_footer",
        "_refresh_generation_param_state",
        "_set_motion_signal", "_shutdown_requested", "_startup_text",
        "_unsubscribe_session_events", "_user_command_components", "_user_command_order",
        "_user_commands", "app", "built_in_header", "footer", "hidden_thinking_label",
        "hide_thinking_block", "history", "prompt_extension_select",
        "setup_autocomplete_provider", "status", "tool_approval_broker", "tui",
    ),
    "lsp": ("_refresh_footer", "app", "history", "status", "tui"),
    "memory": ("_refresh_footer", "app", "history", "status", "tui"),
    "operations": ("_refresh_footer", "app", "history", "status", "tui"),
    "subagents": (
        "_refresh_footer", "_subagent_snapshot", "_unsubscribe_subagents", "app", "history",
        "prompt_extension_confirm", "status", "tui",
    ),
    "sessions": (
        "_clear_motion_signal", "_is_turn_active", "_rebind_session_ui", "_refresh_footer",
        "_session_commands", "_set_motion_signal", "app", "editor_text", "history",
        "prompt_extension_select", "status", "theme_registry", "tui",
    ),
    "extensions": (
        "_clear_motion_signal", "_extension_commands", "_refresh_extension_motion_signal",
        "_refresh_footer", "_reload_resource_themes", "_render_widgets", "_request_shutdown",
        "_run_session_command", "_set_motion_signal", "_terminal_input_listeners", "app",
        "autocomplete_provider_wrappers", "extension_status_states", "extension_statuses",
        "extension_widgets_above", "extension_widgets_below", "extension_working_active",
        "history", "prompt_extension_confirm", "prompt_extension_custom",
        "prompt_extension_editor", "prompt_extension_input", "prompt_extension_select",
        "add_autocomplete_provider", "add_terminal_input_listener", "get_editor_text",
        "paste_to_editor", "set_editor_text", "set_extension_footer", "set_extension_header",
        "set_extension_status", "set_extension_widget", "set_hidden_thinking_label",
        "set_terminal_title", "set_working_indicator", "set_working_message",
        "set_working_visible", "setup_autocomplete_provider", "status", "theme_registry", "tui",
    ),
    "turns": (
        "_agent_abort_requested", "_clear_motion_signal", "_command_executor",
        "_dispatch_extension_command", "_dispatch_terminal_input", "_last_idle_ctrl_c_at",
        "_last_turn_finished_at", "_queued_after_turn", "_refresh_footer",
        "_render_auto_compaction_notice", "_set_motion_signal", "_shutdown_requested",
        "_stream_with_session_generation_params", "_turn_future", "_turn_lock", "_turn_thread",
        "_user_commands", "_is_turn_active", "active_editor", "app", "editor_text", "footer", "history",
        "input_fn", "status", "tui",
    ),
    "shutdown": (
        "_agent_abort_requested", "_handle_sigint", "_is_turn_active", "_run_loop_active",
        "_session_commands", "_shutdown_requested", "_turn_future", "_turn_lock", "_turn_thread",
        "app", "tui",
    ),
    "motion": (
        "_refresh_footer", "default_working_message", "extension_status_states",
        "extension_statuses", "extension_working_active", "motion_controller", "status", "tui",
    ),
}

_INTERACTIVE_CONTROLLER_TYPES = {
    "command_dispatch": InteractiveCommandDispatcher,
    "view": InteractiveView,
    "model_auth": InteractiveModelAuth,
    "params": InteractiveParams,
    "processes": InteractiveProcessCommands,
    "lsp": InteractiveLsp,
    "memory": InteractiveMemory,
    "operations": InteractiveOperations,
    "subagents": InteractiveSubagents,
    "sessions": InteractiveSessionCommands,
    "extensions": InteractiveExtensions,
    "turns": InteractiveTurnController,
    "shutdown": InteractiveShutdown,
    "motion": InteractiveMotion,
}
for _controller_name, _attribute_names in INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES.items():
    install_explicit_port_attributes(_INTERACTIVE_CONTROLLER_TYPES[_controller_name], _attribute_names)

INTERACTIVE_CONTROLLER_STATE_NAMES = frozenset(
    name for names in INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES.values() for name in names
)
INTERACTIVE_DELEGATED_NAMES = frozenset(
    name for names in INTERACTIVE_CONTROLLER_DELEGATES.values() for name in names
)


def bind_interactive_controller_owners(
    registry: ControllerBindingRegistry,
    controllers: InteractiveControllers,
) -> None:
    for controller_name, names in INTERACTIVE_CONTROLLER_DELEGATES.items():
        controller = getattr(controllers, controller_name)
        for name in names:
            if name in INTERACTIVE_CONTROLLER_STATE_NAMES:
                registry.bind_owner(name, controller)
    registry.bind_owner("_rebind_controller_sessions", controllers)


__all__ = [
    "INTERACTIVE_CONTROLLER_DELEGATES",
    "INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES",
    "INTERACTIVE_CONTROLLER_STATE_NAMES",
    "INTERACTIVE_DELEGATED_NAMES",
    "InteractiveControllers",
    "bind_interactive_controller_owners",
]
