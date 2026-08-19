"""Explicit dependency records used by native-TUI collaborators."""

from __future__ import annotations

from dataclasses import dataclass, field

from travis.controller_ports import ControllerBinding
from travis.tui.interactive_services import InteractiveServices
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState


@dataclass(slots=True)
class InteractiveRuntimeBindings:
    """Explicit cells owned by the TUI composition root."""

    _abort_active_turn_for_shutdown: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _dispatch_extension_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _dispatch_extension_shortcut: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _dispatch_terminal_input: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_commands: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_host: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _handle_active_turn_prompt: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _handle_editor_escape: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _install_sigint_handler: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _is_registered_extension_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _is_registered_prompt_template: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _is_turn_active: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _line_input_mode: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _open_resume_picker: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _read_prompt_from_line_input: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _read_prompt_from_tui: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _refresh_footer: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _restore_sigint_handler: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_agents_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_auth_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_bash_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_clone_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_copy_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_export_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_fork_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_help_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_import_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_loop_active: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_lsp_status_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_manual_compress: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_memory_status_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_model_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_name_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_new_session_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_operations_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_package_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_params_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_processes_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_reload_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_resume_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_session_info_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_share_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_theme_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_tree_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_trust_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_unknown_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _session_commands: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _set_motion_signal: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _shutdown_requested: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _shutdown_subagent_ui: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _start_turn_thread: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_app_session_rebound: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_footer_branch_change: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_process_events: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_session_events: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_tui_scroll_change: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_tui_terminal_input: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _user_commands: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _wait_for_active_turn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    active_editor: ControllerBinding[object] = field(default_factory=ControllerBinding)
    app: ControllerBinding[object] = field(default_factory=ControllerBinding)
    autocomplete_provider: ControllerBinding[object] = field(default_factory=ControllerBinding)
    editor_container: ControllerBinding[object] = field(default_factory=ControllerBinding)
    editor_text: ControllerBinding[object] = field(default_factory=ControllerBinding)
    footer_data_provider: ControllerBinding[object] = field(default_factory=ControllerBinding)
    history: ControllerBinding[object] = field(default_factory=ControllerBinding)
    init: ControllerBinding[object] = field(default_factory=ControllerBinding)
    motion_controller: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_history: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_label: ControllerBinding[object] = field(default_factory=ControllerBinding)
    status: ControllerBinding[object] = field(default_factory=ControllerBinding)
    theme_context: ControllerBinding[object] = field(default_factory=ControllerBinding)
    tui: ControllerBinding[object] = field(default_factory=ControllerBinding)
    MAX_WIDGET_LINES: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _clear_motion_signal: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _emit_pending_model_picker_trace: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _builtin_theme_records: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _ensure_builtin_themes: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _extension_bindings: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _handle_process_event: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _handle_tui_terminal_input: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _history_populated: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _initialized: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _last_compaction_failure_notice_key: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _reload_resource_themes: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _terminal_input_listeners: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _update_available_provider_count: ControllerBinding[object] = field(default_factory=ControllerBinding)
    autocomplete_provider_wrappers: ControllerBinding[object] = field(default_factory=ControllerBinding)
    built_in_header: ControllerBinding[object] = field(default_factory=ControllerBinding)
    custom_footer: ControllerBinding[object] = field(default_factory=ControllerBinding)
    custom_header: ControllerBinding[object] = field(default_factory=ControllerBinding)
    default_hidden_thinking_label: ControllerBinding[object] = field(default_factory=ControllerBinding)
    extension_statuses: ControllerBinding[object] = field(default_factory=ControllerBinding)
    extension_widgets_above: ControllerBinding[object] = field(default_factory=ControllerBinding)
    extension_widgets_below: ControllerBinding[object] = field(default_factory=ControllerBinding)
    footer: ControllerBinding[object] = field(default_factory=ControllerBinding)
    footer_container: ControllerBinding[object] = field(default_factory=ControllerBinding)
    header_container: ControllerBinding[object] = field(default_factory=ControllerBinding)
    hidden_thinking_label: ControllerBinding[object] = field(default_factory=ControllerBinding)
    hide_thinking_block: ControllerBinding[object] = field(default_factory=ControllerBinding)
    input_fn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    theme_controller: ControllerBinding[object] = field(default_factory=ControllerBinding)
    theme_registry: ControllerBinding[object] = field(default_factory=ControllerBinding)
    widget_container_above: ControllerBinding[object] = field(default_factory=ControllerBinding)
    widget_container_below: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _pending_model_picker_trace: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _refresh_generation_param_state: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _run_session_command: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_extension_input: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_extension_select: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _show_status: ControllerBinding[object] = field(default_factory=ControllerBinding)
    generation_param_warnings: ControllerBinding[object] = field(default_factory=ControllerBinding)
    generation_params: ControllerBinding[object] = field(default_factory=ControllerBinding)
    startup_generation_params: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _command_executor: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _completed_user_commands: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _handle_session_event: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _notified_processes: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _populate_existing_history: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _process_cursors: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _rebind_controller_sessions: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _rebind_subagent_supervisor: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _startup_text: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _user_command_components: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _user_command_order: ControllerBinding[object] = field(default_factory=ControllerBinding)
    setup_autocomplete_provider: ControllerBinding[object] = field(default_factory=ControllerBinding)
    tool_approval_broker: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _subagent_snapshot: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _unsubscribe_subagents: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_extension_confirm: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _rebind_session_ui: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _refresh_extension_motion_signal: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _render_widgets: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _request_shutdown: ControllerBinding[object] = field(default_factory=ControllerBinding)
    extension_status_states: ControllerBinding[object] = field(default_factory=ControllerBinding)
    extension_working_active: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_extension_custom: ControllerBinding[object] = field(default_factory=ControllerBinding)
    prompt_extension_editor: ControllerBinding[object] = field(default_factory=ControllerBinding)
    add_autocomplete_provider: ControllerBinding[object] = field(default_factory=ControllerBinding)
    add_terminal_input_listener: ControllerBinding[object] = field(default_factory=ControllerBinding)
    get_editor_text: ControllerBinding[object] = field(default_factory=ControllerBinding)
    paste_to_editor: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_editor_text: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_extension_footer: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_extension_header: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_extension_status: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_extension_widget: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_hidden_thinking_label: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_terminal_title: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_working_indicator: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_working_message: ControllerBinding[object] = field(default_factory=ControllerBinding)
    set_working_visible: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _agent_abort_requested: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _last_idle_ctrl_c_at: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _last_turn_finished_at: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _queued_after_turn: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _render_auto_compaction_notice: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _stream_with_session_generation_params: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _turn_future: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _turn_lock: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _turn_thread: ControllerBinding[object] = field(default_factory=ControllerBinding)
    _handle_sigint: ControllerBinding[object] = field(default_factory=ControllerBinding)
    default_working_message: ControllerBinding[object] = field(default_factory=ControllerBinding)


