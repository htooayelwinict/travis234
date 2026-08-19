"""Static, responsibility-specific native-TUI controller surfaces."""

from __future__ import annotations

from contextlib import AbstractContextManager

from travis.tui.interactive_dependencies import (
    InteractiveCommandDispatchDependencies,
    InteractiveExtensionDependencies,
    InteractiveLspDependencies,
    InteractiveMemoryDependencies,
    InteractiveModelAuthDependencies,
    InteractiveMotionDependencies,
    InteractiveOperationsDependencies,
    InteractiveParamsDependencies,
    InteractiveProcessDependencies,
    InteractiveSessionDependencies,
    InteractiveShutdownDependencies,
    InteractiveSubagentDependencies,
    InteractiveTurnDependencies,
    InteractiveViewDependencies,
)
from travis.tui.interactive_services import PortBoundController


class InteractiveCommandDispatchSurface(PortBoundController[InteractiveCommandDispatchDependencies]):
    """Attribute surface for the command dispatch controller."""

    __slots__ = (
        "_abort_active_turn_for_shutdown",
        "_dispatch_extension_command",
        "_dispatch_extension_shortcut",
        "_dispatch_terminal_input",
        "_extension_commands",
        "_extension_host",
        "_handle_active_turn_prompt",
        "_handle_editor_escape",
        "_install_sigint_handler",
        "_is_registered_extension_command",
        "_is_registered_prompt_template",
        "_is_turn_active",
        "_line_input_mode",
        "_open_resume_picker",
        "_read_prompt_from_line_input",
        "_read_prompt_from_tui",
        "_refresh_footer",
        "_restore_sigint_handler",
        "_run_agents_command",
        "_run_auth_command",
        "_run_bash_command",
        "_run_clone_command",
        "_run_copy_command",
        "_run_export_command",
        "_run_fork_command",
        "_run_help_command",
        "_run_import_command",
        "_run_loop_active",
        "_run_lsp_status_command",
        "_run_manual_compress",
        "_run_memory_status_command",
        "_run_model_command",
        "_run_name_command",
        "_run_new_session_command",
        "_run_operations_command",
        "_run_package_command",
        "_run_params_command",
        "_run_processes_command",
        "_run_reload_command",
        "_run_resume_command",
        "_run_session_info_command",
        "_run_share_command",
        "_run_theme_command",
        "_run_tree_command",
        "_run_trust_command",
        "_run_unknown_command",
        "_session_commands",
        "_set_motion_signal",
        "_shutdown_requested",
        "_shutdown_subagent_ui",
        "_start_turn_thread",
        "_unsubscribe_app_session_rebound",
        "_unsubscribe_footer_branch_change",
        "_unsubscribe_process_events",
        "_unsubscribe_session_events",
        "_unsubscribe_tui_scroll_change",
        "_unsubscribe_tui_terminal_input",
        "_user_commands",
        "_wait_for_active_turn",
        "active_editor",
        "app",
        "autocomplete_provider",
        "editor_container",
        "editor_text",
        "footer_data_provider",
        "history",
        "init",
        "motion_controller",
        "prompt_history",
        "prompt_label",
        "status",
        "theme_context",
        "tui",
    )


