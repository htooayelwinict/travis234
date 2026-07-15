"""Focused turns ownership for coding sessions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from travis.agent.agent import Agent
from travis.agent.async_utils import resolve
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

from travis.coding_agent.session_persistence import _user_message
from travis.coding_agent.session_policy_controller import (
    _is_internal_steering_user_message,
)
from travis.coding_agent.session_types import AgentSettledEvent, AutoRetryEndEvent, AutoRetryStartEvent, _MALFORMED_STREAMED_TOOL_ARGS_MARKER, _MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE, _MALFORMED_STREAM_RECOVERY_PREFIX, _MAX_PARTIAL_STREAM_CONTINUATIONS, _NON_RETRYABLE_PROVIDER_LIMIT_MARKERS, _PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE, _PARTIAL_STREAM_STUB_ID, _RETRYABLE_ERROR_MARKERS, _SUBAGENT_TOOL_NAMES, _prompt_requests_subagent_tools
from travis.coding_agent.prompt_templates import expand_prompt_template
from travis.coding_agent.input_expansion import InputExpansionError, expand_user_input
from travis.coding_agent.skills import format_skill_invocation
from travis.coding_agent.subagent_trace import _message_content_text

def _wait_for_retry_abort(signal: AbortSignal, delay_ms: int) -> bool:
    deadline = time.monotonic() + max(0, delay_ms) / 1000
    while True:
        if signal.aborted:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return signal.aborted
        time.sleep(min(remaining, 0.05))


def _partial_stream_dropped_tool_names(message: AssistantMessage) -> list[str]:
    if message.stop_reason != "length":
        return []
    if message.response_id != _PARTIAL_STREAM_STUB_ID:
        return []
    names: list[str] = []
    for item in message.diagnostics or []:
        if not isinstance(item, dict):
            continue
        if item.get("code") != _PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE:
            continue
        dropped = item.get("dropped_tool_names")
        if not isinstance(dropped, list):
            continue
        for name in dropped:
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names


def _malformed_stream_dropped_tool_names(message: AssistantMessage) -> list[str]:
    if message.stop_reason != "length":
        return []
    if message.response_id != _PARTIAL_STREAM_STUB_ID:
        return []
    names: list[str] = []
    for item in message.diagnostics or []:
        if not isinstance(item, dict):
            continue
        if item.get("code") != _MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE:
            continue
        dropped = item.get("dropped_tool_names")
        if not isinstance(dropped, list):
            continue
        for name in dropped:
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names


def _partial_stream_continuation_prompt(dropped_tools: list[str]) -> str:
    tool_list = ", ".join(dropped_tools[:3]) if dropped_tools else "unknown tool"
    return (
        "[System: Your previous tool call "
        f"({tool_list}) was too large or malformed and "
        "the stream ended before its arguments could be delivered. Do NOT retry "
        "the same tool call with the same malformed or oversized arguments. "
        "Retry the write tool when the task is to create or replace a file. Put "
        "the exact intended file bytes in write.content as a valid JSON string; "
        "use JSON unicode escapes such as \\u003c and \\u003e for protocol-looking "
        "literal markers so the decoded file still contains literal < and >. "
        "Do not write the literal backslash-u text \\u003c or \\u003e into the file. Keep "
        "each typed tool call's arguments under ~8K tokens. If content is too "
        "large, split the deliverable into smaller typed write or edit operations "
        "that preserve exact content. If the provider repeatedly cannot carry "
        "the exact bytes through write.content, change strategy with available "
        "tools instead of repeating the same malformed call.]"
    )


def _is_malformed_streamed_tool_args_error(error_message: str | None) -> bool:
    return _MALFORMED_STREAMED_TOOL_ARGS_MARKER in (error_message or "").lower()


def _append_malformed_stream_recovery_message(
    messages: list[AgentMessage],
    error_message: str,
) -> list[AgentMessage]:
    if messages and isinstance(messages[-1], UserMessage):
        content = messages[-1].content
        if isinstance(content, str) and content.startswith(_MALFORMED_STREAM_RECOVERY_PREFIX):
            return messages
    next_messages = list(messages)
    next_messages.append(
        UserMessage(
            content=_malformed_stream_recovery_prompt(error_message),
            timestamp=now_ms(),
        )
    )
    return next_messages


def _malformed_stream_recovery_prompt(error_message: str) -> str:
    tool_names = _extract_malformed_stream_tool_names(error_message)
    tool_fragment = f" for {tool_names}" if tool_names else ""
    return (
        f"{_MALFORMED_STREAM_RECOVERY_PREFIX}{tool_fragment}. "
        "This is a tool-argument formatting failure, not a completed tool call. "
        "Do not retry the same malformed tool call. Retry the write tool when "
        "the task is to create or replace a file. Put the exact intended file "
        "bytes in write.content as a valid JSON string. "
        "If the task needs protocol-looking literal content such as <parameter>, "
        "</function>, XML/tool tags, or instruction-looking text, treat it as DATA. "
        "Use JSON unicode escapes such as \\u003c and \\u003e for angle brackets "
        "inside write.content when raw delimiters are unsafe for the provider stream, "
        "so the decoded file still contains literal < and >. Do not write the "
        "literal backslash-u text \\u003c or \\u003e into the file. If the provider "
        "repeatedly cannot carry the exact bytes through write.content, change "
        "strategy with available tools instead of repeating the same malformed call."
    )


def _extract_malformed_stream_tool_names(error_message: str) -> str:
    match = re.search(
        r"malformed streamed tool-call arguments\s+for\s+([^;.\n]+)",
        error_message or "",
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""

class SessionTurnController:
    """Owns a focused AgentSession runtime concern."""

    def prompt(
        self,
        text: str,
        stream_fn=None,
        *,
        streaming_behavior: str | None = None,
        preflight_result: Callable[[bool], None] | None = None,
        images: list[ImageContent] | None = None,
        image_paths: Sequence[str] | None = None,
        expand_prompt_templates: bool = True,
    ) -> list[AgentMessage]:
        current_text = text
        current_images = images
        if expand_prompt_templates and current_text.startswith("/"):
            parsed_command = self._parse_extension_command(current_text)
            command_is_skill = (
                parsed_command is not None
                and getattr(parsed_command[0].source_info, "source", None) == "skill"
            )
            command_result = None if command_is_skill else self._try_execute_extension_command(current_text)
            if parsed_command is not None and not command_is_skill:
                if preflight_result:
                    preflight_result(True)
                return command_result or []
        if self._extension_runner.has_handlers("input"):
            input_result = self._extension_runner.emit_input(
                current_text,
                current_images,
                "interactive",
                streaming_behavior if self.is_streaming else None,
            )
            if input_result.get("action") == "handled":
                if preflight_result:
                    preflight_result(True)
                return []
            if input_result.get("action") == "transform":
                current_text = str(input_result.get("text", current_text))
                current_images = input_result.get("images", current_images)

        if expand_prompt_templates:
            parsed_command = self._parse_extension_command(current_text)
            if (
                parsed_command is not None
                and getattr(parsed_command[0].source_info, "source", None) == "skill"
            ):
                skill_name = parsed_command[0].registration_name.removeprefix("skill:")
                skill = next(
                    (
                        item
                        for item in self._resource_loader.get_skills()["skills"]
                        if getattr(item, "name", None) == skill_name
                    ),
                    None,
                )
                if skill is not None:
                    current_text = format_skill_invocation(skill, parsed_command[1])
            current_text = expand_prompt_template(current_text, self.prompt_templates)

        current_text, current_images = self._expand_user_references(
            current_text,
            current_images,
            image_paths=image_paths,
        )

        if self.is_streaming:
            try:
                if not streaming_behavior:
                    raise RuntimeError(
                        "Agent is already processing. Specify streamingBehavior ('steer' or 'followUp') to queue the message."
                    )
                if streaming_behavior == "followUp" or streaming_behavior == "follow_up":
                    self._queue_turn_input("follow_up", current_text, current_images)
                elif streaming_behavior == "steer":
                    self._queue_turn_input("steering", current_text, current_images)
                else:
                    raise ValueError("streaming_behavior must be 'steer' or 'followUp'")
            except Exception:
                if preflight_result:
                    preflight_result(False)
                raise
            if preflight_result:
                preflight_result(True)
            return []

        if preflight_result:
            preflight_result(True)
        restore_active_tool_names: list[str] | None = None
        if _prompt_requests_subagent_tools(current_text):
            current_active_tool_names = self.get_active_tool_names()
            missing_subagent_tools = [
                name for name in _SUBAGENT_TOOL_NAMES if name not in set(current_active_tool_names)
            ]
            if missing_subagent_tools:
                restore_active_tool_names = current_active_tool_names
                self.set_active_tools_by_name([*current_active_tool_names, *missing_subagent_tools])
        self.agent.state.system_prompt = self.system_prompt
        self._reset_model_subagent_turn_budget()
        try:
            if self._pending_next_turn_messages:
                prompt_message = _user_message(current_text, current_images)
                pending_next_turn = list(self._pending_next_turn_messages)
                self._pending_next_turn_messages = []
                prompt_messages = [prompt_message, *pending_next_turn]
                prompt_messages = self._apply_before_agent_start(current_text, current_images, prompt_messages)
                return self._run_agent_prompt(prompt_messages, stream_fn=stream_fn)

            prompt_message = _user_message(current_text, current_images)
            prompt_message = self._apply_before_agent_start(current_text, current_images, prompt_message)
            return self._run_agent_prompt(prompt_message, stream_fn=stream_fn)
        finally:
            if restore_active_tool_names is not None:
                self.set_active_tools_by_name(restore_active_tool_names)

    def _reset_model_subagent_turn_budget(self) -> None:
        self._model_subagents_spawned_this_turn = 0
        self._model_subagent_spawn_signatures_this_turn.clear()

    def continue_(self, stream_fn=None) -> list[AgentMessage]:
        try:
            return self.agent.continue_(stream_fn=stream_fn or self._stream_fn)
        finally:
            if not self._defer_agent_settled:
                self.emit_agent_settled()

    def emit_agent_settled(self) -> None:
        self._extension_runner.emit({"type": "agent_settled"})
        self._emit(AgentSettledEvent())

    def steer(self, text: str, images: list[ImageContent] | None = None) -> str:
        self._raise_if_extension_command(text)
        expanded_text, expanded_images = self._expand_user_references(text, images)
        return self._queue_turn_input("steering", expanded_text, expanded_images)

    def follow_up(self, text: str, images: list[ImageContent] | None = None) -> str:
        self._raise_if_extension_command(text)
        expanded_text, expanded_images = self._expand_user_references(text, images)
        return self._queue_turn_input("follow_up", expanded_text, expanded_images)

    def _queue_turn_input(
        self,
        kind: str,
        text: str,
        images: list[ImageContent] | None,
    ) -> str:
        queued = self._turn_mailbox.enqueue(kind, text, images)
        if not self.agent.state.is_streaming:
            self._flush_turn_mailbox_kind(kind)
        self._emit_queue_update()
        return queued.id

    def _expand_user_references(
        self,
        text: str,
        images: list[ImageContent] | None,
        *,
        image_paths: Sequence[str] | None = None,
    ) -> tuple[str, list[ImageContent] | None]:
        expanded = expand_user_input(
            text,
            cwd=self.cwd,
            images=image_paths or (),
        )
        referenced_images = [
            block for block in expanded.content if isinstance(block, ImageContent)
        ]
        if referenced_images and "image" not in self.model.input:
            raise InputExpansionError(
                f"Model {self.model.provider}/{self.model.id} does not support image input"
            )
        merged_images = [*(images or []), *referenced_images]
        return expanded.text, merged_images or None

    def _flush_turn_mailbox(self) -> None:
        self._flush_turn_mailbox_kind("steering")
        self._flush_turn_mailbox_kind("follow_up")

    def _flush_turn_mailbox_kind(self, kind: MailboxKind) -> None:
        if kind == "steering":
            mode = self.agent.steering_mode
            sender = self.agent.steer
        else:
            mode = self.agent.follow_up_mode
            sender = self.agent.follow_up
        for queued in self._turn_mailbox.drain(kind, mode=mode):
            message = _user_message(queued.text, list(queued.images))
            setattr(message, "_coding_queue_id", queued.id)
            sender(message)

    def _restore_unacknowledged_turn_messages(self) -> None:
        restored = self._turn_mailbox.restore_unacknowledged()
        if not restored:
            return
        restored_ids = {item.id for item in restored}
        self.agent._steering.messages = [  # noqa: SLF001 - coding adapter owns tagged messages.
            message
            for message in self.agent._steering.messages  # noqa: SLF001
            if getattr(message, "_coding_queue_id", None) not in restored_ids
        ]
        self.agent._follow_up.messages = [  # noqa: SLF001 - coding adapter owns tagged messages.
            message
            for message in self.agent._follow_up.messages  # noqa: SLF001
            if getattr(message, "_coding_queue_id", None) not in restored_ids
        ]
        self._emit_queue_update()

    def send_custom_message(self, message: dict, options: dict | None = None, stream_fn=None) -> list[AgentMessage]:
        options = options or {}
        app_message = CustomMessage(
            custom_type=message["customType"],
            content=message.get("content", ""),
            display=bool(message.get("display", True)),
            details=message.get("details"),
            timestamp=int(time.time() * 1000),
        )
        deliver_as = options.get("deliverAs", options.get("deliver_as"))
        if options.get("transient", options.get("local_only", False)):
            self._emit(MessageStartEvent(message=app_message))
            self._emit(MessageEndEvent(message=app_message))
            return [app_message]
        if deliver_as == "nextTurn":
            self._pending_next_turn_messages.append(app_message)
            return []
        if self.is_streaming:
            if deliver_as == "followUp" or deliver_as == "follow_up":
                self.agent.follow_up(app_message)
            else:
                self.agent.steer(app_message)
            return []
        if options.get("triggerTurn", options.get("trigger_turn", False)):
            return self._run_agent_prompt(app_message, stream_fn=stream_fn)

        self.agent.state.messages.append(app_message)
        if self._session_store:
            self._session_store.append_custom_message_entry(
                app_message.custom_type,
                app_message.content,
                app_message.display,
                app_message.details,
            )
        self._emit(MessageStartEvent(message=app_message))
        self._emit(MessageEndEvent(message=app_message))
        return [app_message]

    def _apply_before_agent_start(self, text: str, images, prompt_message):
        if not self._extension_runner.has_handlers("before_agent_start"):
            return prompt_message
        result = self._extension_runner.emit_before_agent_start(text, images, self.system_prompt, None)
        if not result:
            return prompt_message
        if result.get("systemPrompt") is not None:
            self.agent.state.system_prompt = str(result["systemPrompt"])
        injected: list[AgentMessage] = []
        for message in result.get("messages", []) or []:
            if not isinstance(message, dict):
                continue
            injected.append(
                CustomMessage(
                    custom_type=message.get("customType", ""),
                    content=message.get("content", ""),
                    display=bool(message.get("display", True)),
                    details=message.get("details"),
                    timestamp=int(time.time() * 1000),
                )
            )
        if not injected:
            return prompt_message
        if isinstance(prompt_message, list):
            return [*prompt_message, *injected]
        return [prompt_message, *injected]

    async def _transform_context(
        self,
        messages: list[AgentMessage],
        signal: AbortSignal | None = None,
    ) -> list[AgentMessage]:
        transformed = (
            await resolve(self._caller_transform_context(messages, signal))
            if self._caller_transform_context is not None
            else list(messages)
        )
        if transformed is None:
            transformed = list(messages)
        if self._extension_runner.has_handlers("context"):
            return await self._extension_runner.async_emit_context(transformed)
        return transformed

    async def _on_provider_payload(self, payload, model=None):
        if not self._extension_runner.has_handlers("before_provider_request"):
            return payload
        return await self._extension_runner.async_emit_before_provider_request(payload)

    async def _on_provider_headers(self, headers, model=None):
        if not self._extension_runner.has_handlers("before_provider_headers"):
            return headers
        return await self._extension_runner.async_emit_before_provider_headers(headers)

    async def _on_provider_response(self, response, model=None) -> None:
        if not self._extension_runner.has_handlers("after_provider_response"):
            return None
        status = response.get("status") if isinstance(response, dict) else getattr(response, "status", None)
        headers = response.get("headers") if isinstance(response, dict) else getattr(response, "headers", None)
        await self._extension_runner.async_emit(
            {
                "type": "after_provider_response",
                "status": status,
                "headers": headers,
            }
        )
        return None

    def _prepare_next_turn(self, context=None, signal: AbortSignal | None = None) -> AgentLoopTurnUpdate:
        if isinstance(context, AbortSignal) and signal is None:
            signal = context
            context = None
        self._flush_turn_mailbox()
        active_tool_names = self.get_active_tool_names()
        self.system_prompt = self._build_system_prompt(active_tool_names)
        self.agent.state.system_prompt = self.system_prompt
        self._queue_partial_stream_continuation_if_needed()
        return AgentLoopTurnUpdate(
            context=AgentContext(
                system_prompt=self.agent.state.system_prompt,
                messages=list(self.agent.state.messages),
                tools=list(self.agent.state.tools),
            ),
            model=self.agent.state.model,
            thinking_level=self.agent.state.thinking_level,
        )

    def _queue_partial_stream_continuation_if_needed(self) -> None:
        last_message = self.agent.state.messages[-1] if self.agent.state.messages else None
        if not isinstance(last_message, AssistantMessage):
            return
        malformed_tools = _malformed_stream_dropped_tool_names(last_message)
        if malformed_tools:
            if self._partial_stream_continue_retries >= _MAX_PARTIAL_STREAM_CONTINUATIONS:
                return
            self._partial_stream_continue_retries += 1
            self.agent.follow_up(
                UserMessage(
                    content=[
                        TextContent(
                            text=_malformed_stream_recovery_prompt(
                                f"{_MALFORMED_STREAMED_TOOL_ARGS_MARKER} for {', '.join(malformed_tools)}"
                            )
                        )
                    ],
                    timestamp=now_ms(),
                )
            )
            return
        dropped_tools = _partial_stream_dropped_tool_names(last_message)
        if not dropped_tools:
            if last_message.stop_reason != "length":
                self._partial_stream_continue_retries = 0
            return
        if self._partial_stream_continue_retries >= _MAX_PARTIAL_STREAM_CONTINUATIONS:
            return
        self._partial_stream_continue_retries += 1
        self.agent.follow_up(
            UserMessage(
                content=[TextContent(text=_partial_stream_continuation_prompt(dropped_tools))],
                timestamp=now_ms(),
            )
        )

    def clear_queue(self) -> dict[str, list[str]]:
        steering = [item.text for item in self._turn_mailbox.clear("steering")]
        follow_up = [item.text for item in self._turn_mailbox.clear("follow_up")]
        self.agent.clear_all_queues()
        self._emit_queue_update()
        return {"steering": steering, "follow_up": follow_up}

    def _run_agent_prompt(self, prompt_message, stream_fn=None) -> list[AgentMessage]:
        self._flush_pending_bash_messages()
        self._partial_stream_continue_retries = 0
        active_stream_fn = stream_fn or self._stream_fn
        try:
            new_messages = list(self.agent.prompt(prompt_message, stream_fn=active_stream_fn))
            while self._prepare_retry(_last_assistant_message(new_messages)):
                retry_messages = list(self.agent.continue_(stream_fn=active_stream_fn))
                new_messages.extend(retry_messages)
                latest = _last_assistant_message(retry_messages)
                if latest and latest.stop_reason != "error":
                    self._emit(AutoRetryEndEvent(success=True, attempt=self._retry_attempt))
                    self._retry_attempt = 0
                    break
                if latest and latest.stop_reason == "error" and self._retry_attempt >= self._max_retries:
                    self._emit(
                        AutoRetryEndEvent(
                            success=False,
                            attempt=self._retry_attempt,
                            final_error=latest.error_message,
                        )
                    )
                    self._retry_attempt = 0
                    break
            return new_messages
        finally:
            try:
                self._flush_pending_bash_messages()
            finally:
                if not self._defer_agent_settled:
                    self.emit_agent_settled()

    def _prepare_retry(self, message: AssistantMessage | None) -> bool:
        if not self._retry_enabled or message is None or message.stop_reason != "error":
            return False
        if self._retry_attempt >= self._max_retries:
            if self._retry_attempt > 0:
                self._emit(
                    AutoRetryEndEvent(
                        success=False,
                        attempt=self._retry_attempt,
                        final_error=message.error_message,
                    )
                )
                self._retry_attempt = 0
            return False
        if not self._is_retryable_error(message):
            return False
        self._retry_attempt += 1
        self._retry_signal = AbortSignal()
        self._emit(
            AutoRetryStartEvent(
                attempt=self._retry_attempt,
                max_attempts=self._max_retries,
                delay_ms=self._retry_delay_ms * (2 ** (self._retry_attempt - 1)),
                error_message=message.error_message or "",
            )
        )
        if self.messages and isinstance(self.messages[-1], AssistantMessage):
            retry_context_messages = list(self.messages[:-1])
            if _is_malformed_streamed_tool_args_error(message.error_message):
                retry_context_messages = _append_malformed_stream_recovery_message(
                    retry_context_messages,
                    message.error_message or "",
                )
            self.agent.state.messages = retry_context_messages
        delay_ms = self._retry_delay_ms * (2 ** (self._retry_attempt - 1))
        try:
            if delay_ms > 0 and _wait_for_retry_abort(self._retry_signal, delay_ms):
                attempt = self._retry_attempt
                self._retry_attempt = 0
                self._emit(
                    AutoRetryEndEvent(
                        success=False,
                        attempt=attempt,
                        final_error="Retry cancelled",
                    )
                )
                return False
        finally:
            self._retry_signal = None
        return True

    def _is_retryable_error(self, message: AssistantMessage) -> bool:
        if self._retryable_error_predicate:
            return self._retryable_error_predicate(message)
        error = (message.error_message or "").lower()
        if any(marker in error for marker in _NON_RETRYABLE_PROVIDER_LIMIT_MARKERS):
            return False
        return any(marker in error for marker in _RETRYABLE_ERROR_MARKERS)

    def _wait_for_retry_abort(self, signal: AbortSignal, delay_ms: int) -> bool:
        return _wait_for_retry_abort(signal, delay_ms)

    def _latest_user_message_text(self, agent_context) -> str:
        messages = getattr(agent_context, "messages", None) or []
        for message in reversed(messages):
            if getattr(message, "role", None) != "user":
                continue
            text = _message_content_text(getattr(message, "content", ""))
            if _is_internal_steering_user_message(text):
                continue
            return text
        return ""

__all__ = (
    'SessionTurnController',
    '_append_malformed_stream_recovery_message',
    '_extract_malformed_stream_tool_names',
    '_is_malformed_streamed_tool_args_error',
    '_malformed_stream_dropped_tool_names',
    '_malformed_stream_recovery_prompt',
    '_partial_stream_continuation_prompt',
    '_partial_stream_dropped_tool_names',
    '_wait_for_retry_abort',
)
