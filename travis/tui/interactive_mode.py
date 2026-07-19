"""InteractiveMode composition facade."""

from __future__ import annotations

import inspect
import json
import os
import queue
import signal as signal_module
import subprocess
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from travis.ai.providers.capabilities import ProviderParamWarning
from travis.ai.providers.params import GenerationParams, compact_generation_params_display
from travis.compaction import estimate_tokens
from travis.coding_agent.agent_session import BashResult
from travis.coding_agent.extension_host import ExtensionHostAdapter
from travis.coding_agent.session_catalog import SessionInfo
from travis.coding_agent.session_commands import SessionCommandExecutor
from travis.coding_agent.source_info import SourceInfo
from travis.coding_agent.themes import Theme, ThemeRegistry
from travis.coding_agent.processes.types import ProcessEvent, ProcessSnapshot, ProcessState
from travis.coding_agent.tools.bash import BashExecOptions, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.tui.components import (
    CombinedAutocompleteProvider,
    Component,
    Container,
    FooterComponent,
    Input,
    Spacer,
    StatusLine,
    Text,
)
from travis.tui.components.autocomplete import _call_autocomplete_method, _settle_autocomplete_result
from travis.tui.interactive import (
    AssistantMessageComponent,
    BashExecutionComponent,
    message_to_component,
    user_message_to_component,
)
from travis.tui.user_commands import (
    ResolvedUserCommand,
    UserCommandBinding,
    UserCommandController,
    UserCommandHandle,
)
from travis.tui.builtin_themes import BUILTIN_THEMES, resolve_builtin_theme
from travis.tui.motion import MotionController
from travis.tui.theme import ThemeContext
from travis.tui.theme_controller import ThemeController

from travis.tui.interactive_command_dispatcher import *  # noqa: F403
from travis.tui.interactive_extensions import *  # noqa: F403
from travis.tui.interactive_model_auth import *  # noqa: F403
from travis.tui.interactive_motion import *  # noqa: F403
from travis.tui.interactive_params import *  # noqa: F403
from travis.tui.interactive_process_commands import *  # noqa: F403
from travis.tui.interactive_session_commands import *  # noqa: F403
from travis.tui.interactive_shutdown import *  # noqa: F403
from travis.tui.interactive_turn_controller import *  # noqa: F403
from travis.tui.interactive_view import *  # noqa: F403
from travis.tui.footer_data import *  # noqa: F403
from travis.runtime_facade import RuntimeFacade

from travis.tui.footer_data import _ExtensionFooterDataProvider
from travis.tui.interactive_shutdown import InputFn


def _builtin_theme_records() -> list[Theme]:
    records: list[Theme] = []
    for name, definition in BUILTIN_THEMES.items():
        records.append(
            Theme(
                name=name,
                colors=dict(definition["colors"]),
                vars=dict(definition["vars"]),
                source_path="",
                source_info=SourceInfo(
                    path=f"builtin:{name}",
                    source="travis234-builtin",
                    scope="built-in",
                ),
            )
        )
    return records


def _terminal_color_mode() -> str:
    if "NO_COLOR" in os.environ or os.environ.get("TERM", "").lower() == "dumb":
        return "none"
    if "256color" in os.environ.get("TERM", "").lower():
        return "256color"
    return "truecolor"

