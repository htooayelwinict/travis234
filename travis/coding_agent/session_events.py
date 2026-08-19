"""Focused events ownership for coding sessions."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from travis.ai.types import (
    AssistantMessage,
    ToolCall,
    now_ms,
)
from travis.coding_agent.policy.types import TOOL_EFFECT_ORDER, ToolPolicyDecision
from travis.coding_agent.session_extensions import _replace_message_in_place
from travis.coding_agent.session_surfaces import SessionEventControllerSurface
from travis.coding_agent.session_types import QueueUpdateEvent
from travis.coding_agent.tools.process import PROCESS_ACTIONS, prepare_process_arguments

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPolicyDecisionEvent:
    tool: str
    effects: tuple[str, ...]
    mode: str
    allow: bool
    reason_code: str
    type: str = "tool_policy_decision"


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

class SessionEventController(SessionEventControllerSurface):
    """Owns a focused AgentSession runtime concern."""

    __slots__ = ()

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

    async def _handle_agent_event(self, event) -> None:
        if event.type == "message_start" and getattr(event.message, "role", None) == "user":
            queue_id = getattr(event.message, "_coding_queue_id", None)
            if isinstance(queue_id, str) and self._turn_mailbox.acknowledge(queue_id):
                self._emit_queue_update()
        await self._emit_extension_event(event)
        if event.type == "message_end" and isinstance(event.message, AssistantMessage):
            _canonicalize_process_tool_calls(event.message)
        if event.type == "agent_end":
            self._restore_unacknowledged_turn_messages()
            event.will_retry = self._will_retry_after_agent_end(event)
            object.__setattr__(event, "willRetry", event.will_retry)
        if event.type == "message_end" and self._session_store:
            message_role = getattr(event.message, "role", None)
            if message_role == "custom":
                self._session_store.append_custom_message_entry(
                    getattr(event.message, "custom_type", "custom"),
                    event.message.content,
                    bool(getattr(event.message, "display", True)),
                    getattr(event.message, "details", None),
                )
            elif message_role in ("user", "assistant", "toolResult"):
                self._session_store.append_message(event.message)
                self._operation_record_persisted_message(event.message)
        self._emit(event)

    async def _emit_extension_event(self, event) -> None:
        if event.type == "agent_start":
            self._turn_index = 0
            await self._extension_runner.async_emit({"type": "agent_start"})
            return
        if event.type == "agent_end":
            await self._extension_runner.async_emit(
                {"type": "agent_end", "messages": event.messages}
            )
            return
        if event.type == "turn_start":
            await self._extension_runner.async_emit(
                {
                    "type": "turn_start",
                    "turnIndex": self._turn_index,
                    "timestamp": now_ms(),
                }
            )
            return
        if event.type == "turn_end":
            await self._extension_runner.async_emit(
                {
                    "type": "turn_end",
                    "turnIndex": self._turn_index,
                    "message": event.message,
                    "toolResults": event.tool_results,
                }
            )
            self._turn_index += 1
            return
        if event.type == "message_start":
            await self._extension_runner.async_emit(
                {"type": "message_start", "message": event.message}
            )
            return
        if event.type == "message_update":
            await self._extension_runner.async_emit(
                {
                    "type": "message_update",
                    "message": event.message,
                    "assistantMessageEvent": event.assistant_message_event,
                }
            )
            return
        if event.type == "message_end":
            replacement = await self._extension_runner.async_emit_message_end(
                {"type": "message_end", "message": event.message}
            )
            if replacement is not None:
                replacement = _normalize_extension_message(replacement)
                _replace_message_in_place(event.message, replacement)
            return
        if event.type == "tool_execution_start":
            await self._extension_runner.async_emit(
                {
                    "type": "tool_execution_start",
                    "toolCallId": event.tool_call_id,
                    "toolName": event.tool_name,
                    "args": event.args,
                }
            )
            return
        if event.type == "tool_execution_update":
            await self._extension_runner.async_emit(
                {
                    "type": "tool_execution_update",
                    "toolCallId": event.tool_call_id,
                    "toolName": event.tool_name,
                    "args": event.args,
                    "partialResult": event.partial_result,
                }
            )
            return
        if event.type == "tool_execution_end":
            await self._extension_runner.async_emit(
                {
                    "type": "tool_execution_end",
                    "toolCallId": event.tool_call_id,
                    "toolName": event.tool_name,
                    "result": event.result,
                    "isError": event.is_error,
                }
            )

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

    def _emit_tool_policy_decision(self, decision: ToolPolicyDecision) -> None:
        effects = tuple(effect for effect in TOOL_EFFECT_ORDER if effect in decision.effects)
        event = ToolPolicyDecisionEvent(
            tool=decision.tool_name,
            effects=effects,
            mode=decision.mode,
            allow=decision.allow,
            reason_code=decision.reason_code,
        )
        self._emit(event)
        sink = self._tool_policy_event_sink
        if sink is None:
            return
        try:
            sink(
                {
                    "type": event.type,
                    "tool": event.tool,
                    "effects": list(event.effects),
                    "mode": event.mode,
                    "allow": event.allow,
                    "reason_code": event.reason_code,
                }
            )
        except Exception as error:  # noqa: BLE001 - diagnostics never change tool outcomes.
            logger.warning(
                "Tool policy decision sink failed for %s (%s)",
                event.tool,
                type(error).__name__,
            )

    def _emit(self, event) -> None:
        for listener in list(self._event_listeners):
            try:
                listener(_snapshot_session_event(event))
            except Exception as error:  # noqa: BLE001 - public observers cannot fail the session.
                logger.warning(
                    "Session observer failed for %s (%s)",
                    getattr(event, "type", type(event).__name__),
                    type(error).__name__,
                )


def _snapshot_session_event(event):
    snapshot = copy.copy(event)
    fields_by_type = {
        "message_start": ("message",),
        "message_update": ("message", "assistant_message_event"),
        "message_end": ("message",),
        "agent_end": ("messages",),
        "turn_end": ("message", "tool_results"),
        "tool_execution_start": ("args",),
        "tool_execution_update": ("args", "partial_result"),
        "tool_execution_end": ("args", "result"),
        "queue_update": ("steering", "follow_up"),
    }
    for field_name in fields_by_type.get(getattr(event, "type", ""), ()):
        if hasattr(event, field_name):
            setattr(snapshot, field_name, copy.deepcopy(getattr(event, field_name)))
    return snapshot


def _normalize_extension_message(message):
    role = getattr(message, "role", None)
    if role not in {"user", "assistant", "toolResult", "custom"}:
        return message
    if getattr(message, "content", None) is not None:
        return message
    try:
        return replace(message, content=[])
    except TypeError:
        message.content = []
        return message

__all__ = (
    'SessionEventController',
    '_canonicalize_process_tool_calls',
    '_normalize_extension_message',
)
