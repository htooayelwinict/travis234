"""Narrow service ports and immutable service bundle for TUI collaborators."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, cast

from travis.controller_ports import ControllerDependencies, ExplicitController
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState


class ControllerDelegate:
    """Descriptor that exposes one named method from an owned controller."""

    __slots__ = ("controller_name", "method_name")

    def __init__(self, controller_name: str, method_name: str) -> None:
        self.controller_name = controller_name
        self.method_name = method_name

    def __get__(self, instance: object, owner: type[object]) -> object:
        if instance is None:
            return self
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        return getattr(controller, self.method_name)

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise TypeError("controller delegates must be bound to a runtime instance")

    def __set__(self, instance: object, value: object) -> None:
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        setattr(controller, self.method_name, value)


def install_controller_delegates(
    owner: type[object],
    methods: dict[str, tuple[str, ...]],
) -> None:
    for controller_name, names in methods.items():
        for name in names:
            if name in owner.__dict__:
                raise ValueError(f"delegate would replace explicit runtime member: {name}")
            setattr(owner, name, ControllerDelegate(controller_name, name))


class PortBoundController[ControllerPortT](
    ExplicitController[ControllerDependencies[ControllerPortT]]
):
    """Own a responsibility-specific dependency record."""

    __slots__ = (
        "MAX_WIDGET_LINES", "_abort_active_turn_for_shutdown", "_agent_abort_requested",
        "_bound_session",
        "_clear_motion_signal", "_command_executor", "_completed_user_commands",
        "_dispatch_extension_command", "_dispatch_extension_shortcut", "_dispatch_terminal_input",
        "_builtin_theme_records", "_emit_pending_model_picker_trace", "_ensure_builtin_themes",
        "_extension_bindings",
        "_extension_commands", "_extension_host", "_handle_active_turn_prompt",
        "_handle_editor_escape", "_handle_process_event", "_handle_session_event", "_handle_sigint",
        "_handle_tui_terminal_input", "_history_populated", "_initialized",
        "_install_sigint_handler", "_is_registered_extension_command",
        "_is_registered_prompt_template", "_is_turn_active",
        "_last_compaction_failure_notice_key", "_last_idle_ctrl_c_at", "_last_turn_finished_at",
        "_line_input_mode", "_notified_processes", "_open_resume_picker",
        "_pending_model_picker_trace", "_populate_existing_history", "_process_cursors",
        "_queued_after_turn", "_read_prompt_from_line_input", "_read_prompt_from_tui",
        "_rebind_session_ui", "_rebind_subagent_supervisor", "_refresh_extension_motion_signal",
        "_refresh_footer", "_refresh_generation_param_state", "_reload_resource_themes",
        "_render_auto_compaction_notice", "_render_widgets", "_request_shutdown",
        "_restore_sigint_handler", "_run_agents_command", "_run_auth_command", "_run_bash_command",
        "_run_clone_command", "_run_copy_command", "_run_export_command", "_run_fork_command",
        "_run_help_command", "_run_import_command", "_run_loop_active",
        "_run_lsp_status_command", "_run_manual_compress", "_run_memory_status_command",
        "_run_model_command", "_run_name_command", "_run_new_session_command",
        "_run_operations_command", "_run_package_command", "_run_params_command",
        "_run_processes_command", "_run_reload_command", "_run_resume_command",
        "_run_session_command", "_run_session_info_command", "_run_share_command",
        "_run_theme_command", "_run_tree_command", "_run_trust_command", "_run_unknown_command",
        "_session_commands", "_set_motion_signal", "_show_status", "_shutdown_requested",
        "_shutdown_subagent_ui", "_start_turn_thread", "_startup_text",
        "_stream_with_session_generation_params", "_subagent_snapshot", "_terminal_input_listeners",
        "_turn_future", "_turn_lock", "_turn_thread", "_unsubscribe_app_session_rebound",
        "_unsubscribe_footer_branch_change", "_unsubscribe_process_events",
        "_unsubscribe_session_events", "_unsubscribe_subagents", "_unsubscribe_tui_scroll_change",
        "_unsubscribe_tui_terminal_input", "_update_available_provider_count",
        "_user_command_components", "_user_command_order", "_user_commands",
        "_wait_for_active_turn", "active_editor", "app", "autocomplete_provider",
        "autocomplete_provider_wrappers", "built_in_header", "custom_footer", "custom_header",
        "default_hidden_thinking_label", "default_working_message", "editor_container", "editor_text",
        "extension_status_states", "extension_statuses", "extension_widgets_above",
        "extension_widgets_below", "extension_working_active", "footer", "footer_container",
        "footer_data_provider", "generation_param_warnings", "generation_params", "header_container",
        "hidden_thinking_label", "hide_thinking_block", "history", "init", "input_fn",
        "motion_controller", "prompt_extension_confirm", "prompt_extension_input",
        "prompt_extension_select", "prompt_history", "prompt_label", "set_extension_footer",
        "set_extension_header", "set_hidden_thinking_label", "set_terminal_title",
        "set_working_indicator", "set_working_message", "set_working_visible",
        "setup_autocomplete_provider", "startup_generation_params", "status", "theme_context",
        "theme_controller", "theme_registry", "tool_approval_broker", "tui",
        "widget_container_above", "widget_container_below",
    )
    _turn_lock: AbstractContextManager[object]



class InteractiveRenderPort(Protocol):
    def add(self, component: object) -> None: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def request_render(self, force: bool = False) -> object | None: ...


class InteractiveStatusPort(Protocol):
    def set_message(self, message: str) -> None: ...

    def set_visible(self, visible: bool) -> None: ...

    def set_indicator(self, indicator: str | None, *, position: str = "suffix") -> None: ...


class InteractiveHistoryPort(Protocol):
    def add(self, component: object) -> None: ...

    def clear(self) -> None: ...


class InteractiveSessionBindingPort(Protocol):
    @property
    def session(self) -> object: ...

    def replace_session(self, session: object) -> object: ...


class InteractiveSessionRebindController(Protocol):
    def rebind_session(self, session: object) -> object: ...


@dataclass(frozen=True, slots=True)
class InteractiveSessionPortAdapter:
    """Typed session-facing value used during transactional controller rebinding."""

    identity: int


class InteractiveOwnerThreadPort(Protocol):
    def is_owner_thread(self) -> bool: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def call_later(self, delay: float, callback: Callable[[], None]) -> object: ...


class InteractiveTerminalInputPort(Protocol):
    def read(self, prompt: str) -> str: ...

    def select(self, title: str, choices: Sequence[str]) -> str | None: ...


class InteractiveThemePort(Protocol):
    def role(self, name: str) -> object: ...


class InteractiveThemeRegistryPort(Protocol):
    def select(self, name: str) -> object: ...


class InteractiveSessionPort(Protocol):
    settings_manager: object


class InteractiveAppPort(Protocol):
    cwd: object
    session: InteractiveSessionPort


class InteractiveControllerPort(Protocol):
    def read(self, name: str) -> object: ...

    def write(self, name: str, value: object) -> None: ...


class InteractiveViewPort(InteractiveControllerPort, Protocol):
    """View-facing state and render services supplied by the composition root."""

    app: InteractiveAppPort
    tui: InteractiveRenderPort
    history: InteractiveHistoryPort
    status: InteractiveStatusPort
    theme_context: InteractiveThemePort

class InteractiveMotionPort(InteractiveControllerPort, Protocol):
    """Motion-facing status state supplied by the composition root."""

    tui: InteractiveRenderPort
    status: InteractiveStatusPort
    motion_controller: object
    extension_statuses: dict[str, str]
    extension_status_states: dict[str, str]

class InteractiveCommandPort(InteractiveControllerPort, Protocol):
    """Command-loop state and named handlers supplied by the composition root."""

    @property
    def app(self) -> InteractiveAppPort: ...

    @property
    def tui(self) -> InteractiveRenderPort: ...

    @property
    def history(self) -> InteractiveHistoryPort: ...

    @property
    def status(self) -> InteractiveStatusPort: ...

    @property
    def extension_statuses(self) -> dict[str, str]: ...

    @property
    def theme_registry(self) -> InteractiveThemeRegistryPort: ...

    def _is_turn_active(self) -> bool: ...

    def _rebind_controller_sessions(self, session: object) -> None: ...

    def add_autocomplete_provider(self, factory: Callable[[object], object]) -> None: ...

    def add_terminal_input_listener(
        self, handler: Callable[[str], object]
    ) -> Callable[[], None]: ...

    def get_editor_text(self) -> str: ...

    def paste_to_editor(self, text: str) -> None: ...

    def prompt_extension_confirm(self, title: str, message: str, options: object = None) -> bool: ...

    def prompt_extension_custom(self, factory: Callable[..., object], options: object = None) -> object: ...

    def prompt_extension_editor(self, title: str, prefill: str | None = None) -> str | None: ...

    def prompt_extension_input(
        self, title: str, placeholder: str | None = None, options: object = None
    ) -> str | None: ...

    def prompt_extension_select(
        self, title: str, options: Sequence[str], dialog_options: object = None
    ) -> str | None: ...

    def set_editor_text(self, text: str) -> None: ...

    def set_extension_footer(self, factory: Callable[..., object] | None = None) -> None: ...

    def set_extension_header(self, factory: Callable[..., object] | None = None) -> None: ...

    def set_extension_status(self, key: str, text: str | None, options: object = None) -> None: ...

    def set_extension_widget(self, key: str, content: object = None, options: object = None) -> None: ...

    def set_hidden_thinking_label(self, label: str | None = None) -> None: ...

    def set_terminal_title(self, title: str) -> None: ...

    def set_working_indicator(self, options: object = None) -> None: ...

    def set_working_message(self, message: str | None = None) -> None: ...

    def set_working_visible(self, visible: bool) -> None: ...


class InteractiveCommandPortAdapter:
    """Explicit command-facing adapter over an allowlisted binding port."""

    __slots__ = ("_port",)

    def __init__(self, port: InteractiveControllerPort) -> None:
        self._port = port

    def read(self, name: str) -> object:
        return self._port.read(name)

    def write(self, name: str, value: object) -> None:
        self._port.write(name, value)

    @property
    def declared_names(self) -> frozenset[str]:
        return cast(frozenset[str], getattr(self._port, "declared_names"))

    @property
    def app(self) -> InteractiveAppPort:
        return cast(InteractiveAppPort, self.read("app"))

    @property
    def tui(self) -> InteractiveRenderPort:
        return cast(InteractiveRenderPort, self.read("tui"))

    @property
    def history(self) -> InteractiveHistoryPort:
        return cast(InteractiveHistoryPort, self.read("history"))

    @property
    def status(self) -> InteractiveStatusPort:
        return cast(InteractiveStatusPort, self.read("status"))

    @property
    def extension_statuses(self) -> dict[str, str]:
        return cast(dict[str, str], self.read("extension_statuses"))

    @property
    def theme_registry(self) -> InteractiveThemeRegistryPort:
        return cast(InteractiveThemeRegistryPort, self.read("theme_registry"))

    def _call(self, name: str, *args: object) -> object:
        return cast(Callable[..., object], self.read(name))(*args)

    def _is_turn_active(self) -> bool:
        return bool(self._call("_is_turn_active"))

    def _rebind_controller_sessions(self, session: object) -> None:
        self._call(
            "_rebind_controller_sessions",
            InteractiveSessionPortAdapter(id(session)),
        )

    def add_autocomplete_provider(self, factory: Callable[[object], object]) -> None:
        self._call("add_autocomplete_provider", factory)

    def add_terminal_input_listener(self, handler: Callable[[str], object]) -> Callable[[], None]:
        return cast(Callable[[], None], self._call("add_terminal_input_listener", handler))

    def get_editor_text(self) -> str:
        return str(self._call("get_editor_text"))

    def paste_to_editor(self, text: str) -> None:
        self._call("paste_to_editor", text)

    def prompt_extension_confirm(self, title: str, message: str, options: object = None) -> bool:
        return bool(self._call("prompt_extension_confirm", title, message, options))

    def prompt_extension_custom(self, factory: Callable[..., object], options: object = None) -> object:
        return self._call("prompt_extension_custom", factory, options)

    def prompt_extension_editor(self, title: str, prefill: str | None = None) -> str | None:
        return cast(str | None, self._call("prompt_extension_editor", title, prefill))

    def prompt_extension_input(
        self, title: str, placeholder: str | None = None, options: object = None
    ) -> str | None:
        return cast(str | None, self._call("prompt_extension_input", title, placeholder, options))

    def prompt_extension_select(
        self, title: str, options: Sequence[str], dialog_options: object = None
    ) -> str | None:
        return cast(str | None, self._call("prompt_extension_select", title, options, dialog_options))

    def set_editor_text(self, text: str) -> None:
        self._call("set_editor_text", text)

    def set_extension_footer(self, factory: Callable[..., object] | None = None) -> None:
        self._call("set_extension_footer", factory)

    def set_extension_header(self, factory: Callable[..., object] | None = None) -> None:
        self._call("set_extension_header", factory)

    def set_extension_status(self, key: str, text: str | None, options: object = None) -> None:
        self._call("set_extension_status", key, text, options)

    def set_extension_widget(self, key: str, content: object = None, options: object = None) -> None:
        self._call("set_extension_widget", key, content, options)

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._call("set_hidden_thinking_label", label)

    def set_terminal_title(self, title: str) -> None:
        self._call("set_terminal_title", title)

    def set_working_indicator(self, options: object = None) -> None:
        self._call("set_working_indicator", options)

    def set_working_message(self, message: str | None = None) -> None:
        self._call("set_working_message", message)

    def set_working_visible(self, visible: bool) -> None:
        self._call("set_working_visible", visible)

@dataclass(frozen=True, slots=True)
class InteractiveServices:
    render: InteractiveRenderPort
    status: InteractiveStatusPort
    history: InteractiveHistoryPort
    sessions: InteractiveSessionBindingPort
    owner_thread: InteractiveOwnerThreadPort
    terminal_input: InteractiveTerminalInputPort
    theme: InteractiveThemePort


@dataclass(frozen=True, slots=True)
class InteractiveCommandDependencies(ControllerDependencies[InteractiveCommandPort]):
    state: InteractiveState
    lifecycle: InteractiveLifecycleState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveMotionDependencies(ControllerDependencies[InteractiveMotionPort]):
    state: InteractiveState
    services: InteractiveServices


@dataclass(frozen=True, slots=True)
class InteractiveViewDependencies(ControllerDependencies[InteractiveViewPort]):
    state: InteractiveState
    services: InteractiveServices


__all__ = [
    "InteractiveHistoryPort",
    "InteractiveAppPort",
    "ControllerDelegate",
    "InteractiveCommandDependencies",
    "InteractiveCommandPortAdapter",
    "InteractiveControllerPort",
    "InteractiveCommandPort",
    "InteractiveMotionPort",
    "InteractiveMotionDependencies",
    "InteractiveOwnerThreadPort",
    "PortBoundController",
    "install_controller_delegates",
    "InteractiveRenderPort",
    "InteractiveServices",
    "InteractiveSessionBindingPort",
    "InteractiveSessionRebindController",
    "InteractiveSessionPort",
    "InteractiveSessionPortAdapter",
    "InteractiveStatusPort",
    "InteractiveTerminalInputPort",
    "InteractiveThemePort",
    "InteractiveThemeRegistryPort",
    "InteractiveViewPort",
    "InteractiveViewDependencies",
]
