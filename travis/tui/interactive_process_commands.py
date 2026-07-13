"""Focused process commands ownership for the TUI."""

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

from travis.tui.interactive_view import _short_status_text

class InteractiveProcessCommands:
    """Owns a focused interactive runtime concern."""

    def _run_processes_command(self) -> None:
        service = getattr(self.app, "process_service", None)
        owner_factory = getattr(self.app, "process_owner", None)
        if service is None or not callable(owner_factory):
            self.history.add(StatusLine("Managed process service is unavailable.", kind="error"))
            self.tui.request_render()
            return
        rows: list[tuple[object, ProcessSnapshot]] = []
        seen_process_ids: set[str] = set()
        for owner in (owner_factory(origin="agent"), owner_factory(origin="user")):
            for snapshot in service.list(owner):
                if snapshot.session_id in seen_process_ids:
                    continue
                seen_process_ids.add(snapshot.session_id)
                rows.append((owner, snapshot))
        controller_rows = [
            inspection
            for inspection in (self._user_commands.list() if self._user_commands is not None else ())
            if inspection.process_id is None or inspection.process_id not in seen_process_ids
        ]
        if not rows and not controller_rows:
            self.history.add(StatusLine("No managed processes for this workspace.", kind="process"))
            self.tui.request_render()
            return
        labels = [self._process_label(snapshot, owner.origin) for owner, snapshot in rows]
        labels.extend(
            f"user | {inspection.handle.command_id[:13]} | starting | custom | "
            f"{_short_status_text(inspection.handle.command, limit=80)}"
            for inspection in controller_rows
        )
        selected = self.prompt_extension_select("Managed processes", labels, kind="process")
        if selected is None:
            return
        selected_index = labels.index(selected)
        if selected_index >= len(rows):
            inspection = controller_rows[selected_index - len(rows)]
            action = self.prompt_extension_select(
                "Process action",
                ["Interrupt"],
                kind="process",
            )
            if action == "Interrupt":
                assert self._user_commands is not None
                self._user_commands.interrupt(inspection.handle.command_id)
                self.history.add(
                    StatusLine(
                        f"Interrupting user command {inspection.handle.command_id}",
                        kind="process",
                    )
                )
                self.tui.request_render()
            return
        owner, snapshot = rows[selected_index]
        actions = self._process_actions(snapshot.state)
        action = self.prompt_extension_select("Process action", actions, kind="process")
        if action is None:
            return
        try:
            if action == "Refresh":
                cursor = self._process_cursors.get(snapshot.session_id, 0)
                snapshot = service.poll(owner, snapshot.session_id, cursor, wait_ms=0, max_bytes=8192)
                self._process_cursors[snapshot.session_id] = snapshot.next_cursor
            elif action == "Interrupt":
                snapshot = service.interrupt(owner, snapshot.session_id, wait_ms=0)
            elif action == "Terminate":
                snapshot = service.terminate(owner, snapshot.session_id, wait_ms=250)
            else:
                snapshot = service.kill(owner, snapshot.session_id)
            self._render_process_snapshot(snapshot)
        except Exception as error:  # noqa: BLE001 - user controls render failures without leaving the TUI.
            self.history.add(StatusLine(f"Process action failed: {error}", kind="error"))
        finally:
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

    @staticmethod
    def _process_actions(state: ProcessState) -> list[str]:
        if state is ProcessState.RUNNING:
            return ["Refresh", "Interrupt", "Terminate", "Kill"]
        if state is ProcessState.STOPPING:
            return ["Refresh", "Kill"]
        return ["Refresh"]

    @staticmethod
    def _process_label(snapshot: ProcessSnapshot, origin: str = "agent") -> str:
        mode = "tty" if snapshot.tty else "pipe"
        elapsed = max(0, snapshot.elapsed_ms // 1000)
        command = _short_status_text(snapshot.command, limit=80)
        return f"{origin} | {snapshot.session_id[:13]} | {snapshot.state.value} | {elapsed}s | {mode} | {command}"

    def _render_process_snapshot(self, snapshot: ProcessSnapshot) -> None:
        if snapshot.output:
            self.history.add(Text(snapshot.output))
        exit_text = f" ({snapshot.exit_code})" if snapshot.exit_code is not None else ""
        self.history.add(
            StatusLine(
                f"Process {snapshot.session_id} {snapshot.state.value}{exit_text}",
                kind="process",
            )
        )

    def _handle_process_event(self, event: ProcessEvent) -> None:
        if self._shutdown_requested or not event.state.terminal or event.session_id in self._notified_processes:
            return
        owner_factory = getattr(self.app, "process_owner", None)
        if not callable(owner_factory) or event.owner not in {
            owner_factory(origin="agent"),
            owner_factory(origin="user"),
        }:
            return
        if self.app.event_trace is not None:
            self.app.event_trace.write(
                "process_event",
                {
                    "process_id": event.session_id,
                    "process_state": event.state.value,
                    "origin": event.owner.origin,
                    "status": "ok" if event.state.value == "exited" else event.state.value,
                },
            )
        self._notified_processes.add(event.session_id)
        exit_text = f" ({event.exit_code})" if event.exit_code is not None else ""
        self.history.add(
            StatusLine(
                f"Process {event.session_id} {event.state.value}{exit_text}",
                kind="process",
            )
        )
        self.tui.request_render()

    def _rebind_session_ui(self) -> None:
        if self._unsubscribe_session_events is not None:
            self._unsubscribe_session_events()
            self._unsubscribe_session_events = None
        self.history.clear()
        self._history_populated = False
        self.app.renderer.set_output_container(self.history)
        self.app.renderer.set_hidden_thinking_label(self.hidden_thinking_label)
        self.app.renderer.set_hide_thinking_block(self.hide_thinking_block)
        self._populate_existing_history()
        self.built_in_header.set_text(self._startup_text())
        self.footer.cwd = str(self.app.cwd)
        self.setup_autocomplete_provider()
        if self._initialized:
            self._unsubscribe_session_events = self.app.session.subscribe(
                lambda event: self.tui.post(lambda: self._handle_session_event(event))
            )
        self._refresh_footer()
        self.tui.scroll_to_bottom()
        self.tui.request_render(force=True)

    def _run_bash_command(self, command: str, *, exclude_from_context: bool) -> None:
        component = BashExecutionComponent(command, exclude_from_context=exclude_from_context)
        self.history.add(component)
        self.status.set_message("Running bash")
        self._refresh_footer()
        self.tui.request_render()
        try:
            if self._user_commands is None:
                raise RuntimeError("User command controller is unavailable")
            binding = UserCommandBinding(
                session=self.app.session,
                session_id=self.app.session.session_id or None,
                session_path=self.app.session.session_path,
                exclude_from_context=exclude_from_context,
            )
            handle = self._user_commands.start(command, binding)
            self._user_command_components[handle.command_id] = component
            self._user_command_order.append(handle.command_id)
            if self.app.event_trace is not None:
                self.app.event_trace.write("user_command_started", {"status": "ok"})
        except Exception as error:  # noqa: BLE001 - user bash errors are rendered in the TUI
            component.set_complete(None, False)
            self.history.add(StatusLine(f"Bash command failed: {error}", kind="error"))

    def _resolve_user_command(
        self,
        command: str,
        binding: UserCommandBinding,
        signal,
    ) -> ResolvedUserCommand:
        session = binding.session
        extension_result = session.extension_runner.emit_user_bash(
            {
                "type": "user_bash",
                "command": command,
                "excludeFromContext": binding.exclude_from_context,
                "cwd": str(session.cwd),
            }
        )
        if isinstance(extension_result, dict) and extension_result.get("result") is not None:
            return ResolvedUserCommand.immediate(extension_result["result"])

        command_prefix = None
        shell_path = None
        operations = None
        if isinstance(extension_result, dict):
            operations = extension_result.get("operations")
            command_prefix = extension_result.get("commandPrefix", extension_result.get("command_prefix"))
            shell_path = extension_result.get("shellPath", extension_result.get("shell_path"))
        if operations is not None:
            return ResolvedUserCommand.custom(
                lambda abort, on_output: self._run_custom_user_command(
                    command,
                    binding,
                    operations,
                    command_prefix,
                    abort,
                    on_output,
                )
            )
        return ResolvedUserCommand.managed(
            self.app.user_command_request(
                command,
                session=session,
                command_prefix=command_prefix,
                shell_path=shell_path,
            )
        )

    @staticmethod
    def _run_custom_user_command(
        command: str,
        binding: UserCommandBinding,
        operations,
        command_prefix: str | None,
        signal,
        on_output: Callable[[str], None],
    ) -> BashResult:
        resolved_command = f"{command_prefix}\n{command}" if command_prefix else command
        output = OutputSpool(
            temp_file_prefix="travis-user-bash",
            artifact_registry=binding.session._artifacts,
            artifact_kind="user-bash-output",
        )
        exit_code = None
        cancelled = False

        def on_data(data: bytes) -> None:
            output.append(data)
            on_output(data.decode("utf-8", errors="replace"))

        try:
            result = operations.exec(
                resolved_command,
                binding.session.cwd,
                BashExecOptions(on_data=on_data, signal=signal, env=get_shell_env()),
            )
            exit_code = result.get("exit_code")
        except RuntimeError as error:
            cancelled = str(error) == "aborted" or signal.aborted
            if not cancelled:
                raise
        finally:
            output.finish()
        snapshot = output.snapshot(persist_if_truncated=True)
        output.close()
        return BashResult(
            output=snapshot.content,
            exit_code=exit_code,
            cancelled=cancelled,
            truncated=snapshot.truncation.truncated,
            full_output_path=snapshot.full_output_path,
        )

    def _append_user_command_output(self, command_id: str, text: str) -> None:
        component = self._user_command_components.get(command_id)
        if component is not None:
            component.append_output(text)
            self.tui.request_render()

    def _finish_user_command(self, handle: UserCommandHandle, result: BashResult) -> None:
        component = self._user_command_components.pop(handle.command_id, None)
        if component is not None:
            component.set_complete(
                result.exit_code,
                result.cancelled,
                result.truncated,
                result.full_output_path,
            )
        self._completed_user_commands[handle.command_id] = (handle, result)
        self._flush_completed_user_command_records()
        has_user_commands = self._user_commands is not None and bool(self._user_commands.list())
        self.status.set_message("Running bash" if has_user_commands else "Running" if self._is_turn_active() else "Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _fail_user_command(self, command_id: str, message: str) -> None:
        component = self._user_command_components.pop(command_id, None)
        if component is not None:
            component.set_complete(None, False)
        self.history.add(StatusLine(f"Bash command failed: {message}", kind="error"))
        self._completed_user_commands[command_id] = None
        self._flush_completed_user_command_records()
        has_user_commands = self._user_commands is not None and bool(self._user_commands.list())
        self.status.set_message("Running bash" if has_user_commands else "Running" if self._is_turn_active() else "Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _flush_completed_user_command_records(self) -> None:
        while self._user_command_order:
            command_id = self._user_command_order[0]
            if command_id not in self._completed_user_commands:
                return
            self._user_command_order.pop(0)
            completed = self._completed_user_commands.pop(command_id)
            if completed is None:
                continue
            handle, result = completed
            self._command_executor().submit(
                "record-user-bash",
                lambda handle=handle, result=result: handle.binding.session.record_bash_result(
                    handle.command,
                    result,
                    {"excludeFromContext": handle.binding.exclude_from_context},
                ),
            )

__all__ = (
    'InteractiveProcessCommands',
)
