"""Focused bash ownership for coding sessions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional

from travis.agent.agent import Agent
from travis.agent.types import AbortSignal
from travis.agent.types import AfterToolCallResult
from travis.agent.types import AgentContext
from travis.agent.types import AgentLoopTurnUpdate
from travis.agent.types import AgentTool
from travis.agent.types import AgentToolResult
from travis.agent.types import AgentMessage
from travis.agent.types import BeforeToolCallResult
from travis.agent.types import MessageEndEvent, MessageStartEvent
from travis.ai.model_resolver import ScopedModel
from travis.ai.models import (
    clamp_thinking_level,
    get_supported_thinking_levels,
)
from travis.ai.types import AssistantMessage, Cost, ImageContent, Message, Model, TextContent, UserMessage, now_ms
from travis.ai.types import ToolCall, ToolResultMessage, Usage
from travis.compaction.compressor import LEGACY_SUMMARY_PREFIX, SUMMARY_END_MARKER, SUMMARY_PREFIX, estimate_tokens
from travis.compaction.timing import CompactionManager
from travis.coding_agent.branch_summarization import generate_branch_summary
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.compaction_adapter import (
    SessionCompactionAdapter,
    compaction_summary_with_details,
)
from travis.coding_agent.compaction_coordinator import (
    CompactionCoordinator,
    CompactionTransactionCoordinator,
)
from travis.coding_agent.config import get_packaged_context_paths
from travis.coding_agent.extensions import ExtensionRunner, emit_session_shutdown_event
from travis.coding_agent.execution_backend import select_execution_backend
from travis.coding_agent.mailbox import CodingTurnMailbox, MailboxKind
from travis.coding_agent.message_utils import (
    bash_execution_text as _bash_execution_to_text,
    last_assistant_message as _last_assistant_message,
    user_message_text as _text_from_user_message_content,
)
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.process_context import ProcessContextResolver
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_index import SessionIndex
from travis.coding_agent.session_store import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CustomMessage,
    SessionStore,
    deserialize_message,
)
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from travis.coding_agent.subagents import (
    CallableSubagentBackend,
    CodexExecBackend,
    SubagentResult,
    SubagentSupervisor,
    SubagentTask,
)
from travis.coding_agent.tools import create_all_tool_definitions
from travis.coding_agent.tools.bash import BashExecOptions, BashOperations, create_local_bash_operations, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.coding_agent.tools.process import PROCESS_ACTIONS, create_process_tool_definition, prepare_process_arguments
from travis.coding_agent.tools.types import (
    ToolContext,
    ToolDefinition,
    create_tool_definition_from_agent_tool,
    wrap_tool_definition,
)

from travis.coding_agent.session_types import BashResult

class SessionBashController:
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
        self._bash_signal = AbortSignal()

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
                BashExecOptions(on_data=handle_data, signal=self._bash_signal),
            )
            exit_code = result.get("exit_code")
        except RuntimeError as error:
            cancelled = str(error) == "aborted"
            if not cancelled:
                raise
        finally:
            output.finish()
            self._bash_signal = None
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
        if self._bash_signal is not None:
            self._bash_signal.abort()

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
