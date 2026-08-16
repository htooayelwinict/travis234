"""Session-owned turn, provider, usage, and persistence journal boundaries."""

from __future__ import annotations

import hashlib
import re
import sys
import threading
from typing import Mapping

from travis.ai.stream_proxy import ProxyEventStream
from travis.ai.types import AssistantMessage
from travis.coding_agent.policy import argument_fingerprint


_EFFECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_request_shape(model, context, options) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    for message in context.messages:
        content = getattr(message, "content", None)
        if isinstance(content, list):
            content_types = [str(getattr(item, "type", type(item).__name__)) for item in content]
        else:
            content_types = [type(content).__name__]
        messages.append(
            {
                "role": str(getattr(message, "role", "unknown")),
                "contentTypes": content_types,
            }
        )
    option_names = tuple(
        name
        for name in (
            "transport",
            "timeout_ms",
            "websocket_connect_timeout_ms",
            "max_retries",
            "reasoning",
            "service_tier",
            "text_verbosity",
        )
        if getattr(options, name, None) is not None
    ) if options is not None else ()
    return {
        "provider": str(model.provider),
        "model": str(model.id),
        "api": str(model.api),
        "messages": messages,
        "tools": sorted(str(tool.name) for tool in (context.tools or [])),
        "optionNames": list(option_names),
    }


def _effect_name(value: object) -> str:
    candidate = str(value or "provider")
    if _EFFECT_NAME.fullmatch(candidate) is not None:
        return candidate
    return f"provider-{_hash_text(candidate)[:16]}"


