"""Focused models ownership for coding sessions."""

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

from travis.coding_agent.session_types import ModelCycleResult, SessionInfoChangedEvent, ThinkingLevelChangedEvent, _THINKING_LEVELS

class SessionModelController:
    """Owns a focused AgentSession runtime concern."""

    @property
    def pending_message_count(self) -> int:
        return len(self._turn_mailbox.snapshot("steering")) + len(
            self._turn_mailbox.snapshot("follow_up")
        )

    @property
    def has_pending_bash_messages(self) -> bool:
        return bool(self._pending_bash_messages)

    @property
    def is_bash_running(self) -> bool:
        return self._bash_signal is not None

    def get_steering_messages(self) -> list[str]:
        return [item.text for item in self._turn_mailbox.snapshot("steering")]

    def get_follow_up_messages(self) -> list[str]:
        return [item.text for item in self._turn_mailbox.snapshot("follow_up")]

    @property
    def is_streaming(self) -> bool:
        return self.agent.state.is_streaming

    @property
    def state(self):
        return self.agent.state

    @property
    def model(self) -> Model:
        return self.agent.state.model

    @property
    def thinking_level(self) -> str:
        return self.agent.state.thinking_level

    @property
    def scoped_models(self) -> list[ScopedModel]:
        return list(self._scoped_models)

    @property
    def retry_attempt(self) -> int:
        return self._retry_attempt

    @property
    def is_retrying(self) -> bool:
        return self._retry_signal is not None

    @property
    def auto_retry_enabled(self) -> bool:
        return self._retry_enabled

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        self._retry_enabled = bool(enabled)

    def abort_retry(self) -> None:
        if self._retry_signal is not None:
            self._retry_signal.abort()

    @property
    def session_name(self) -> str | None:
        return self._session_name

    @property
    def extension_runner(self) -> ExtensionRunner:
        return self._extension_runner

    @property
    def resource_loader(self) -> DefaultResourceLoader:
        return self._resource_loader

    @property
    def prompt_templates(self) -> list[object]:
        return self._resource_loader.get_prompts()["prompts"]

    def has_extension_handlers(self, event_type: str) -> bool:
        return self._extension_runner.has_handlers(event_type)

    @property
    def messages(self) -> list[AgentMessage]:
        return self.agent.state.messages

    @property
    def steering_mode(self) -> str:
        return self.agent.steering_mode

    @property
    def follow_up_mode(self) -> str:
        return self.agent.follow_up_mode

    def set_steering_mode(self, mode: str) -> None:
        self.agent.steering_mode = mode

    def set_follow_up_mode(self, mode: str) -> None:
        self.agent.follow_up_mode = mode

    def set_session_name(self, name: str | None) -> None:
        self._session_name = name
        if self._session_store:
            self._session_store.append_session_info(name)
        self._emit(SessionInfoChangedEvent(name=name))

    def set_thinking_level(self, level: str) -> None:
        available_levels = self.get_available_thinking_levels()
        effective_level = level if level in available_levels else self._clamp_thinking_level(level, available_levels)
        previous = self.agent.state.thinking_level
        self.agent.state.thinking_level = effective_level
        if effective_level != previous:
            if self._session_store:
                self._session_store.append_thinking_level_change(effective_level)
            self._emit(ThinkingLevelChangedEvent(level=effective_level))

    def cycle_thinking_level(self) -> str | None:
        if not self.supports_thinking():
            return None
        levels = self.get_available_thinking_levels()
        if not levels:
            return None
        try:
            current_index = levels.index(self.thinking_level)
        except ValueError:
            current_index = -1
        next_level = levels[(current_index + 1) % len(levels)]
        self.set_thinking_level(next_level)
        return next_level

    def get_available_thinking_levels(self) -> list[str]:
        if not self.model:
            return list(_THINKING_LEVELS)
        return get_supported_thinking_levels(self.model)

    def supports_thinking(self) -> bool:
        return bool(self.model and self.model.reasoning)

    def _get_thinking_level_for_model_switch(self, explicit_level: str | None = None) -> str:
        if explicit_level is not None:
            return explicit_level
        if not self.supports_thinking():
            get_default_thinking_level = getattr(
                self.settings_manager,
                "get_default_thinking_level",
                None,
            ) or getattr(self.settings_manager, "getDefaultThinkingLevel", None)
            if callable(get_default_thinking_level):
                default_level = get_default_thinking_level()
                if default_level:
                    return str(default_level)
            return "off"
        return self.thinking_level

    def _clamp_thinking_level(self, level: str, _available_levels: list[str]) -> str:
        return clamp_thinking_level(self.model, level) if self.model else "off"

    def set_model(self, model: Model) -> None:
        previous_model = self.model
        thinking_level = self._get_thinking_level_for_model_switch()
        self.agent.state.model = model
        if self._session_store:
            self._session_store.append_model_change(model.provider, model.id)
        self.set_thinking_level(thinking_level)
        listener = getattr(self, "_model_change_listener", None)
        if listener is not None:
            listener(previous_model, model)

    def with_model_overrides(self, *, max_tokens: int) -> Model:
        overridden = replace(self.model, max_tokens=int(max_tokens))
        self.agent.state.model = overridden
        return overridden

    def set_scoped_models(self, scoped_models: list[ScopedModel]) -> None:
        self._scoped_models = list(scoped_models)

    def cycle_model(self, direction: str = "forward") -> ModelCycleResult | None:
        if self._scoped_models:
            return self._cycle_scoped_model(direction)
        return self._cycle_available_model(direction)

    def _cycle_scoped_model(self, direction: str) -> ModelCycleResult | None:
        scoped_models = [
            scoped for scoped in self._scoped_models if self.model_registry.is_selectable(scoped.model)
        ]
        if len(scoped_models) <= 1:
            return None

        current_index = next(
            (
                index
                for index, scoped in enumerate(scoped_models)
                if scoped.model.provider == self.model.provider and scoped.model.id == self.model.id
            ),
            0,
        )
        count = len(scoped_models)
        if direction == "backward":
            next_index = (current_index - 1 + count) % count
        else:
            next_index = (current_index + 1) % count

        next_scoped = scoped_models[next_index]
        thinking_level = self._get_thinking_level_for_model_switch(next_scoped.thinking_level)
        self.set_model(next_scoped.model)
        self.set_thinking_level(thinking_level)
        return ModelCycleResult(model=next_scoped.model, thinking_level=self.thinking_level, is_scoped=True)

    def _cycle_available_model(self, direction: str) -> ModelCycleResult | None:
        available_models = self.model_registry.get_selectable()
        if len(available_models) <= 1:
            return None

        current_index = next(
            (
                index
                for index, model in enumerate(available_models)
                if model.provider == self.model.provider and model.id == self.model.id
            ),
            0,
        )
        count = len(available_models)
        if direction == "backward":
            next_index = (current_index - 1 + count) % count
        else:
            next_index = (current_index + 1) % count

        next_model = available_models[next_index]
        thinking_level = self._get_thinking_level_for_model_switch()
        self.set_model(next_model)
        self.set_thinking_level(thinking_level)
        return ModelCycleResult(model=next_model, thinking_level=self.thinking_level, is_scoped=False)

__all__ = (
    'SessionModelController',
)
