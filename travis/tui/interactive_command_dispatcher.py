"""Focused command dispatcher ownership for the TUI."""

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
from travis.coding_agent.session_types import BashResult
from travis.coding_agent.session_catalog import SessionInfo
from travis.coding_agent.session_commands import SessionCommandExecutor
from travis.coding_agent.processes.types import ProcessEvent, ProcessSnapshot, ProcessState
from travis.coding_agent.tools.bash import BashExecOptions, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.tui.components import (
    CombinedAutocompleteProvider,
    Component,
    Container,
    Editor,
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

from travis.tui.interactive_shutdown import SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS

def _is_manual_compression_command(prompt: str) -> bool:
    return prompt in {"/compress", "/compact"} or prompt.startswith("/compress ") or prompt.startswith("/compact ")


def _is_command_like_slash_prompt(prompt: str) -> bool:
    if not prompt.startswith("/"):
        return False
    command_name = prompt[1:].partition(" ")[0]
    return bool(command_name) and "/" not in command_name


def _is_prompt_level_skill_trigger(prompt: str) -> bool:
    return prompt == "/subagents" or prompt.startswith("/subagents ")


def _is_help_command(prompt: str) -> bool:
    return prompt == "/help" or prompt.startswith("/help ")


def _is_processes_command(prompt: str) -> bool:
    return prompt == "/processes"


def _is_reload_command(prompt: str) -> bool:
    return prompt == "/reload"


def _is_trust_command(prompt: str) -> bool:
    return prompt == "/trust"


def _parse_session_command(prompt: str) -> str | None:
    if prompt == "/resume":
        return "resume"
    if prompt == "/new":
        return "new"
    if prompt == "/session":
        return "session"
    if prompt == "/name" or prompt.startswith("/name "):
        return "name"
    if prompt == "/fork":
        return "fork"
    if prompt == "/clone":
        return "clone"
    if prompt == "/tree":
        return "tree"
    if prompt == "/export" or prompt.startswith("/export "):
        return "export"
    if prompt == "/import" or prompt.startswith("/import "):
        return "import"
    if prompt == "/copy":
        return "copy"
    if prompt == "/share":
        return "share"
    if prompt == "/theme" or prompt.startswith("/theme "):
        return "theme"
    return None


def _parse_auth_command(prompt: str) -> tuple[str, str | None] | None:
    if prompt == "/login":
        return "login", None
    if prompt == "/logout":
        return "logout", None
    return None


def _parse_model_command(prompt: str) -> tuple[str, str | None] | None:
    if prompt == "/models":
        return "list", None
    if prompt == "/model":
        return "select", None
    if prompt.startswith("/model "):
        return "select", prompt[len("/model ") :].strip()
    return None


def _parse_params_command(prompt: str) -> str | None:
    if prompt == "/params":
        return ""
    if prompt.startswith("/params "):
        return prompt[len("/params ") :].strip()
    return None


def _is_openrouter_model(model) -> bool:
    return getattr(model, "provider", "") == "openrouter" or "openrouter.ai" in str(getattr(model, "base_url", ""))

def _parse_bash_command(prompt: str) -> tuple[str, bool] | None:
    if not prompt.startswith("!"):
        return None
    excluded = prompt.startswith("!!")
    command = prompt[2:].strip() if excluded else prompt[1:].strip()
    if not command:
        return None
    return command, excluded

class InteractiveCommandDispatcher:
    """Owns a focused interactive runtime concern."""

    def run(self) -> int:
        self._run_loop_active = True
        self.init()
        previous_sigint_handler = self._install_sigint_handler()
        try:
            if self._open_resume_picker:
                self._open_resume_picker = False
                if not self._run_resume_command(startup=True):
                    return 0
            while True:
                submitted: list[str] = []
                submitted_queue: queue.Queue[str] = queue.Queue()

                def on_submit(value: str) -> None:
                    submitted.append(value)
                    submitted_queue.put(value)

                prompt_component = Editor(
                    value=self.editor_text,
                    prompt=self.prompt_label,
                    on_submit=on_submit,
                    theme_context=self.theme_context,
                )
                prompt_component.set_history(self.prompt_history)
                prompt_component.on_escape = self._handle_editor_escape
                prompt_component.on_escape = self._handle_editor_escape
                prompt_component.set_autocomplete_provider(self.autocomplete_provider)
                self.active_editor = prompt_component
                self.editor_container.add(prompt_component)
                self.tui.set_focus(prompt_component)
                self.tui.request_render()

                if self._line_input_mode:
                    try:
                        prompt_text = self._read_prompt_from_line_input()
                    except EOFError:
                        return 0
                    dispatch_result = self._dispatch_terminal_input(prompt_text)
                    if dispatch_result[0]:
                        self.tui.set_focus(None)
                        self.editor_container.remove(prompt_component)
                        self.editor_text = prompt_component.get_value()
                        self.active_editor = None
                        self.tui.request_render()
                        continue
                    prompt_text = dispatch_result[1]
                    prompt_component.handle_input(f"{prompt_text}\r")
                    prompt = (submitted[0] if submitted else prompt_component.get_value()).strip()
                else:
                    prompt_value = self._read_prompt_from_tui(submitted_queue)
                    if prompt_value is None:
                        return 0
                    prompt = prompt_value.strip()

                self.tui.set_focus(None)
                self.editor_container.remove(prompt_component)
                self.active_editor = None
                if self._dispatch_extension_shortcut(prompt):
                    if self._shutdown_requested:
                        self.status.set_message("Exiting")
                        self._refresh_footer()
                        self.tui.request_render()
                        return 0
                    self._refresh_footer()
                    self.tui.request_render()
                    continue
                self.editor_text = ""
                self.tui.scroll_to_bottom()
                if prompt:
                    self.history.add(user_message_to_component(prompt))
                else:
                    self.history.add(Text(""))
                self.tui.request_render()

                if prompt in {"/exit", "/quit", "exit", "quit"}:
                    self._shutdown_requested = True
                    self.status.set_message("Exiting")
                    self._refresh_footer()
                    self.tui.request_render()
                    return 0
                if not prompt:
                    continue
                prompt_component.add_to_history(prompt)
                if _is_help_command(prompt):
                    self._run_help_command()
                    continue
                session_command = _parse_session_command(prompt)
                if session_command == "resume":
                    self._run_resume_command()
                    continue
                if session_command == "new":
                    self._run_new_session_command()
                    continue
                if session_command == "session":
                    self._run_session_info_command()
                    continue
                if session_command == "name":
                    self._run_name_command(prompt)
                    continue
                if session_command == "fork":
                    self._run_fork_command()
                    continue
                if session_command == "clone":
                    self._run_clone_command()
                    continue
                if session_command == "tree":
                    self._run_tree_command()
                    continue
                if session_command == "export":
                    self._run_export_command(prompt)
                    continue
                if session_command == "import":
                    self._run_import_command(prompt)
                    continue
                if session_command == "copy":
                    self._run_copy_command()
                    continue
                if session_command == "share":
                    self._run_share_command()
                    continue
                if session_command == "theme":
                    self._run_theme_command(prompt)
                    continue
                if _is_processes_command(prompt):
                    self._run_processes_command()
                    continue
                if _is_reload_command(prompt):
                    self._run_reload_command()
                    continue
                if _is_trust_command(prompt):
                    self._run_trust_command()
                    continue
                if self._run_package_command(prompt):
                    continue
                bash_command = _parse_bash_command(prompt)
                if bash_command:
                    self._run_bash_command(bash_command[0], exclude_from_context=bash_command[1])
                    continue
                if _is_manual_compression_command(prompt):
                    self._run_manual_compress(prompt)
                    continue
                auth_command = _parse_auth_command(prompt)
                if auth_command:
                    self._run_auth_command(auth_command[0], auth_command[1])
                    continue
                model_command = _parse_model_command(prompt)
                if model_command:
                    self._run_model_command(model_command[0], model_command[1])
                    continue
                params_query = _parse_params_command(prompt)
                if params_query is not None:
                    self._run_params_command(params_query)
                    continue
                if self._dispatch_extension_command(prompt):
                    self._refresh_footer()
                    self.tui.request_render()
                    continue
                if (
                    _is_command_like_slash_prompt(prompt)
                    and not _is_prompt_level_skill_trigger(prompt)
                    and not self._is_registered_extension_command(prompt)
                ):
                    self._run_unknown_command(prompt)
                    continue
                if self._handle_active_turn_prompt(prompt):
                    continue
                self.status.set_message("Running")
                before_compressions = self.app.compaction.compressor.compression_count
                before_tokens = estimate_tokens(self.app.messages)
                self._refresh_footer()
                self.tui.request_render()
                self._start_turn_thread(prompt, before_compressions, before_tokens)
        finally:
            self._shutdown_requested = True
            if self._user_commands is not None:
                self._user_commands.close()
            self.tui.drain_dispatcher()
            if not self._wait_for_active_turn():
                self._abort_active_turn_for_shutdown()
                self._wait_for_active_turn()
            self.tui.drain_dispatcher()
            self._run_loop_active = False
            if self._session_commands is not None:
                self._session_commands.close(timeout=SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS)
                self._session_commands = None
            if self._unsubscribe_session_events is not None:
                self._unsubscribe_session_events()
                self._unsubscribe_session_events = None
            if self._unsubscribe_footer_branch_change is not None:
                self._unsubscribe_footer_branch_change()
                self._unsubscribe_footer_branch_change = None
            if self._unsubscribe_tui_terminal_input is not None:
                self._unsubscribe_tui_terminal_input()
                self._unsubscribe_tui_terminal_input = None
            if self._unsubscribe_tui_scroll_change is not None:
                self._unsubscribe_tui_scroll_change()
                self._unsubscribe_tui_scroll_change = None
            if self._unsubscribe_app_session_rebound is not None:
                self._unsubscribe_app_session_rebound()
                self._unsubscribe_app_session_rebound = None
            if self._unsubscribe_process_events is not None:
                self._unsubscribe_process_events()
                self._unsubscribe_process_events = None
            self.footer_data_provider.dispose()
            if self.app.event_trace is not None:
                self.app.event_trace.write("shutdown", {"status": "ok"})
            self.tui.stop()
            self._restore_sigint_handler(previous_sigint_handler)

__all__ = (
    'InteractiveCommandDispatcher',
    '_is_command_like_slash_prompt',
    '_is_help_command',
    '_is_manual_compression_command',
    '_is_openrouter_model',
    '_is_processes_command',
    '_is_reload_command',
    '_is_trust_command',
    '_is_prompt_level_skill_trigger',
    '_parse_auth_command',
    '_parse_bash_command',
    '_parse_model_command',
    '_parse_params_command',
    '_parse_session_command',
)