class SessionOperationController:
    """Observe session effects without owning execution or conversation state."""

    def _initialize_session_operations(
        self,
        *,
        role: str | None,
        task_id: str | None,
    ) -> None:
        self._operation_role = str(role or "primary")[:128]
        self._operation_task_id = task_id
        self._operation_turn_sequence = 0
        self._operation_turn_active = False
        self._operation_start_message_count = 0
        self._operation_assistant_sequence = 0
        self._operation_usage_keys: dict[int, str] = {}

    def _operation_start_turn(self) -> None:
        self._operation_turn_sequence += 1
        self._operation_start_message_count = len(self.messages)
        self._operation_assistant_sequence = 0
        self._operation_usage_keys.clear()
        registers: dict[str, object] = {
            "role": self._operation_role,
            "turn_sequence": self._operation_turn_sequence,
        }
        session_id = self.session_id or None
        if session_id:
            registers["session_id_hash"] = _hash_text(session_id)
        branch_id = self.get_session_leaf_id()
        if branch_id:
            registers["branch_id_hash"] = _hash_text(branch_id)
        if self._operation_task_id:
            registers["task_id_hash"] = _hash_text(self._operation_task_id)
        try:
            operation_id = self.operation_coordinator.start("turn", session_id)
            self._operation_turn_active = operation_id is not None
            if self._operation_turn_active:
                self.operation_coordinator.advance("turn_started", registers)
        except Exception:
            self._operation_turn_active = False
            self._disable_operation_journal()

    def _operation_finish_turn(self) -> None:
        if not self._operation_turn_active:
            return
        outcome = "turn_error" if sys.exc_info()[0] is not None else "ok"
        recent = self.messages[self._operation_start_message_count :]
        assistant = next(
            (message for message in reversed(recent) if isinstance(message, AssistantMessage)),
            None,
        )
        if outcome == "ok" and assistant is not None:
            if assistant.stop_reason == "aborted":
                outcome = "cancelled"
            elif assistant.stop_reason == "error":
                outcome = "turn_error"
        try:
            self.operation_coordinator.advance("turn_settled", {})
            self.operation_coordinator.complete(outcome)
        except Exception:
            self._disable_operation_journal()
        finally:
            self._operation_turn_active = False

    def _operation_continue(self, stream_fn=None):
        self._operation_start_turn()
        try:
            return self.agent.continue_(
                stream_fn=self._operation_stream_fn(stream_fn or self._stream_fn)
            )
        finally:
            self._operation_finish_turn()

    def _operation_stream_fn(self, stream_fn):
        def wrapped(model, context, options=None):
            return self._operation_invoke_provider(
                stream_fn, model, context, options
            )

        return wrapped

    def _operation_invoke_provider(self, stream_fn, model, context, options=None):
        handle = None
        try:
            if self._operation_turn_active:
                self.operation_coordinator.advance("provider_intent", {})
                handle = self.operation_coordinator.begin_effect(
                    "provider",
                    _effect_name(model.provider),
                    argument_fingerprint(
                        _provider_request_shape(model, context, options)
                    ),
                )
        except Exception:
            self._disable_operation_journal()
        try:
            source = stream_fn(model, context, options)
        except BaseException:
            self._operation_settle_provider(handle, "provider_error")
            raise
        if handle is None:
            return source
        return self._operation_observe_provider_stream(source, handle)

    def _operation_observe_provider_stream(self, source, handle):
        proxy = ProxyEventStream(source)
        settled = threading.Event()

        def settle(outcome_code: str) -> None:
            if settled.is_set():
                return
            settled.set()
            self._operation_settle_provider(handle, outcome_code)

        def outcome(message) -> str:
            stop_reason = getattr(message, "stop_reason", None)
            if stop_reason == "aborted":
                return "cancelled"
            if stop_reason == "error":
                return "provider_error"
            return "ok"

        def forward() -> None:
            try:
                for event in source.iter_until(lambda: proxy.cancelled):
                    if event.type == "done":
                        settle(outcome(event.message))
                    elif event.type == "error":
                        settle(outcome(event.error))
                    proxy.push(event)
                if proxy.cancelled:
                    settle("cancelled")
                else:
                    result = source.result_sync()
                    settle(outcome(result))
                    proxy.end(result)
            except BaseException as error:  # noqa: BLE001 - preserve provider behavior.
                settle("provider_error")
                proxy.fail(error)
            finally:
                source.close()

        threading.Thread(
            target=forward,
            name="travis-operation-provider-stream",
            daemon=True,
        ).start()
        return proxy

    def _operation_settle_provider(self, handle, outcome_code: str) -> None:
        if handle is None:
            return
        try:
            self.operation_coordinator.settle_effect(handle, outcome_code)
            self.operation_coordinator.advance("provider_settled", {})
        except Exception:
            self._disable_operation_journal()

    def _operation_record_persisted_message(self, message) -> None:
        if not self._operation_turn_active:
            return
        if isinstance(message, AssistantMessage):
            identity = id(message)
            source_key = self._operation_usage_keys.get(identity)
            if source_key is None:
                self._operation_assistant_sequence += 1
                source_key = argument_fingerprint(
                    {
                        "turnSequence": self._operation_turn_sequence,
                        "assistantSequence": self._operation_assistant_sequence,
                        "provider": message.provider,
                        "model": message.model,
                        "responseId": message.response_id,
                        "timestamp": message.timestamp,
                    }
                )
                self._operation_usage_keys[identity] = source_key
            usage = message.usage
            try:
                self.operation_coordinator.record_usage(
                    source_key,
                    provider=message.provider,
                    model=message.model,
                    input_tokens=usage.input,
                    output_tokens=usage.output,
                    cache_read_tokens=usage.cache_read,
                    cache_write_tokens=usage.cache_write,
                    cost=usage.cost.total,
                )
            except Exception:
                self._disable_operation_journal()
        self._operation_advance(
            "conversation_persisted",
            {"message_role": str(getattr(message, "role", "unknown"))[:64]},
        )

    def _operation_record_tools_settled(self, tool_call_id: str) -> None:
        self._operation_advance(
            "tools_settled", {"tool_call_id_hash": _hash_text(tool_call_id)}
        )

    def _operation_advance(
        self, phase: str, registers: Mapping[str, object] | None = None
    ) -> int:
        if not self._operation_turn_active:
            return 0
        try:
            return self.operation_coordinator.advance(phase, registers or {})
        except Exception:
            self._disable_operation_journal()
            return 0


__all__ = ["SessionOperationController"]