class InteractiveViewSurface(PortBoundController[InteractiveViewDependencies]):
    """Attribute surface for the view controller."""

    __slots__ = (
        "MAX_WIDGET_LINES",
        "_clear_motion_signal",
        "_emit_pending_model_picker_trace",
        "_builtin_theme_records",
        "_ensure_builtin_themes",
        "_extension_bindings",
        "_extension_host",
        "_handle_process_event",
        "_handle_tui_terminal_input",
        "_history_populated",
        "_initialized",
        "_is_turn_active",
        "_last_compaction_failure_notice_key",
        "_line_input_mode",
        "_read_prompt_from_line_input",
        "_reload_resource_themes",
        "_set_motion_signal",
        "_shutdown_requested",
        "_terminal_input_listeners",
        "_unsubscribe_footer_branch_change",
        "_unsubscribe_process_events",
        "_unsubscribe_session_events",
        "_unsubscribe_tui_terminal_input",
        "_update_available_provider_count",
        "active_editor",
        "app",
        "autocomplete_provider",
        "autocomplete_provider_wrappers",
        "built_in_header",
        "custom_footer",
        "custom_header",
        "default_hidden_thinking_label",
        "editor_container",
        "editor_text",
        "extension_statuses",
        "extension_widgets_above",
        "extension_widgets_below",
        "footer",
        "footer_container",
        "footer_data_provider",
        "header_container",
        "hidden_thinking_label",
        "hide_thinking_block",
        "history",
        "input_fn",
        "status",
        "theme_context",
        "theme_controller",
        "theme_registry",
        "tui",
        "widget_container_above",
        "widget_container_below",
        "session",
    )


class InteractiveModelAuthSurface(PortBoundController[InteractiveModelAuthDependencies]):
    """Attribute surface for the model auth controller."""

    __slots__ = (
        "_pending_model_picker_trace",
        "_refresh_footer",
        "_refresh_generation_param_state",
        "_run_session_command",
        "app",
        "footer_data_provider",
        "history",
        "prompt_extension_input",
        "prompt_extension_select",
        "tui",
    )


class InteractiveParamsSurface(PortBoundController[InteractiveParamsDependencies]):
    """Attribute surface for the params controller."""

    __slots__ = (
        "_is_turn_active",
        "_refresh_footer",
        "_show_status",
        "app",
        "generation_param_warnings",
        "generation_params",
        "startup_generation_params",
        "tui",
        "session",
    )


class InteractiveProcessSurface(PortBoundController[InteractiveProcessDependencies]):
    """Attribute surface for the processes controller."""

    __slots__ = (
        "_clear_motion_signal",
        "_command_executor",
        "_completed_user_commands",
        "_handle_session_event",
        "_history_populated",
        "_initialized",
        "_is_turn_active",
        "_notified_processes",
        "_populate_existing_history",
        "_process_cursors",
        "_rebind_controller_sessions",
        "_rebind_subagent_supervisor",
        "_refresh_footer",
        "_refresh_generation_param_state",
        "_set_motion_signal",
        "_shutdown_requested",
        "_startup_text",
        "_unsubscribe_session_events",
        "_user_command_components",
        "_user_command_order",
        "_user_commands",
        "app",
        "built_in_header",
        "footer",
        "hidden_thinking_label",
        "hide_thinking_block",
        "history",
        "prompt_extension_select",
        "setup_autocomplete_provider",
        "status",
        "tool_approval_broker",
        "tui",
        "session",
    )


class InteractiveLspSurface(PortBoundController[InteractiveLspDependencies]):
    """Attribute surface for the lsp controller."""

    __slots__ = (
        "_refresh_footer",
        "app",
        "history",
        "status",
        "tui",
    )


class InteractiveMemorySurface(PortBoundController[InteractiveMemoryDependencies]):
    """Attribute surface for the memory controller."""

    __slots__ = (
        "_refresh_footer",
        "app",
        "history",
        "status",
        "tui",
    )


class InteractiveOperationsSurface(PortBoundController[InteractiveOperationsDependencies]):
    """Attribute surface for the operations controller."""

    __slots__ = (
        "_refresh_footer",
        "app",
        "history",
        "status",
        "tui",
    )


class InteractiveSubagentSurface(PortBoundController[InteractiveSubagentDependencies]):
    """Attribute surface for the subagents controller."""

    __slots__ = (
        "_refresh_footer",
        "_subagent_snapshot",
        "_unsubscribe_subagents",
        "app",
        "history",
        "prompt_extension_confirm",
        "status",
        "tui",
    )


