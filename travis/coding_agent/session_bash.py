"""Focused bash ownership for coding sessions."""

from __future__ import annotations

import time

from travis.agent.types import (
    AbortSignal,
)
from travis.coding_agent.session_ports import SessionControllerPort, SessionPortBoundController
from travis.coding_agent.session_store import (
    BashExecutionMessage,
)
from travis.coding_agent.session_types import BashResult
from travis.coding_agent.tools.bash import BashExecOptions, BashOperations, create_local_bash_operations
from travis.coding_agent.tools.output_spool import OutputSpool


class SessionBashController(SessionPortBoundController[SessionControllerPort]):
    """Owns a focused AgentSession runtime concern."""

    def execute_bash(self, command: str, on_chunk=None, options: dict | None = None) -> BashResult:
        options = options or {}
        command_prefix = options.get("commandPrefix")
        if command_prefix is None:
            command_prefix = options.get("command_prefix")
        if command_prefix is None:
            command_prefix = self._settings_shell_command_prefix()
        shell_path = options.get("shellPath")
        if shell_path is None:
            shell_path = options.get("shell_path")
        if shell_path is None:
            shell_path = self._settings_shell_path()
        operations: BashOperations = options.get("operations") or create_local_bash_operations(shell_path=shell_path)
        resolved_command = f"{command_prefix}\n{command}" if command_prefix else command
        output = OutputSpool(
            temp_file_prefix="travis-user-bash",
            artifact_registry=self._artifacts,
            artifact_kind="user-bash-output",
        )
        signal = AbortSignal()
        with self._bash_signals_lock:
            self._bash_signals.add(signal)

        def handle_data(data: bytes) -> None:
            output.append(data)
            if on_chunk:
                on_chunk(data.decode("utf-8", errors="replace"))

        exit_code: int | None = None
        cancelled = False
        try:
            result = operations.exec(
                resolved_command,
                self.cwd,
                BashExecOptions(on_data=handle_data, signal=signal),
            )
            exit_code = result.get("exit_code")
        except RuntimeError as error:
            cancelled = str(error) == "aborted"
            if not cancelled:
                raise
        finally:
            output.finish()
            with self._bash_signals_lock:
                self._bash_signals.discard(signal)
        snapshot = output.snapshot(persist_if_truncated=True)
        output.close()
        bash_result = BashResult(
            output=snapshot.content,
            exit_code=exit_code,
            cancelled=cancelled,
            truncated=bool(snapshot.truncation.truncated),
            full_output_path=snapshot.full_output_path,
        )
        self.record_bash_result(command, bash_result, options)
        return bash_result

    def abort_bash(self) -> None:
        with self._bash_signals_lock:
            signals = tuple(self._bash_signals)
        for signal in signals:
            signal.abort()

    def record_bash_result(self, command: str, result: BashResult, options: dict | None = None) -> None:
        options = options or {}
        message = BashExecutionMessage(
            command=command,
            output=result.output,
            exit_code=result.exit_code,
            cancelled=result.cancelled,
            truncated=result.truncated,
            full_output_path=result.full_output_path,
            timestamp=int(time.time() * 1000),
            exclude_from_context=options.get("excludeFromContext", options.get("exclude_from_context")),
        )
        if self.is_streaming:
            self._pending_bash_messages.append(message)
            return
        self._append_bash_message(message)

    def _append_bash_message(self, message: BashExecutionMessage) -> None:
        self.agent.state.messages.append(message)
        if self._session_store:
            self._session_store.append_message(message)

    def _flush_pending_bash_messages(self) -> None:
        if not self._pending_bash_messages:
            return
        for message in self._pending_bash_messages:
            self._append_bash_message(message)
        self._pending_bash_messages = []

__all__ = (
    'SessionBashController',
)
