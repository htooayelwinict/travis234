"""Focused session commands ownership for the TUI."""

from __future__ import annotations

import inspect
import json
import os
import queue
import shlex
import shutil
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
from travis.coding_agent.project_trust import ProjectTrustStore, get_project_trust_options
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

from travis.tui.footer_data import _footer_usage_stats
from travis.tui.interactive_extensions import _manual_compression_options

class InteractiveSessionCommands:
    """Owns a focused interactive runtime concern."""

    def _command_executor(self) -> SessionCommandExecutor:
        if self._session_commands is None:
            self._session_commands = SessionCommandExecutor(daemon=True)
        return self._session_commands

    def _run_session_command(self, name: str, callback: Callable[[], object]):
        if name != "compact":
            turn_active = bool(
                getattr(self.app.session, "is_streaming", False)
                or (callable(getattr(self, "_is_turn_active", None)) and self._is_turn_active())
            )
            if turn_active:
                raise RuntimeError("session command unavailable while turn is active")
            if self.app.session.is_compacting:
                raise RuntimeError("session command unavailable while compaction is active")
        executor = self._command_executor()
        if executor.is_owner_thread():
            return callback()
        return executor.submit(name, callback).result()

    def _startup_text(self) -> str:
        cwd = str(self.app.cwd).replace("\\", "/")
        return (
            "Travis234 TUI\n"
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

    def _run_name_command(self, prompt: str) -> None:
        name = prompt.removeprefix("/name").strip()
        if not name:
            if self.app.session.session_name:
                self.history.add(Text(f"Session name: {self.app.session.session_name}"))
            else:
                self.history.add(StatusLine("Usage: /name <name>", kind="warning"))
        else:
            try:
                self._run_session_command("name-session", lambda: self.app.rename_session(name))
                self.history.add(StatusLine(f"Session name set: {name}", kind="session"))
            except Exception as error:  # noqa: BLE001 - naming failures do not end the TUI.
                self.history.add(StatusLine(f"Could not name session: {error}", kind="error"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_fork_command(self) -> None:
        messages = self.app.session.get_user_messages_for_forking()
        if not messages:
            self.history.add(StatusLine("No messages to fork from.", kind="warning"))
            self.tui.request_render()
            return
        labels = [
            f"{item['text'].replace(chr(10), ' ')[:120]} [{item['entryId'][:8]}]"
            for item in messages
        ]
        selected = self.prompt_extension_select("Fork before user message", labels, kind="session")
        if selected is None:
            self.history.add(StatusLine("Fork cancelled.", kind="session"))
            return
        selected_message = messages[labels.index(selected)]
        try:
            result = self._run_session_command(
                "fork-session",
                lambda: self.app.fork_session(selected_message["entryId"]),
            )
            if result.get("cancelled"):
                self.history.add(StatusLine("Fork cancelled.", kind="session"))
                return
            if self.tui.dispatcher.is_owner_thread():
                self.tui.drain_dispatcher()
            self.editor_text = str(result.get("selectedText") or "")
            self.history.add(StatusLine("Forked to new session.", kind="session"))
        except Exception as error:  # noqa: BLE001 - session remains active on fork failure.
            self.history.add(StatusLine(f"Could not fork session: {error}", kind="error"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    def _run_clone_command(self) -> None:
        try:
            result = self._run_session_command("clone-session", self.app.clone_session)
            if result.get("cancelled"):
                self.history.add(StatusLine("Clone cancelled.", kind="session"))
                return
            if self.tui.dispatcher.is_owner_thread():
                self.tui.drain_dispatcher()
            self.editor_text = ""
            self.history.add(StatusLine("Cloned to new session.", kind="session"))
        except Exception as error:  # noqa: BLE001 - session remains active on clone failure.
            self.history.add(StatusLine(f"Could not clone session: {error}", kind="error"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    def _run_tree_command(self) -> None:
        tree = self.app.session_tree()
        if not tree:
            self.history.add(StatusLine("No entries in session.", kind="warning"))
            self.tui.request_render()
            return
        labels = [
            f"{'*' if node['active'] else ' '} {'  ' * int(node['depth'])}{node['summary']} "
            f"[{node['type']} {node['id'][:8]}]"
            for node in tree
        ]
        selected = self.prompt_extension_select("Session tree", labels, kind="session")
        if selected is None:
            self.history.add(StatusLine("Tree navigation cancelled.", kind="session"))
            return
        target = tree[labels.index(selected)]
        if target.get("active"):
            self.history.add(StatusLine("Already at this point.", kind="session"))
            return
        try:
            result = self._run_session_command(
                "navigate-session-tree",
                lambda: self.app.navigate_session_tree(str(target["id"])),
            )
            if result.get("cancelled"):
                self.history.add(StatusLine("Tree navigation cancelled.", kind="session"))
                return
            if result.get("editorText") is not None:
                self.editor_text = str(result["editorText"])
            self._rebind_session_ui()
            self.history.add(StatusLine("Navigated to selected point.", kind="session"))
        except Exception as error:  # noqa: BLE001 - navigation errors leave the old branch selected.
            self.history.add(StatusLine(f"Could not navigate session tree: {error}", kind="error"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    @staticmethod
    def _session_path_argument(prompt: str, command: str) -> str | None:
        raw = prompt.removeprefix(command).strip()
        if not raw:
            return None
        try:
            arguments = shlex.split(raw)
        except ValueError:
            return None
        return arguments[0] if arguments else None

    def _run_export_command(self, prompt: str) -> None:
        output_path = self._session_path_argument(prompt, "/export")
        try:
            if output_path and output_path.lower().endswith(".jsonl"):
                exported = self.app.export_session_jsonl(output_path)
            else:
                exported = self.app.session.export_to_html(output_path)
            self.history.add(StatusLine(f"Session exported to: {exported}", kind="session"))
        except Exception as error:  # noqa: BLE001 - export errors are local and recoverable.
            self.history.add(StatusLine(f"Could not export session: {error}", kind="error"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_import_command(self, prompt: str) -> None:
        input_path = self._session_path_argument(prompt, "/import")
        if not input_path:
            self.history.add(StatusLine("Usage: /import <path.jsonl>", kind="warning"))
            self.tui.request_render()
            return
        selected = self.prompt_extension_select(
            f"Replace the active session with {input_path}?",
            ["Import", "Cancel"],
            kind="session",
        )
        if selected != "Import":
            self.history.add(StatusLine("Import cancelled.", kind="session"))
            return
        try:
            result = self._run_session_command(
                "import-session",
                lambda: self.app.import_session(input_path),
            )
            if result.get("cancelled"):
                self.history.add(StatusLine("Import cancelled.", kind="session"))
                return
            if self.tui.dispatcher.is_owner_thread():
                self.tui.drain_dispatcher()
            self.history.add(StatusLine(f"Session imported from: {input_path}", kind="session"))
        except Exception as error:  # noqa: BLE001 - import validation and cwd errors remain visible.
            self.history.add(StatusLine(f"Could not import session: {error}", kind="error"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    def _run_copy_command(self) -> None:
        text = self.app.session.get_last_assistant_text()
        if not text:
            self.history.add(StatusLine("No agent message to copy yet.", kind="warning"))
            self.tui.request_render()
            return
        commands = (
            ("pbcopy",),
            ("wl-copy",),
            ("xclip", "-selection", "clipboard"),
            ("clip",),
        )
        command = next((candidate for candidate in commands if shutil.which(candidate[0])), None)
        if command is None:
            self.history.add(StatusLine("Clipboard is unavailable on this platform.", kind="warning"))
            self.tui.request_render()
            return
        try:
            subprocess.run(command, input=text, text=True, check=True, capture_output=True, timeout=3)
            self.history.add(StatusLine("Copied last agent message to clipboard.", kind="session"))
        except (OSError, subprocess.SubprocessError) as error:
            self.history.add(StatusLine(f"Could not copy message: {error}", kind="error"))
        self.tui.request_render()

    def _run_share_command(self) -> None:
        self.history.add(
            StatusLine(
                "Remote sharing is not configured; use /export to create a local shareable file.",
                kind="warning",
            )
        )
        self.tui.request_render()

    def _run_theme_command(self, prompt: str) -> None:
        requested = prompt.removeprefix("/theme").strip()
        themes = list(self.theme_registry.list())
        if not requested:
            if not themes:
                self.history.add(StatusLine("No discovered themes are available.", kind="warning"))
                self.tui.request_render()
                return
            selected = self.prompt_extension_select(
                "Theme",
                [theme.name for theme in themes],
                kind="theme",
            )
            if selected is None:
                self.history.add(StatusLine("Theme unchanged.", kind="session"))
                return
            requested = selected
        try:
            self.theme_registry.select(requested)
            self.app.session.settings_manager.set_theme(requested)
            self.history.add(StatusLine(f"Theme selected: {requested}", kind="session"))
        except ValueError as error:
            self.history.add(StatusLine(str(error), kind="error"))
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
            "/compact deep [focus] - Create an aggressive bounded generational checkpoint.",
            "/resume - Switch to a previous session.",
            "/new - Start a new persistent session.",
            "/session - Show active session details.",
            "/name <name> - Name the active session.",
            "/fork - Fork before a selected user message.",
            "/clone - Clone the complete active branch.",
            "/tree - Navigate the active session tree.",
            "/export [path] - Export HTML or JSONL.",
            "/import <path.jsonl> - Import and switch session.",
            "/copy - Copy the last agent message when a clipboard adapter is available.",
            "/share - Report configured sharing support.",
            "/theme [name] - Select a discovered theme.",
            "/trust - View or change the project trust decision.",
            "/processes - Inspect and control managed processes.",
            "/reload - Reload extensions, skills, prompts, and themes.",
            "/install <source> [--local] - Install a resource package.",
            "/remove <source> [--local] - Remove a resource package.",
            "/update [source] [--local] - Update resource packages.",
            "/packages [--local] - List installed resource packages.",
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

    def _run_trust_command(self) -> None:
        cwd = Path(self.app.cwd).expanduser().resolve()
        loader = self.app.session.resource_loader
        store = ProjectTrustStore(loader.agent_dir)
        try:
            entry = store.get_entry(cwd)
        except Exception as error:  # noqa: BLE001 - trust-store errors render and fail closed.
            self.history.add(StatusLine(f"Could not read project trust: {error}", kind="error"))
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
            return

        active = "trusted" if loader.project_trusted else "untrusted"
        if entry is None:
            saved_status = "No saved decision"
        elif Path(entry.path) == cwd:
            saved_status = f"Saved for this folder: {'trusted' if entry.decision else 'untrusted'}"
        else:
            saved_status = (
                f"Inherited from {entry.path}: "
                f"{'trusted' if entry.decision else 'untrusted'}"
            )
        options = get_project_trust_options(cwd, include_session_only=True)
        selected = self.prompt_extension_select(
            f"Project trust ({active})\n{saved_status}",
            [option.label for option in options],
        )
        choice = next((option for option in options if option.label == selected), None)
        if choice is None:
            self.history.add(StatusLine("Project trust unchanged.", kind="warning"))
        else:
            try:
                if choice.updates:
                    store.set_many(choice.updates)
                    self.app._project_trust_override = None  # noqa: SLF001 - session trust control.
                    loader._project_trust_override = None  # noqa: SLF001 - applied on explicit reload.
                else:
                    self.app._project_trust_override = choice.trusted  # noqa: SLF001
                    loader._project_trust_override = choice.trusted  # noqa: SLF001
            except Exception as error:  # noqa: BLE001 - trust-store errors render and fail closed.
                self.history.add(StatusLine(f"Could not update project trust: {error}", kind="error"))
            else:
                decision = "trusted" if choice.trusted else "untrusted"
                self.history.add(StatusLine(f"Project marked {decision}.", kind="success"))
                self.history.add(Text("Run /reload or restart before the new trust decision takes effect."))
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