class _InteractiveRuntime(
    InteractiveCommandDispatcher,
    InteractiveExtensions,
    InteractiveModelAuth,
    InteractiveMotion,
    InteractiveParams,
    InteractiveProcessCommands,
    InteractiveSessionCommands,
    InteractiveShutdown,
    InteractiveTurnController,
    InteractiveView,
):
    """Internal TUI runtime assembled from focused behavior owners."""

    MAX_WIDGET_LINES = 10
    def __init__(
        self,
        app,
        *,
        input_fn: InputFn | None = None,
        prompt_label: str = "travis> ",
        generation_params: GenerationParams | None = None,
        generation_param_warnings: list[ProviderParamWarning] | None = None,
        open_resume_picker: bool = False,
    ) -> None:
        self.app = app
        self.startup_generation_params = generation_params or GenerationParams()
        self.generation_params = self.startup_generation_params
        self.generation_param_warnings = list(generation_param_warnings or [])
        self._refresh_generation_param_state()
        self._open_resume_picker = bool(open_resume_picker)
        self.tui = app.tui
        self.input_fn = input_fn or input
        self._line_input_mode = input_fn is not None
        self.prompt_label = prompt_label
        self.theme_registry = ThemeRegistry()
        self._builtin_theme_records = _builtin_theme_records()
        self.theme_registry.register_many(self._builtin_theme_records)
        resource_loader = getattr(app.session, "resource_loader", None)
        discovered_themes = (
            resource_loader.get_themes().get("themes", [])
            if resource_loader is not None
            else []
        )
        self.theme_registry.register_many(
            [
                theme
                for theme in discovered_themes
                if isinstance(theme, Theme)
            ]
        )
        color_mode = _terminal_color_mode()
        initial_theme, _ = resolve_builtin_theme("Signal Glass", color_mode=color_mode)
        self.theme_context = ThemeContext(initial_theme)
        self._theme_render_ready = False
        self.theme_controller = ThemeController(
            self.theme_registry,
            getattr(app.session, "settings_manager", None),
            self.theme_context,
            lambda: self.tui.request_render() if self._theme_render_ready else None,
            color_mode=color_mode,
        )
        self.theme_controller.select_persisted()
        self.history = Container(theme_context=self.theme_context)
        self.status = StatusLine("Idle", theme_context=self.theme_context)
        motion_enabled = os.environ.get("TRAVIS234_MOTION", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.motion_controller = MotionController(
            schedule=self.tui.dispatcher.call_later,
            on_change=lambda snapshot: self.status.set_indicator(
                snapshot.indicator or None,
                position="suffix",
            ),
            request_render=self.tui.request_render,
            enabled=motion_enabled,
            static=color_mode == "none",
        )
        self.default_working_message = "Idle"
        self.default_hidden_thinking_label = ""
        self.hidden_thinking_label = self.default_hidden_thinking_label
        self.hide_thinking_block = True
        self.editor_text = ""
        self.prompt_history: list[str] = []
        self.active_editor: Input | None = None
        self.extension_statuses: dict[str, str] = {}
        self.extension_status_states: dict[str, str] = {}
        self.extension_working_active = False
        self.extension_widgets_above: dict[str, Component] = {}
        self.extension_widgets_below: dict[str, Component] = {}
        self._terminal_input_listeners: list[Callable[[str], object]] = []
        self.autocomplete_provider_wrappers: list[Callable[[object], object]] = []
        self.autocomplete_provider: object | None = None
        self._session_commands: SessionCommandExecutor | None = None
        self._extension_commands: SessionCommandExecutor | None = None
        self._turn_future: Future[object] | None = None
        self._turn_thread: threading.Thread | None = None
        self._turn_lock = threading.RLock()
        self._queued_after_turn: list[str] = []
        self._unsubscribe_session_events: Callable[[], None] | None = None
        self._unsubscribe_footer_branch_change: Callable[[], None] | None = None
        self._unsubscribe_tui_terminal_input: Callable[[], None] | None = None
        self._unsubscribe_tui_scroll_change: Callable[[], None] | None = None
        self._unsubscribe_app_session_rebound: Callable[[], None] | None = None
        self._unsubscribe_process_events: Callable[[], None] | None = None
        self._extension_host: ExtensionHostAdapter | None = None
        self._notified_processes: set[str] = set()
        self._process_cursors: dict[str, int] = {}
        self._user_command_components: dict[str, BashExecutionComponent] = {}
        self._user_command_order: list[str] = []
        self._completed_user_commands: dict[
            str, tuple[UserCommandHandle, BashResult] | None
        ] = {}
        self._user_commands: UserCommandController | None = None
        if all(
            hasattr(self.app, name)
            for name in ("process_service", "process_owner", "user_command_transport")
        ):
            self._user_commands = UserCommandController(
                service=self.app.process_service,
                owner_factory=lambda: self.app.process_owner(origin="user"),
                resolver=self._resolve_user_command,
                transport_factory=self.app.user_command_transport,
                on_output=lambda command_id, text: self.tui.post(
                    lambda: self._append_user_command_output(command_id, text)
                ),
                on_complete=lambda handle, result: self.tui.post(
                    lambda: self._finish_user_command(handle, result)
                ),
                on_error=lambda handle, message: self.tui.post(
                    lambda: self._fail_user_command(handle.command_id, message)
                ),
            )
        self.built_in_header = Text(self._startup_text(), role="accent")
        self.header_container = Container([self.built_in_header, Spacer(1)], theme_context=self.theme_context)
        self.custom_header: object | None = None
        self.widget_container_above = Container(theme_context=self.theme_context)
        self.editor_container = Container(theme_context=self.theme_context)
        self.widget_container_below = Container(theme_context=self.theme_context)
        self.footer = FooterComponent(
            cwd=str(app.cwd),
            model=app.session.model.id,
            provider=app.session.model.provider,
            thinking_level=app.session.thinking_level,
            session_name=app.session.session_name,
            extension_statuses=self.extension_statuses,
            theme_context=self.theme_context,
        )
        self.footer_container = Container([self.footer])
        self.footer_data_provider = _ExtensionFooterDataProvider(self)
        self.custom_footer: object | None = None
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_output_container"):
            app.renderer.set_output_container(self.history)
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_theme_context"):
            app.renderer.set_theme_context(self.theme_context)
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_hidden_thinking_label"):
            app.renderer.set_hidden_thinking_label(self.hidden_thinking_label)
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_hide_thinking_block"):
            app.renderer.set_hide_thinking_block(self.hide_thinking_block)
        self._initialized = False
        self._history_populated = False
        self._shutdown_requested = False
        self._run_loop_active = False
        self._pending_model_picker_trace: tuple[int, str] | None = None
        self._last_turn_finished_at = 0.0
        self._last_idle_ctrl_c_at = 0.0
        self._agent_abort_requested = False
        self._last_compaction_failure_notice_key: tuple[str, str] | None = None
        if callable(getattr(app, "subscribe_session_rebound", None)):
            self._extension_host = ExtensionHostAdapter(
                app,
                mode="tui",
                bindings_factory=self._extension_bindings,
                before_rebind=lambda _session: self._reset_extension_ui(),
                on_rebound=lambda _session: self.tui.post(self._rebind_session_ui),
            )
        self.setup_autocomplete_provider()
        self._theme_render_ready = True

    def _ensure_builtin_themes(self) -> None:
        existing = {theme.name for theme in self.theme_registry.list()}
        missing = [theme for theme in self._builtin_theme_records if theme.name not in existing]
        if missing:
            self.theme_registry.register_many(missing)


class InteractiveMode(RuntimeFacade):
    """Stable public facade over the composed interactive runtime."""

    MAX_WIDGET_LINES = 10
    def __init__(self, *args, **kwargs) -> None:
        object.__setattr__(self, "_runtime", _InteractiveRuntime(*args, **kwargs))

    @staticmethod
    def _process_actions(state: ProcessState) -> list[str]:
        return InteractiveProcessCommands._process_actions(state)
