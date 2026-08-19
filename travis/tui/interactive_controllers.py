"""Typed ownership bundle for interactive collaborators."""

from __future__ import annotations

from dataclasses import dataclass

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
        "init", "_populate_existing_history", "_custom_message_renderers",
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


__all__ = ["INTERACTIVE_CONTROLLER_DELEGATES", "InteractiveControllers"]