class InteractiveSessionSurface(PortBoundController[InteractiveSessionDependencies]):
    """Attribute surface for the sessions controller."""

    __slots__ = (
        "_clear_motion_signal",
        "_is_turn_active",
        "_rebind_session_ui",
        "_refresh_footer",
        "_session_commands",
        "_set_motion_signal",
        "app",
        "editor_text",
        "history",
        "prompt_extension_select",
        "status",
        "theme_registry",
        "tui",
    )


class InteractiveExtensionSurface(PortBoundController[InteractiveExtensionDependencies]):
    """Attribute surface for the extensions controller."""

    __slots__ = (
        "_clear_motion_signal",
        "_extension_commands",
        "_refresh_extension_motion_signal",
        "_refresh_footer",
        "_reload_resource_themes",
        "_render_widgets",
        "_request_shutdown",
        "_run_session_command",
        "_set_motion_signal",
        "_terminal_input_listeners",
        "app",
        "autocomplete_provider_wrappers",
        "extension_status_states",
        "extension_statuses",
        "extension_widgets_above",
        "extension_widgets_below",
        "extension_working_active",
        "history",
        "prompt_extension_confirm",
        "prompt_extension_custom",
        "prompt_extension_editor",
        "prompt_extension_input",
        "prompt_extension_select",
        "add_autocomplete_provider",
        "add_terminal_input_listener",
        "get_editor_text",
        "paste_to_editor",
        "set_editor_text",
        "set_extension_footer",
        "set_extension_header",
        "set_extension_status",
        "set_extension_widget",
        "set_hidden_thinking_label",
        "set_terminal_title",
        "set_working_indicator",
        "set_working_message",
        "set_working_visible",
        "setup_autocomplete_provider",
        "status",
        "theme_registry",
        "tui",
    )


class InteractiveTurnSurface(PortBoundController[InteractiveTurnDependencies]):
    """Attribute surface for the turns controller."""

    __slots__ = (
        "_agent_abort_requested",
        "_clear_motion_signal",
        "_command_executor",
        "_dispatch_extension_command",
        "_dispatch_terminal_input",
        "_last_idle_ctrl_c_at",
        "_last_turn_finished_at",
        "_queued_after_turn",
        "_refresh_footer",
        "_render_auto_compaction_notice",
        "_set_motion_signal",
        "_shutdown_requested",
        "_stream_with_session_generation_params",
        "_turn_future",
        "_turn_lock",
        "_turn_thread",
        "_user_commands",
        "_is_turn_active",
        "active_editor",
        "app",
        "editor_text",
        "footer",
        "history",
        "input_fn",
        "status",
        "tui",
    )
    _turn_lock: AbstractContextManager[object]


class InteractiveShutdownSurface(PortBoundController[InteractiveShutdownDependencies]):
    """Attribute surface for the shutdown controller."""

    __slots__ = (
        "_agent_abort_requested",
        "_handle_sigint",
        "_is_turn_active",
        "_run_loop_active",
        "_session_commands",
        "_shutdown_requested",
        "_turn_future",
        "_turn_lock",
        "_turn_thread",
        "app",
        "tui",
    )
    _turn_lock: AbstractContextManager[object]


class InteractiveMotionSurface(PortBoundController[InteractiveMotionDependencies]):
    """Attribute surface for the motion controller."""

    __slots__ = (
        "_refresh_footer",
        "default_working_message",
        "extension_status_states",
        "extension_statuses",
        "extension_working_active",
        "motion_controller",
        "status",
        "tui",
    )


__all__ = [
    "InteractiveCommandDispatchSurface",
    "InteractiveViewSurface",
    "InteractiveModelAuthSurface",
    "InteractiveParamsSurface",
    "InteractiveProcessSurface",
    "InteractiveLspSurface",
    "InteractiveMemorySurface",
    "InteractiveOperationsSurface",
    "InteractiveSubagentSurface",
    "InteractiveSessionSurface",
    "InteractiveExtensionSurface",
    "InteractiveTurnSurface",
    "InteractiveShutdownSurface",
    "InteractiveMotionSurface",
]
