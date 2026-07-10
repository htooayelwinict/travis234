"""Integrated pi+hermes coding app: ai + agent + coding_agent + compaction + tui.

Capstone composition that wires the ported parity packages into one end-to-end
application, with no imports of external source packages.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any, Callable, Mapping, Optional

from appv231.ai.stream import complete_simple_sync
from appv231.ai.model_resolver import ScopedModel
from appv231.ai.overflow import is_context_overflow, parse_available_output_tokens_from_error
from appv231.ai.types import Context, Model, SimpleStreamOptions, TextContent, ToolResultMessage, UserMessage, now_ms
from appv231.ai.types import AssistantMessage
from appv231.coding_agent.agent_session import AgentSession
from appv231.coding_agent.compaction_adapter import to_compressor_messages
from appv231.coding_agent.branch_summarization import SUMMARIZATION_SYSTEM_PROMPT
from appv231.coding_agent.settings_manager import SettingsManager
from appv231.coding_agent.provider_control_plane import ProviderControlPlane
from appv231.compaction.compressor import ContextCompressor, estimate_tokens
from appv231.compaction.timing import CompactionManager
from appv231.tui.interactive import InteractiveRenderer
from appv231.tui.terminal import ProcessTerminal, Terminal
from appv231.tui.tui import TUI

DEFAULT_CONTEXT_LENGTH = 32000
PI_DEFAULT_COMPACTION_RESERVE_TOKENS = 16_384
HERMES_STATIC_PROMPT_BREATHING_ROOM = 4_096


def _resolve_session_retry_settings(settings_manager: object) -> tuple[bool, int, int]:
    retry_settings = _call_setting(settings_manager, "getRetrySettings", "get_retry_settings")
    if not isinstance(retry_settings, Mapping):
        retry_settings = {}
    enabled = _first_setting(
        _call_setting(settings_manager, "getRetryEnabled", "get_retry_enabled"),
        retry_settings.get("enabled"),
        True,
    )
    max_retries = _coerce_nonnegative_int(
        _first_setting(retry_settings.get("maxRetries"), retry_settings.get("max_retries"), 0),
        default=0,
    )
    retry_delay_ms = _coerce_nonnegative_int(
        _first_setting(retry_settings.get("baseDelayMs"), retry_settings.get("base_delay_ms"), 0),
        default=0,
    )
    return bool(enabled), max_retries, retry_delay_ms


def _call_setting(settings_manager: object, *names: str) -> Any:
    for name in names:
        candidate = getattr(settings_manager, name, None)
        if callable(candidate):
            return candidate()
    return None


def _first_setting(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _coerce_nonnegative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


class CodingApp:
    """End-to-end app: AgentSession + hermes compaction (preflight) + tui rendering."""

    def __init__(
        self,
        *,
        cwd: str,
        model: Model,
        terminal: Optional[Terminal] = None,
        context_length: int | None = None,
        summarizer=None,
        thinking_level: str = "off",
        scoped_models: list[ScopedModel] | None = None,
        enable_tui: bool = True,
        settings_manager: object | None = None,
        max_iterations: int = 90,
        tool_loop_guardrails: Mapping[str, object] | None = None,
        session_path: str | None = None,
        session_id: str | None = None,
        agent_dir: str | None = None,
        provider_control_plane: ProviderControlPlane | None = None,
        event_trace=None,
        conversation_log=None,
    ) -> None:
        self.cwd = cwd
        self.event_trace = event_trace
        self.conversation_log = conversation_log
        self.provider_control_plane = provider_control_plane or ProviderControlPlane.create_default()
        settings_manager = settings_manager or SettingsManager.inMemory()
        retry_enabled, max_retries, retry_delay_ms = _resolve_session_retry_settings(settings_manager)
        self.session = AgentSession(
            cwd=cwd,
            model=model,
            transform_context=self._transform_context,
            thinking_level=thinking_level,
            scoped_models=scoped_models,
            settings_manager=settings_manager,
            retry_enabled=retry_enabled,
            max_retries=max_retries,
            retry_delay_ms=retry_delay_ms,
            max_iterations=max_iterations,
            tool_loop_guardrails=tool_loop_guardrails,
            session_path=session_path,
            session_id=session_id,
            agent_dir=agent_dir,
            provider_control_plane=self.provider_control_plane,
        )
        if summarizer is None:
            summarizer = _model_summarizer(
                lambda: self.session.model,
                thinking_level=lambda: self.session.thinking_level,
                complete_fn=lambda active_model, context, options: self.provider_control_plane.stream_simple(
                    active_model,
                    context,
                    options,
                ).result_sync(),
            )
        resolved_context_length, threshold_percent = _resolve_compaction_window(
            model,
            self.session,
            explicit_context_length=context_length,
        )
        self.compressor = ContextCompressor(
            context_length=resolved_context_length,
            threshold_percent=threshold_percent,
            summarizer=summarizer,
        )
        self.compaction = CompactionManager(
            self.compressor,
            summarizer=summarizer,
            deep_baseline_tokens=_estimate_static_prompt_tool_tokens(self.session),
        )
        self.session.set_compaction_manager(self.compaction)
        self.terminal = terminal or ProcessTerminal()
        self.tui = TUI(self.terminal, render_interval=0.016)
        tool_definitions = {
            name: definition
            for name in self.session.get_active_tool_names()
            if (definition := self.session.get_tool_definition(name)) is not None
        }
        self.renderer = InteractiveRenderer(self.tui, tool_definitions=tool_definitions, cwd=cwd)
        if enable_tui:
            self.session.subscribe(self.renderer.handle_event)
        if self.event_trace is not None:
            self.session.subscribe(self._trace_session_event)

    def _transform_context(self, messages, signal=None):
        # Hermes preflight timing-compaction phase.
        should_emit = self._will_compact_preflight(messages)
        before_compressions = self.compaction.compressor.compression_count
        source_messages = list(messages)
        compressor_messages = to_compressor_messages(source_messages)
        if should_emit:
            self.session._begin_compaction("threshold")
        try:
            compacted = self.compaction.maybe_compress_preflight(compressor_messages)
            if compacted is not compressor_messages:
                applied = self._apply_compaction_boundary(compacted, source_messages=source_messages)
                messages[:] = applied
                return messages
            return compacted
        except Exception as error:  # noqa: BLE001 - keep lifecycle events paired if compaction unexpectedly raises.
            if should_emit:
                self.session._end_compaction(
                    reason="threshold",
                    result=None,
                    aborted=False,
                    will_retry=False,
                    error_message=f"Auto-compaction failed: {error}",
                )
            raise
        finally:
            if should_emit and self.session.is_compacting:
                result = (
                    self.session.messages
                    if self.compaction.compressor.compression_count > before_compressions
                    else None
                )
                self.session._end_compaction(reason="threshold", result=result, aborted=False, will_retry=False)

    def run_turn(
        self,
        prompt: str,
        stream_fn=None,
        on_post_response_compaction_start: Callable[[], object] | None = None,
    ):
        turn_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        before_message_count = len(self.session.messages)
        self._trace(
            "turn_start",
            {"turn_id": turn_id, "provider": self.session.model.provider, "model": self.session.model.id},
        )
        status = "ok"
        try:
            if self._recover_output_cap(stream_fn=stream_fn):
                return []
            if self._recover_context_overflow(stream_fn=stream_fn):
                return []
            self._compact_failed_turn_context()
            before_prompt_compressions = self.compaction.compressor.compression_count
            new_messages = self.session.prompt(prompt, stream_fn=stream_fn)
            if self._recover_output_cap(stream_fn=stream_fn):
                return new_messages
            if self._recover_context_overflow(stream_fn=stream_fn):
                return new_messages
            just_compacted = self.compaction.compressor.compression_count > before_prompt_compressions
            if self._compact_failed_turn_context(skip_if_just_compacted=just_compacted):
                return new_messages
            if on_post_response_compaction_start and self._will_compact_post_response():
                on_post_response_compaction_start()
            self._compact_post_response()
            return new_messages
        except Exception as error:
            status = "error"
            self._trace(
                "fatal",
                {"turn_id": turn_id, "status": "error", "error_code": type(error).__name__},
            )
            raise
        finally:
            if self.conversation_log is not None:
                self.conversation_log.write(
                    turn_id=turn_id,
                    prompt=prompt,
                    response=_assistant_text_after(self.session.messages, before_message_count),
                    status=status,
                )
            self._trace(
                "turn_end",
                {
                    "turn_id": turn_id,
                    "status": status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )

    def _trace(self, event_type: str, fields: Mapping[str, object] | None = None) -> None:
        if self.event_trace is not None:
            self.event_trace.write(event_type, fields)

    def _trace_session_event(self, event) -> None:
        event_type = getattr(event, "type", None)
        if event_type == "tool_execution_end":
            self._trace(
                "tool_end",
                {
                    "tool_call_id": str(getattr(event, "tool_call_id", "")),
                    "tool": str(getattr(event, "tool_name", "")),
                    "status": "error" if getattr(event, "is_error", False) else "ok",
                },
            )
        elif event_type == "compaction_end":
            self._trace(
                "compaction_end",
                {
                    "status": "error" if getattr(event, "error_message", None) else "ok",
                    "compression_count": self.compaction.compressor.compression_count,
                },
            )

    @property
    def messages(self):
        return self.session.messages

    def _compact_post_response(self) -> None:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason in {"error", "aborted"}:
            return
        prompt_tokens = _assistant_prompt_tokens(message)
        should_emit = self._will_compact_post_response()
        before_compressions = self.compaction.compressor.compression_count
        source_messages = list(self.session.messages)
        compressor_messages = to_compressor_messages(source_messages)
        if should_emit:
            self.session._begin_compaction("threshold")
        try:
            compacted = self.compaction.maybe_compress_post_response(compressor_messages, prompt_tokens)
            if compacted is not compressor_messages:
                self._apply_compaction_boundary(compacted, source_messages=source_messages)
        except Exception as error:  # noqa: BLE001 - keep lifecycle events paired if compaction unexpectedly raises.
            if should_emit:
                self.session._end_compaction(
                    reason="threshold",
                    result=None,
                    aborted=False,
                    will_retry=False,
                    error_message=f"Auto-compaction failed: {error}",
                )
            raise
        else:
            if should_emit:
                result = (
                    self.session.messages
                    if self.compaction.compressor.compression_count > before_compressions
                    else None
                )
                self.session._end_compaction(reason="threshold", result=result, aborted=False, will_retry=False)
        finally:
            self.compaction.reset_overflow_attempts()

    def _will_compact_post_response(self) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason in {"error", "aborted"}:
            return False
        prompt_tokens = _assistant_prompt_tokens(message)
        real_tokens = 0 if prompt_tokens == -1 else prompt_tokens
        return self.compaction.compressor.should_compress(real_tokens)

    def _will_compact_preflight(self, messages) -> bool:
        if self.compaction.awaiting_real_usage_after_compression:
            return False
        tokens = estimate_tokens(to_compressor_messages(messages))
        if self.compaction.compressor.should_defer_preflight_to_real_usage(tokens):
            return False
        return self.compaction.compressor.should_compress(tokens)

    def _recover_context_overflow(self, *, stream_fn=None) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason != "error":
            return False
        if not is_context_overflow(message.error_message or ""):
            return False

        # Pi removes the overflow assistant error from model context before compact-and-retry.
        retained = [
            item
            for item in self.session.messages
            if item is not message
        ]
        self.session._begin_compaction("overflow")
        try:
            compacted, recovered = self.compaction.recover_overflow(to_compressor_messages(retained))
        except Exception as error:  # noqa: BLE001 - keep lifecycle events paired if compaction unexpectedly raises.
            self.session._end_compaction(
                reason="overflow",
                result=None,
                aborted=False,
                will_retry=False,
                error_message=f"Context overflow recovery failed: {error}",
            )
            raise
        if not recovered:
            self.session.agent.state.messages = retained
            self.session._end_compaction(reason="overflow", result=None, aborted=False, will_retry=False)
            return True
        compacted = self._apply_compaction_boundary(compacted, source_messages=retained)
        self.session._end_compaction(reason="overflow", result=compacted, aborted=False, will_retry=True)
        self.session.agent.continue_(stream_fn=stream_fn)
        self._compact_post_response()
        return True

    def _compact_failed_turn_context(self, *, skip_if_just_compacted: bool = False) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason not in {"error", "aborted"}:
            return False
        is_prompt_guardrail = message.stop_reason == "error" and _is_prompt_injection_guardrail_error(
            message.error_message or ""
        )

        # A provider error is not proof that the post-compaction request fit.
        # Clear Hermes' "await real usage" gate so the next prompt can compact
        # instead of resending a failed large turn unchanged.
        if message.stop_reason == "error":
            self.compaction.awaiting_real_usage_after_compression = False
        if is_prompt_guardrail:
            retained = [
                item
                for item in self.session.messages
                if item is not message
            ]
            retained = _elide_failed_turn_tool_results(retained)
            before_compressions = self.compaction.compressor.compression_count
            self.session._begin_compaction("threshold")
            try:
                compacted = self.compaction.force_compress_error_context(to_compressor_messages(retained))
                compacted = self._apply_compaction_boundary(
                    compacted,
                    source_messages=retained,
                    retain_source_suffix=False,
                )
            except Exception as error:  # noqa: BLE001 - keep lifecycle events paired if compaction unexpectedly raises.
                self.session.agent.state.messages = list(retained)
                self.session._end_compaction(
                    reason="threshold",
                    result=None,
                    aborted=False,
                    will_retry=False,
                    error_message=f"Auto-compaction failed: {error}",
                )
                raise
            else:
                result = (
                    self.session.messages
                    if self.compaction.compressor.compression_count > before_compressions
                    else retained
                )
                self.session._end_compaction(reason="threshold", result=result, aborted=False, will_retry=False)
                return True
        if skip_if_just_compacted:
            return False

        if not self.compaction.compressor.should_compress(
            estimate_tokens(to_compressor_messages(self.session.messages))
        ):
            return False

        before_compressions = self.compaction.compressor.compression_count
        source_messages = list(self.session.messages)
        compressor_messages = to_compressor_messages(source_messages)
        self.session._begin_compaction("threshold")
        try:
            compacted = self.compaction.maybe_compress_error_context(compressor_messages)
            if compacted is not compressor_messages:
                self._apply_compaction_boundary(compacted, source_messages=source_messages)
        except Exception as error:  # noqa: BLE001 - keep lifecycle events paired if compaction unexpectedly raises.
            self.session._end_compaction(
                reason="threshold",
                result=None,
                aborted=False,
                will_retry=False,
                error_message=f"Auto-compaction failed: {error}",
            )
            raise
        else:
            result = (
                self.session.messages
                if self.compaction.compressor.compression_count > before_compressions
                else None
            )
            self.session._end_compaction(reason="threshold", result=result, aborted=False, will_retry=False)
            return result is not None

    def _apply_compaction_boundary(
        self,
        compacted,
        *,
        source_messages,
        retain_source_suffix: bool = True,
    ):
        result = self.compaction._last_compression_result  # noqa: SLF001 - app owns the compaction manager lifecycle.
        if result is not None and getattr(result, "compressed", False):
            return self.session.apply_compaction_result(
                list(compacted),
                result,
                source_messages=list(source_messages),
                retain_source_suffix=retain_source_suffix,
            )
        self.session.agent.state.messages = list(compacted)
        return list(compacted)

    def _recover_output_cap(self, *, stream_fn=None) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason != "error":
            return False
        available_tokens = parse_available_output_tokens_from_error(message.error_message or "")
        if available_tokens is None:
            return False

        model = self.session.agent.state.model
        current_max_tokens = int(model.max_tokens or 0)
        if current_max_tokens > 0 and available_tokens >= current_max_tokens:
            return False

        self.session.with_model_overrides(max_tokens=max(1, available_tokens))
        retained = [item for item in self.session.messages if item is not message]
        self.session.agent.state.messages = retained
        self.compaction.reset_overflow_attempts()
        if not retained or getattr(retained[-1], "role", None) == "assistant":
            return True

        self.session.agent.continue_(stream_fn=stream_fn)
        self._compact_post_response()
        return True


def _model_summarizer(
    model: Model | Callable[[], Model],
    *,
    thinking_level: str | Callable[[], str] = "off",
    complete_fn=complete_simple_sync,
):
    def summarize(prompt: str) -> str:
        active_model = model() if callable(model) else model
        active_thinking_level = thinking_level() if callable(thinking_level) else thinking_level
        options = SimpleStreamOptions(
            max_tokens=_summarizer_max_tokens(active_model),
            reasoning=(
                active_thinking_level
                if active_model.reasoning and active_thinking_level != "off"
                else None
            ),
        )
        response = complete_fn(
            active_model,
            Context(
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
                messages=[UserMessage(content=[TextContent(text=prompt)], timestamp=now_ms())],
            ),
            options,
        )
        if response.stop_reason == "error":
            raise RuntimeError(response.error_message or "Summarization failed")
        return "\n".join(block.text for block in response.content if isinstance(block, TextContent))

    return summarize


def _summarizer_max_tokens(model: Model) -> int:
    if model.max_tokens and model.max_tokens > 0:
        return min(model.max_tokens, 12_000)
    return 2048


def _last_assistant_message(messages) -> AssistantMessage | None:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return message
    return None


def _assistant_text_after(messages, start_index: int) -> str | None:
    message = _last_assistant_message(list(messages)[start_index:])
    if message is None:
        return None
    text = "".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
    return text or None


def _assistant_prompt_tokens(message: AssistantMessage) -> int:
    return message.usage.total_tokens or (
        message.usage.input + message.usage.output + message.usage.cache_read + message.usage.cache_write
    )


_PROMPT_GUARDRAIL_ERROR_PATTERNS = (
    "prompt-injection guardrail",
    "prompt injection patterns detected",
    "system_prefix_spoofing",
)


def _is_prompt_injection_guardrail_error(error_message: str) -> bool:
    lowered = (error_message or "").lower()
    return any(pattern in lowered for pattern in _PROMPT_GUARDRAIL_ERROR_PATTERNS)


def _elide_failed_turn_tool_results(messages) -> list:
    last_user_index = -1
    for index, message in enumerate(messages):
        if getattr(message, "role", None) == "user":
            last_user_index = index
    if last_user_index < 0:
        return list(messages)

    elided = list(messages)
    for index in range(last_user_index + 1, len(elided)):
        message = elided[index]
        if not isinstance(message, ToolResultMessage):
            continue
        text = "".join(block.text for block in message.content if isinstance(block, TextContent))
        clone = copy.copy(message)
        clone.content = [TextContent(text=_tool_result_guardrail_placeholder(message, text))]
        elided[index] = clone
    return elided


def _tool_result_guardrail_placeholder(message: ToolResultMessage, text: str) -> str:
    line_count = text.count("\n") + 1 if text else 0
    return (
        f"[{message.tool_name}] result elided after provider prompt-injection guardrail "
        f"({len(text)} chars, {line_count} lines). Use narrower reads or summarize code scans before retrying."
    )


def _resolve_compaction_window(
    model: Model,
    session: AgentSession,
    *,
    explicit_context_length: int | None,
) -> tuple[int, float]:
    if explicit_context_length is not None:
        return explicit_context_length, 0.5

    context_length = int(model.context_window or 0) or DEFAULT_CONTEXT_LENGTH
    if context_length <= DEFAULT_CONTEXT_LENGTH:
        return context_length, 0.5

    static_tokens = _estimate_static_prompt_tool_tokens(session)
    reserve_tokens = PI_DEFAULT_COMPACTION_RESERVE_TOKENS + static_tokens + HERMES_STATIC_PROMPT_BREATHING_ROOM
    threshold_tokens = context_length - reserve_tokens
    if threshold_tokens <= 0:
        threshold_tokens = max(1, context_length // 2)
    return context_length, threshold_tokens / context_length


def _estimate_static_prompt_tool_tokens(session: AgentSession) -> int:
    system_prompt = session.system_prompt or ""
    tools = []
    for tool in session.agent.state.tools:
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        })
    payload = system_prompt + json.dumps(tools, sort_keys=True)
    return len(payload) // 4
