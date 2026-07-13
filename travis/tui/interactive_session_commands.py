"""Focused session commands ownership for the TUI."""

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

from travis.tui.footer_data import _footer_usage_stats
from travis.tui.interactive_extensions import _manual_compression_options

class InteractiveSessionCommands:
    """Owns a focused interactive runtime concern."""

    def _command_executor(self) -> SessionCommandExecutor:
        if self._session_commands is None:
            self._session_commands = SessionCommandExecutor(daemon=True)
        return self._session_commands

    def _run_session_command(self, name: str, callback: Callable[[], object]):
        executor = self._command_executor()
        if executor.is_owner_thread():
            return callback()
        return executor.submit(name, callback).result()

    def _startup_text(self) -> str:
        cwd = str(self.app.cwd).replace("\\", "/")
        return (
            "travis travis+travis TUI\n"
            "Current working directory: "
            f"{cwd}\n"
            "Type /exit or /quit to leave."
        )

    def _session_candidates(self) -> list[SessionInfo]:
        catalog = self.app.session_catalog
        current_path = self.app.session.session_path
        active = Path(current_path).expanduser().resolve() if current_path else None
        ordered = [*catalog.list_for_cwd(str(self.app.cwd)), *catalog.list_all()]
        seen: set[Path] = set()
        candidates: list[SessionInfo] = []
        for info in ordered:
            path = info.path.resolve()
            if path == active or path in seen:
                continue
            seen.add(path)
            candidates.append(info)
        return candidates

    @staticmethod
    def _session_label(info: SessionInfo) -> str:
        title = info.name or info.preview or "(empty session)"
        model = f" | {info.model}" if info.model else ""
        return f"{title} | {info.cwd} | {info.session_id[:8]}{model} | {info.path.name}"

    def _run_resume_command(self, *, startup: bool = False) -> bool:
        candidates = self._session_candidates()
        if not candidates:
            self.history.add(StatusLine("No previous sessions available.", kind="warning"))
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
            return False
        labels = [self._session_label(info) for info in candidates]
        selected = self.prompt_extension_select("Resume session", labels, kind="session")
        if selected is None:
            if not startup:
                self.status.set_message("Idle")
                self._refresh_footer()
            return False
        info = candidates[labels.index(selected)]
        self.status.set_message("Switching session")
        self._refresh_footer()
        self.tui.request_render()
        try:
            result = self._run_session_command(
                "resume",
                lambda: self.app.switch_session(str(info.path)),
            )
            if result.get("cancelled"):
                self.history.add(StatusLine("Session switch cancelled.", kind="session"))
                return False
            if self.tui.dispatcher.is_owner_thread():
                self.tui.drain_dispatcher()
            self.history.add(StatusLine(f"Resumed session: {self.app.session.session_id}", kind="session"))
            return True
        except Exception as error:  # noqa: BLE001 - selection failures remain visible without losing current state.
            self.history.add(StatusLine(f"Session switch failed: {error}", kind="error"))
            return False
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    def _run_new_session_command(self) -> None:
        self.status.set_message("Starting new session")
        self._refresh_footer()
        self.tui.request_render()
        try:
            result = self._run_session_command("new-session", self.app.new_session)
            if result.get("cancelled"):
                self.history.add(StatusLine("New session cancelled.", kind="session"))
                return
            if self.tui.dispatcher.is_owner_thread():
                self.tui.drain_dispatcher()
            self.history.add(StatusLine(f"Started new session: {self.app.session.session_id}", kind="session"))
        except Exception as error:  # noqa: BLE001 - command errors are rendered and the old session remains active.
            self.history.add(StatusLine(f"Could not start session: {error}", kind="error"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    def _run_session_info_command(self) -> None:
        session = self.app.session
        session_file = session.session_path or "ephemeral"
        session_id = session.session_id or "ephemeral"
        usage = _footer_usage_stats(session.messages)
        self.history.add(StatusLine("Session", kind="session"))
        for line in (
            f"File: {session_file}",
            f"ID: {session_id}",
            f"Messages: {len(session.messages)}",
            f"Context: ~{estimate_tokens(session.messages):,} tokens",
            f"Usage: {usage['input']:,} input / {usage['output']:,} output tokens",
            f"Model: {session.model.provider}/{session.model.id}",
            f"Thinking: {session.thinking_level}",
        ):
            self.history.add(Text(line))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_help_command(self) -> None:
        self.history.add(StatusLine("TUI commands", kind="help"))
        for line in (
            "/help - Show this help.",
            "/model - Switch model.",
            "/models - List available models.",
            "/params - Show active provider generation parameters.",
            "/login - Configure provider authentication.",
            "/logout - Remove provider authentication.",
            "/compact or /compress - Safely compress conversation context.",
            "/compact deep [focus] - Run bounded multi-pass compaction toward a fresh-session baseline.",
            "/resume - Switch to a previous session.",
            "/new - Start a new persistent session.",
            "/session - Show active session details.",
            "/processes - Inspect and control managed processes.",
            "/allow package-install [uses] - Allow explicit package installation for this session.",
            "/agents - List delegated subagents.",
            "/delegate <role> <task> - Spawn a subagent for explicit multi-agent work.",
            "/cancel-agent <task-id> [reason] - Cancel a delegated subagent.",
            "/exit or /quit - Exit the interactive session.",
            "!<command> - Run a shell command outside model context.",
        ):
            self.history.add(Text(line))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_unknown_command(self, prompt: str) -> None:
        command_name = prompt[1:].partition(" ")[0]
        self.history.add(
            StatusLine(
                f"Unknown command: /{command_name}. Type /help for available commands.",
                kind="error",
            )
        )
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_manual_compress(self, prompt: str) -> None:
        focus, deep = _manual_compression_options(prompt)
        self.status.set_message("Compressing")
        self._refresh_footer()
        self.tui.request_render()

        try:
            status = self._run_session_command(
                "compact",
                lambda: self.app.session.compact(
                    focus=focus,
                    deep=deep,
                ),
            )
            self.history.add(StatusLine(status.headline, kind="compact"))
            self.history.add(Text(status.token_line))
            if status.note:
                self.history.add(StatusLine(status.note, kind="note"))
            if status.warning:
                self.history.add(StatusLine(status.warning, kind="warning"))
            if status.info:
                self.history.add(StatusLine(status.info, kind="info"))
        except Exception as error:  # noqa: BLE001 - keep the command boundary stable: report local command failure without trapping TUI.
            self.history.add(StatusLine(f"Compression failed: {error}", kind="compact"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

__all__ = (
    'InteractiveSessionCommands',
)
