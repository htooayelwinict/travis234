"""Focused shutdown ownership for the TUI."""

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

InputFn = Callable[[str], str]
OPENROUTER_MODEL_CACHE_TTL_SECONDS = 300
OPENROUTER_MODEL_PICKER_LIMIT = 50
LATE_ABORT_GRACE_SECONDS = 1.0
IDLE_CTRL_C_EXIT_WINDOW_SECONDS = 1.5
ACTIVE_TURN_SHUTDOWN_TIMEOUT_SECONDS = 2.0
SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS = 1.0
_SIGINT_HANDLER_UNCHANGED = object()

class InteractiveShutdown:
    """Owns a focused interactive runtime concern."""

    def _install_sigint_handler(self):
        if threading.current_thread() is not threading.main_thread():
            return _SIGINT_HANDLER_UNCHANGED
        try:
            previous = signal_module.getsignal(signal_module.SIGINT)
            signal_module.signal(signal_module.SIGINT, self._handle_sigint)
            return previous
        except (AttributeError, OSError, ValueError):
            return _SIGINT_HANDLER_UNCHANGED

    def _restore_sigint_handler(self, previous_handler) -> None:
        if previous_handler is _SIGINT_HANDLER_UNCHANGED:
            return
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            signal_module.signal(signal_module.SIGINT, previous_handler)
        except (AttributeError, OSError, ValueError):
            pass

    def _wait_for_active_turn(
        self,
        *,
        timeout_seconds: float = ACTIVE_TURN_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._turn_lock:
                future = self._turn_future
                thread = self._turn_thread
            if future is not None and not future.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    future.result(timeout=remaining)
                except FutureTimeoutError:
                    return False
                except BaseException:
                    pass
                continue
            if thread is not None and thread is not threading.current_thread() and thread.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                thread.join(timeout=remaining)
                if thread.is_alive():
                    return False
                continue
            break
        if not self._run_loop_active and self._session_commands is not None:
            self._session_commands.close(timeout=SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS)
            self._session_commands = None
        if self.tui.dispatcher.is_owner_thread():
            self.tui.drain_dispatcher()
        return True

    def _abort_active_turn_for_shutdown(self) -> None:
        if not (self._is_turn_active() or self.app.session.is_streaming):
            return
        if self._agent_abort_requested:
            return
        self._agent_abort_requested = True
        try:
            self.app.session.agent.abort()
        except BaseException:
            pass

    def _request_shutdown(self) -> None:
        self._shutdown_requested = True

__all__ = (
    'ACTIVE_TURN_SHUTDOWN_TIMEOUT_SECONDS',
    'IDLE_CTRL_C_EXIT_WINDOW_SECONDS',
    'InputFn',
    'InteractiveShutdown',
    'LATE_ABORT_GRACE_SECONDS',
    'OPENROUTER_MODEL_CACHE_TTL_SECONDS',
    'OPENROUTER_MODEL_PICKER_LIMIT',
    'SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS',
    '_SIGINT_HANDLER_UNCHANGED',
)
