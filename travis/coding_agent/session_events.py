"""Focused events ownership for coding sessions."""

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
from travis.coding_agent.policies.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolGuardrailDecision,
    ToolLoopPolicy,
    append_toolguard_guidance,
    classify_tool_failure,
    toolguard_synthetic_result,
)
from travis.coding_agent.policies.iteration_limit import coding_iteration_limit_message
from travis.coding_agent.policies.package_consent import PackageMutationPolicy
from travis.coding_agent.policies.pipeline import PolicyPipeline
from travis.coding_agent.policies.types import (
    Allow,
    Block,
    CodingPolicyEvent,
    CodingTurnContext,
    RequireConsent,
    ToolCallView,
    TurnCapabilities,
)
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
from travis.coding_agent.capabilities import CapabilityViolation, WorkspaceCapability
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
from travis.coding_agent.provider_control_plane import ProviderControlPlane
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

from travis.coding_agent.session_extensions import _replace_message_in_place
from travis.coding_agent.session_types import QueueUpdateEvent, _append_toolguard_content, _tool_result_text, _with_toolguard_details

def _canonicalize_process_tool_calls(message: AssistantMessage) -> None:
    for block in message.content:
        if not isinstance(block, ToolCall):
            continue
        if block.name.startswith("process."):
            action = block.name.removeprefix("process.")
            existing_action = block.arguments.get("action")
            if action not in PROCESS_ACTIONS or existing_action not in (None, action):
                continue
            block.name = "process"
            block.arguments["action"] = action
        if block.name != "process":
            continue
        try:
            prepare_process_arguments(block.arguments)
        except ValueError:
            # Invalid calls remain intact so normal tool validation can report the exact model output.
            continue

class SessionEventController:
    """Owns a focused AgentSession runtime concern."""

    def _emit_session_start_event(self) -> None:
        self._extension_runner.emit(self._session_start_event)
        reason = "reload" if self._session_start_event.get("reason") == "reload" else "startup"
        if self._extend_resources_from_extensions(reason):
            self.set_active_tools_by_name(self.get_active_tool_names())

    def emit_deferred_session_start(self) -> None:
        if not self._defer_session_start:
            return
        self._defer_session_start = False
        self._emit_session_start_event()

    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]:
        self._event_listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return _unsubscribe

    def grant_capability(self, name: str, uses: int = 1) -> None:
        self._turn_capabilities.grant(name, uses)

    def _handle_agent_event(self, event) -> None:
        if event.type == "turn_start":
            self._bash_signatures_this_assistant_turn.clear()
        if event.type == "tool_execution_end" and getattr(event, "reason_code", None):
            self._observe_nonexecuted_tool_outcome(event)
        if event.type == "message_end" and self._extension_runner.has_handlers("message_end"):
            replacement = self._extension_runner.emit_message_end({"type": "message_end", "message": event.message})
            if replacement is not None:
                _replace_message_in_place(event.message, replacement)
        if event.type == "message_end" and isinstance(event.message, AssistantMessage):
            _canonicalize_process_tool_calls(event.message)
        if event.type == "message_start" and getattr(event.message, "role", None) == "user":
            queue_id = getattr(event.message, "_coding_queue_id", None)
            if isinstance(queue_id, str) and self._turn_mailbox.acknowledge(queue_id):
                self._emit_queue_update()
        if event.type == "agent_end":
            self._restore_unacknowledged_turn_messages()
            setattr(event, "will_retry", self._will_retry_after_agent_end(event))
            setattr(event, "willRetry", getattr(event, "will_retry"))
        if event.type == "message_end" and self._session_store:
            message_role = getattr(event.message, "role", None)
            if message_role == "custom":
                self._session_store.append_custom_message_entry(
                    event.message.custom_type,
                    event.message.content,
                    event.message.display,
                    event.message.details,
                )
            elif message_role in ("user", "assistant", "toolResult"):
                self._session_store.append_message(event.message)
        self._emit(event)

    def _observe_nonexecuted_tool_outcome(self, event) -> None:
        if self._tool_guardrail_halt_decision is not None:
            return
        result_text = _tool_result_text(event.result.content)
        decision = self._tool_guardrails.after_call(
            event.tool_name,
            event.args if isinstance(event.args, Mapping) else {},
            result_text,
            failed=True,
        )
        if decision.action == "halt":
            event.result.content = _append_toolguard_content(event.result.content, decision)
        elif decision.action == "warn":
            event.result.content = _append_toolguard_content(event.result.content, decision)
            event.result.details = _with_toolguard_details(event.result.details, decision)
        if decision.should_halt:
            self._tool_guardrail_halt_decision = decision
            event.is_error = True

    def _will_retry_after_agent_end(self, event) -> bool:
        if not self._retry_enabled or self._retry_attempt >= self._max_retries:
            return False
        for message in reversed(event.messages):
            if isinstance(message, AssistantMessage):
                return message.stop_reason == "error" and self._is_retryable_error(message)
        return False

    def _emit_queue_update(self) -> None:
        self._emit(
            QueueUpdateEvent(
                steering=self.get_steering_messages(),
                follow_up=self.get_follow_up_messages(),
            )
        )

    def _emit(self, event) -> None:
        for listener in list(self._event_listeners):
            listener(event)

__all__ = (
    'SessionEventController',
    '_canonicalize_process_tool_calls',
)