@dataclass(frozen=True, slots=True)
class InteractiveCommandDispatchDependencies:
    """Dependencies consumed only by the command dispatch controller."""

    _abort_active_turn_for_shutdown: ControllerBinding[object]
    _dispatch_extension_command: ControllerBinding[object]
    _dispatch_extension_shortcut: ControllerBinding[object]
    _dispatch_terminal_input: ControllerBinding[object]
    _extension_commands: ControllerBinding[object]
    _extension_host: ControllerBinding[object]
    _handle_active_turn_prompt: ControllerBinding[object]
    _handle_editor_escape: ControllerBinding[object]
    _install_sigint_handler: ControllerBinding[object]
    _is_registered_extension_command: ControllerBinding[object]
    _is_registered_prompt_template: ControllerBinding[object]
    _is_turn_active: ControllerBinding[object]
    _line_input_mode: ControllerBinding[object]
    _open_resume_picker: ControllerBinding[object]
    _read_prompt_from_line_input: ControllerBinding[object]
    _read_prompt_from_tui: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _restore_sigint_handler: ControllerBinding[object]
    _run_agents_command: ControllerBinding[object]
    _run_auth_command: ControllerBinding[object]
    _run_bash_command: ControllerBinding[object]
    _run_clone_command: ControllerBinding[object]
    _run_copy_command: ControllerBinding[object]
    _run_export_command: ControllerBinding[object]
    _run_fork_command: ControllerBinding[object]
    _run_help_command: ControllerBinding[object]
    _run_import_command: ControllerBinding[object]
    _run_loop_active: ControllerBinding[object]
    _run_lsp_status_command: ControllerBinding[object]
    _run_manual_compress: ControllerBinding[object]
    _run_memory_status_command: ControllerBinding[object]
    _run_model_command: ControllerBinding[object]
    _run_name_command: ControllerBinding[object]
    _run_new_session_command: ControllerBinding[object]
    _run_operations_command: ControllerBinding[object]
    _run_package_command: ControllerBinding[object]
    _run_params_command: ControllerBinding[object]
    _run_processes_command: ControllerBinding[object]
    _run_reload_command: ControllerBinding[object]
    _run_resume_command: ControllerBinding[object]
    _run_session_info_command: ControllerBinding[object]
    _run_share_command: ControllerBinding[object]
    _run_theme_command: ControllerBinding[object]
    _run_tree_command: ControllerBinding[object]
    _run_trust_command: ControllerBinding[object]
    _run_unknown_command: ControllerBinding[object]
    _session_commands: ControllerBinding[object]
    _set_motion_signal: ControllerBinding[object]
    _shutdown_requested: ControllerBinding[object]
    _shutdown_subagent_ui: ControllerBinding[object]
    _start_turn_thread: ControllerBinding[object]
    _unsubscribe_app_session_rebound: ControllerBinding[object]
    _unsubscribe_footer_branch_change: ControllerBinding[object]
    _unsubscribe_process_events: ControllerBinding[object]
    _unsubscribe_session_events: ControllerBinding[object]
    _unsubscribe_tui_scroll_change: ControllerBinding[object]
    _unsubscribe_tui_terminal_input: ControllerBinding[object]
    _user_commands: ControllerBinding[object]
    _wait_for_active_turn: ControllerBinding[object]
    active_editor: ControllerBinding[object]
    app: ControllerBinding[object]
    autocomplete_provider: ControllerBinding[object]
    editor_container: ControllerBinding[object]
    editor_text: ControllerBinding[object]
    footer_data_provider: ControllerBinding[object]
    history: ControllerBinding[object]
    init: ControllerBinding[object]
    motion_controller: ControllerBinding[object]
    prompt_history: ControllerBinding[object]
    prompt_label: ControllerBinding[object]
    status: ControllerBinding[object]
    theme_context: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveViewDependencies:
    """Dependencies consumed only by the view controller."""

    MAX_WIDGET_LINES: ControllerBinding[object]
    _clear_motion_signal: ControllerBinding[object]
    _emit_pending_model_picker_trace: ControllerBinding[object]
    _builtin_theme_records: ControllerBinding[object]
    _ensure_builtin_themes: ControllerBinding[object]
    _extension_bindings: ControllerBinding[object]
    _extension_host: ControllerBinding[object]
    _handle_process_event: ControllerBinding[object]
    _handle_tui_terminal_input: ControllerBinding[object]
    _history_populated: ControllerBinding[object]
    _initialized: ControllerBinding[object]
    _is_turn_active: ControllerBinding[object]
    _last_compaction_failure_notice_key: ControllerBinding[object]
    _line_input_mode: ControllerBinding[object]
    _read_prompt_from_line_input: ControllerBinding[object]
    _reload_resource_themes: ControllerBinding[object]
    _set_motion_signal: ControllerBinding[object]
    _shutdown_requested: ControllerBinding[object]
    _terminal_input_listeners: ControllerBinding[object]
    _unsubscribe_footer_branch_change: ControllerBinding[object]
    _unsubscribe_process_events: ControllerBinding[object]
    _unsubscribe_session_events: ControllerBinding[object]
    _unsubscribe_tui_terminal_input: ControllerBinding[object]
    _update_available_provider_count: ControllerBinding[object]
    active_editor: ControllerBinding[object]
    app: ControllerBinding[object]
    autocomplete_provider: ControllerBinding[object]
    autocomplete_provider_wrappers: ControllerBinding[object]
    built_in_header: ControllerBinding[object]
    custom_footer: ControllerBinding[object]
    custom_header: ControllerBinding[object]
    default_hidden_thinking_label: ControllerBinding[object]
    editor_container: ControllerBinding[object]
    editor_text: ControllerBinding[object]
    extension_statuses: ControllerBinding[object]
    extension_widgets_above: ControllerBinding[object]
    extension_widgets_below: ControllerBinding[object]
    footer: ControllerBinding[object]
    footer_container: ControllerBinding[object]
    footer_data_provider: ControllerBinding[object]
    header_container: ControllerBinding[object]
    hidden_thinking_label: ControllerBinding[object]
    hide_thinking_block: ControllerBinding[object]
    history: ControllerBinding[object]
    input_fn: ControllerBinding[object]
    status: ControllerBinding[object]
    theme_context: ControllerBinding[object]
    theme_controller: ControllerBinding[object]
    theme_registry: ControllerBinding[object]
    tui: ControllerBinding[object]
    widget_container_above: ControllerBinding[object]
    widget_container_below: ControllerBinding[object]
    session: ControllerBinding[object]
    state: InteractiveState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveModelAuthDependencies:
    """Dependencies consumed only by the model auth controller."""

    _pending_model_picker_trace: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _refresh_generation_param_state: ControllerBinding[object]
    _run_session_command: ControllerBinding[object]
    app: ControllerBinding[object]
    footer_data_provider: ControllerBinding[object]
    history: ControllerBinding[object]
    prompt_extension_input: ControllerBinding[object]
    prompt_extension_select: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveParamsDependencies:
    """Dependencies consumed only by the params controller."""

    _is_turn_active: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _show_status: ControllerBinding[object]
    app: ControllerBinding[object]
    generation_param_warnings: ControllerBinding[object]
    generation_params: ControllerBinding[object]
    startup_generation_params: ControllerBinding[object]
    tui: ControllerBinding[object]
    session: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveProcessDependencies:
    """Dependencies consumed only by the processes controller."""

    _clear_motion_signal: ControllerBinding[object]
    _command_executor: ControllerBinding[object]
    _completed_user_commands: ControllerBinding[object]
    _handle_session_event: ControllerBinding[object]
    _history_populated: ControllerBinding[object]
    _initialized: ControllerBinding[object]
    _is_turn_active: ControllerBinding[object]
    _notified_processes: ControllerBinding[object]
    _populate_existing_history: ControllerBinding[object]
    _process_cursors: ControllerBinding[object]
    _rebind_controller_sessions: ControllerBinding[object]
    _rebind_subagent_supervisor: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _refresh_generation_param_state: ControllerBinding[object]
    _set_motion_signal: ControllerBinding[object]
    _shutdown_requested: ControllerBinding[object]
    _startup_text: ControllerBinding[object]
    _unsubscribe_session_events: ControllerBinding[object]
    _user_command_components: ControllerBinding[object]
    _user_command_order: ControllerBinding[object]
    _user_commands: ControllerBinding[object]
    app: ControllerBinding[object]
    built_in_header: ControllerBinding[object]
    footer: ControllerBinding[object]
    hidden_thinking_label: ControllerBinding[object]
    hide_thinking_block: ControllerBinding[object]
    history: ControllerBinding[object]
    prompt_extension_select: ControllerBinding[object]
    setup_autocomplete_provider: ControllerBinding[object]
    status: ControllerBinding[object]
    tool_approval_broker: ControllerBinding[object]
    tui: ControllerBinding[object]
    session: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveLspDependencies:
    """Dependencies consumed only by the lsp controller."""

    _refresh_footer: ControllerBinding[object]
    app: ControllerBinding[object]
    history: ControllerBinding[object]
    status: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveMemoryDependencies:
    """Dependencies consumed only by the memory controller."""

    _refresh_footer: ControllerBinding[object]
    app: ControllerBinding[object]
    history: ControllerBinding[object]
    status: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveOperationsDependencies:
    """Dependencies consumed only by the operations controller."""

    _refresh_footer: ControllerBinding[object]
    app: ControllerBinding[object]
    history: ControllerBinding[object]
    status: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveSubagentDependencies:
    """Dependencies consumed only by the subagents controller."""

    _refresh_footer: ControllerBinding[object]
    _subagent_snapshot: ControllerBinding[object]
    _unsubscribe_subagents: ControllerBinding[object]
    app: ControllerBinding[object]
    history: ControllerBinding[object]
    prompt_extension_confirm: ControllerBinding[object]
    status: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveSessionDependencies:
    """Dependencies consumed only by the sessions controller."""

    _clear_motion_signal: ControllerBinding[object]
    _is_turn_active: ControllerBinding[object]
    _rebind_session_ui: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _session_commands: ControllerBinding[object]
    _set_motion_signal: ControllerBinding[object]
    app: ControllerBinding[object]
    editor_text: ControllerBinding[object]
    history: ControllerBinding[object]
    prompt_extension_select: ControllerBinding[object]
    status: ControllerBinding[object]
    theme_registry: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveExtensionDependencies:
    """Dependencies consumed only by the extensions controller."""

    _clear_motion_signal: ControllerBinding[object]
    _extension_commands: ControllerBinding[object]
    _refresh_extension_motion_signal: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _reload_resource_themes: ControllerBinding[object]
    _render_widgets: ControllerBinding[object]
    _request_shutdown: ControllerBinding[object]
    _run_session_command: ControllerBinding[object]
    _set_motion_signal: ControllerBinding[object]
    _terminal_input_listeners: ControllerBinding[object]
    app: ControllerBinding[object]
    autocomplete_provider_wrappers: ControllerBinding[object]
    extension_status_states: ControllerBinding[object]
    extension_statuses: ControllerBinding[object]
    extension_widgets_above: ControllerBinding[object]
    extension_widgets_below: ControllerBinding[object]
    extension_working_active: ControllerBinding[object]
    history: ControllerBinding[object]
    prompt_extension_confirm: ControllerBinding[object]
    prompt_extension_custom: ControllerBinding[object]
    prompt_extension_editor: ControllerBinding[object]
    prompt_extension_input: ControllerBinding[object]
    prompt_extension_select: ControllerBinding[object]
    add_autocomplete_provider: ControllerBinding[object]
    add_terminal_input_listener: ControllerBinding[object]
    get_editor_text: ControllerBinding[object]
    paste_to_editor: ControllerBinding[object]
    set_editor_text: ControllerBinding[object]
    set_extension_footer: ControllerBinding[object]
    set_extension_header: ControllerBinding[object]
    set_extension_status: ControllerBinding[object]
    set_extension_widget: ControllerBinding[object]
    set_hidden_thinking_label: ControllerBinding[object]
    set_terminal_title: ControllerBinding[object]
    set_working_indicator: ControllerBinding[object]
    set_working_message: ControllerBinding[object]
    set_working_visible: ControllerBinding[object]
    setup_autocomplete_provider: ControllerBinding[object]
    status: ControllerBinding[object]
    theme_registry: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveTurnDependencies:
    """Dependencies consumed only by the turns controller."""

    _agent_abort_requested: ControllerBinding[object]
    _clear_motion_signal: ControllerBinding[object]
    _command_executor: ControllerBinding[object]
    _dispatch_extension_command: ControllerBinding[object]
    _dispatch_terminal_input: ControllerBinding[object]
    _last_idle_ctrl_c_at: ControllerBinding[object]
    _last_turn_finished_at: ControllerBinding[object]
    _queued_after_turn: ControllerBinding[object]
    _refresh_footer: ControllerBinding[object]
    _render_auto_compaction_notice: ControllerBinding[object]
    _set_motion_signal: ControllerBinding[object]
    _shutdown_requested: ControllerBinding[object]
    _stream_with_session_generation_params: ControllerBinding[object]
    _turn_future: ControllerBinding[object]
    _turn_lock: ControllerBinding[object]
    _turn_thread: ControllerBinding[object]
    _user_commands: ControllerBinding[object]
    _is_turn_active: ControllerBinding[object]
    active_editor: ControllerBinding[object]
    app: ControllerBinding[object]
    editor_text: ControllerBinding[object]
    footer: ControllerBinding[object]
    history: ControllerBinding[object]
    input_fn: ControllerBinding[object]
    status: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveShutdownDependencies:
    """Dependencies consumed only by the shutdown controller."""

    _agent_abort_requested: ControllerBinding[object]
    _handle_sigint: ControllerBinding[object]
    _is_turn_active: ControllerBinding[object]
    _run_loop_active: ControllerBinding[object]
    _session_commands: ControllerBinding[object]
    _shutdown_requested: ControllerBinding[object]
    _turn_future: ControllerBinding[object]
    _turn_lock: ControllerBinding[object]
    _turn_thread: ControllerBinding[object]
    app: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveMotionDependencies:
    """Dependencies consumed only by the motion controller."""

    _refresh_footer: ControllerBinding[object]
    default_working_message: ControllerBinding[object]
    extension_status_states: ControllerBinding[object]
    extension_statuses: ControllerBinding[object]
    extension_working_active: ControllerBinding[object]
    motion_controller: ControllerBinding[object]
    status: ControllerBinding[object]
    tui: ControllerBinding[object]
    state: InteractiveState
    services: InteractiveServices


__all__ = [
    "InteractiveCommandDispatchDependencies",
    "InteractiveViewDependencies",
    "InteractiveModelAuthDependencies",
    "InteractiveParamsDependencies",
    "InteractiveProcessDependencies",
    "InteractiveLspDependencies",
    "InteractiveMemoryDependencies",
    "InteractiveOperationsDependencies",
    "InteractiveSubagentDependencies",
    "InteractiveSessionDependencies",
    "InteractiveExtensionDependencies",
    "InteractiveTurnDependencies",
    "InteractiveShutdownDependencies",
    "InteractiveMotionDependencies",
    "InteractiveRuntimeBindings",
]
