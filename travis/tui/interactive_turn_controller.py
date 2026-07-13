"""Focused turn controller ownership for the TUI."""

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
from travis.ai.providers.model_catalog import get_last_openrouter_live_catalog_error, get_live_openrouter_models
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
from travis.tui.model_loader import ModelCatalogLoader
from travis.tui.user_commands import (
    ResolvedUserCommand,
    UserCommandBinding,
    UserCommandController,
    UserCommandHandle,
)

from travis.tui.interactive_shutdown import IDLE_CTRL_C_EXIT_WINDOW_SECONDS, LATE_ABORT_GRACE_SECONDS

class InteractiveTurnController:
    """Owns a focused interactive runtime concern."""

    def _read_prompt_from_tui(self, submitted_queue: "queue.Queue[str]") -> str | None:
        while not self._shutdown_requested:
            try:
                timeout = self.tui.time_until_next_work(0.05)
                return submitted_queue.get(timeout=timeout)
            except queue.Empty:
                self.tui.drain_dispatcher()
                continue
        return None

    def _read_prompt_from_line_input(self, prompt: str = "") -> str:
        results: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                results.put(("value", self.input_fn(prompt)))
            except BaseException as error:  # noqa: BLE001 - re-raised on the UI owner thread.
                results.put(("error", error))

        threading.Thread(target=read, name="travis-line-input", daemon=True).start()
        while True:
            try:
                kind, value = results.get(timeout=self.tui.time_until_next_work(0.05))
            except queue.Empty:
                self.tui.drain_dispatcher()
                continue
            self.tui.drain_dispatcher()
            if kind == "error":
                raise value  # type: ignore[misc]
            return str(value)

    def _handle_tui_terminal_input(self, data: str):
        if data == "\x03":
            self._handle_sigint(None, None)
            return {"consume": True}
        consumed, current = self._dispatch_terminal_input(data)
        if consumed:
            return {"consume": True}
        if current != data:
            return {"data": current}
        return None

    def _handle_sigint(self, _signum, _frame) -> None:
        has_user_command = self._user_commands is not None and bool(self._user_commands.list())
        if has_user_command or self._is_turn_active() or self.app.session.is_streaming or self.app.session.is_bash_running:
            self._last_idle_ctrl_c_at = 0.0
            self._handle_editor_escape()
            return
        if self._is_recently_finished_turn():
            self._last_idle_ctrl_c_at = time.monotonic()
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
            return
        now = time.monotonic()
        if now - self._last_idle_ctrl_c_at > IDLE_CTRL_C_EXIT_WINDOW_SECONDS:
            self._last_idle_ctrl_c_at = now
            self.status.set_message("Press Ctrl-C again to exit")
            self._refresh_footer()
            self.tui.request_render()
            return
        self._shutdown_requested = True
        self.status.set_message("Exiting")
        self._refresh_footer()
        self.tui.request_render()

    def _is_recently_finished_turn(self) -> bool:
        if self._last_turn_finished_at <= 0:
            return False
        return time.monotonic() - self._last_turn_finished_at <= LATE_ABORT_GRACE_SECONDS

    def _is_turn_active(self) -> bool:
        with self._turn_lock:
            future = self._turn_future
            thread = self._turn_thread
        active = (future is not None and not future.done()) or (thread is not None and thread.is_alive())
        if not active and self.tui.dispatcher.is_owner_thread():
            self.tui.drain_dispatcher()
        return active

    def _start_turn_thread(self, prompt: str, before_compressions: int, before_tokens: int) -> None:
        future = self._command_executor().submit(
            "turn",
            lambda: self._run_turn_thread(prompt, before_compressions, before_tokens),
        )
        with self._turn_lock:
            self._turn_future = future
            self._turn_thread = None

    def _run_turn_thread(self, prompt: str, before_compressions: int, before_tokens: int) -> None:
        try:
            self.app.run_turn(
                prompt,
                on_post_response_compaction_start=self._show_post_response_compaction_status,
            )
        except Exception as error:  # noqa: BLE001 - keep the TUI responsive if a turn fails outside agent handling
            self.tui.post(
                lambda error=error: self.history.add(StatusLine(f"Turn failed: {error}", kind="error"))
            )
        finally:
            self.tui.post(lambda: self._finish_turn_thread(before_compressions, before_tokens))

    def _finish_turn_thread(self, before_compressions: int, before_tokens: int) -> None:
        self._agent_abort_requested = False
        self._render_auto_compaction_notice(before_compressions, before_tokens)
        with self._turn_lock:
            next_prompt = None if self._shutdown_requested or not self._queued_after_turn else self._queued_after_turn.pop(0)
        if next_prompt:
            next_before_compressions = self.app.compaction.compressor.compression_count
            next_before_tokens = estimate_tokens(self.app.messages)
            self.status.set_message("Running")
            self._refresh_footer()
            self.tui.request_render()
            self._start_turn_thread(next_prompt, next_before_compressions, next_before_tokens)
            return
        self._last_turn_finished_at = time.monotonic()
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _handle_active_turn_prompt(self, prompt: str) -> bool:
        if not self._is_turn_active():
            return False
        if self._dispatch_extension_command(prompt):
            self._refresh_footer()
            self.tui.request_render()
            return True
        try:
            self.app.session.steer(prompt)
        except Exception as error:  # noqa: BLE001 - queue errors should render, not crash input handling
            self.history.add(StatusLine(f"Queued input failed: {error}", kind="error"))
        self._refresh_footer()
        self.tui.request_render()
        return True

    def _handle_editor_escape(self) -> None:
        if self._user_commands is not None and self._user_commands.interrupt_focused():
            self.status.set_message("Interrupting user command")
            self._refresh_footer()
            self.tui.request_render()
            return

        if self._is_turn_active() or self.app.session.is_streaming:
            if not self._agent_abort_requested:
                self._agent_abort_requested = True
                self.status.set_message("Aborting")
                self.app.session.agent.abort()
            self._refresh_footer()
            self.tui.request_render()
            return

        if self.app.session.is_bash_running:
            self.status.set_message("Aborting bash")
            self.app.session.abort_bash()
            self._refresh_footer()
            self.tui.request_render()
            return

        if self.active_editor is not None and self.active_editor.get_value():
            self.active_editor.set_value("")
            self.editor_text = ""
            self.tui.request_render()

    def _show_post_response_compaction_status(self) -> None:
        if not self.tui.dispatcher.is_owner_thread():
            self.tui.post(self._show_post_response_compaction_status)
            return
        self.status.set_message("Compressing")
        self._refresh_footer()
        self.tui.request_render()

__all__ = (
    'InteractiveTurnController',
)
