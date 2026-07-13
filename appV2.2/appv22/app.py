"""Integrated pi+hermes coding app: ai + agent + coding_agent + compaction + tui.

Capstone composition that wires the ported parity packages into one end-to-end
application, with no imports of external source packages.
"""

from __future__ import annotations

import copy
import json
from typing import Callable, Mapping, Optional

from appv22.ai.stream import complete_simple_sync
from appv22.ai.model_resolver import ScopedModel
from appv22.ai.overflow import is_context_overflow, parse_available_output_tokens_from_error
from appv22.ai.types import Context, Model, SimpleStreamOptions, TextContent, ToolResultMessage, UserMessage, now_ms
from appv22.ai.types import AssistantMessage
from appv22.coding_agent.agent_session import AgentSession
from appv22.coding_agent.branch_summarization import SUMMARIZATION_SYSTEM_PROMPT
from appv22.compaction.compressor import ContextCompressor, estimate_tokens
from appv22.compaction.timing import CompactionManager
from appv22.tui.interactive import InteractiveRenderer
from appv22.tui.terminal import ProcessTerminal, Terminal
from appv22.tui.tui import TUI

DEFAULT_CONTEXT_LENGTH = 32000
PI_DEFAULT_COMPACTION_RESERVE_TOKENS = 16_384
HERMES_STATIC_PROMPT_BREATHING_ROOM = 4_096


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
    ) -> None:
        self.cwd = cwd
        summarizer = summarizer or _model_summarizer(model, thinking_level=thinking_level)
        self.session = AgentSession(
            cwd=cwd,
            model=model,
            transform_context=self._transform_context,
            thinking_level=thinking_level,
            scoped_models=scoped_models,
            settings_manager=settings_manager,
            max_iterations=max_iterations,
            tool_loop_guardrails=tool_loop_guardrails,
            session_path=session_path,
            session_id=session_id,
            agent_dir=agent_dir,
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
        self.compaction = CompactionManager(self.compressor, summarizer=summarizer)
        self.session.set_compaction_manager(self.compaction)
        self.terminal = terminal or ProcessTerminal()
        self.tui = TUI(self.terminal)
        tool_definitions = {
            name: definition
            for name in self.session.get_active_tool_names()
            if (definition := self.session.get_tool_definition(name)) is not None
        }
        self.renderer = InteractiveRenderer(self.tui, tool_definitions=tool_definitions, cwd=cwd)
        if enable_tui:
            self.session.subscribe(self.renderer.handle_event)

    def _transform_context(self, messages, signal=None):
        # Hermes preflight timing-compaction phase.
        should_emit = self._will_compact_preflight(messages)
        before_compressions = self.compaction.compressor.compression_count
        if should_emit:
            self.session._begin_compaction("threshold")
        try:
            compacted = self.compaction.maybe_compress_preflight(messages)
            if compacted is not messages:
                messages[:] = compacted
                self.session.agent.state.messages = list(compacted)
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
        if should_emit:
            self.session._begin_compaction("threshold")
        try:
            compacted = self.compaction.maybe_compress_post_response(self.session.messages, prompt_tokens)
            if compacted is not self.session.messages:
                self.session.agent.state.messages = compacted
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
        tokens = estimate_tokens(messages)
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
            compacted, recovered = self.compaction.recover_overflow(retained)
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
        self.session.agent.state.messages = compacted
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
                compacted = self.compaction.force_compress_error_context(retained)
                self.session.agent.state.messages = list(compacted)
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

        if not self.compaction.compressor.should_compress(estimate_tokens(self.session.messages)):
            return False

        before_compressions = self.compaction.compressor.compression_count
        self.session._begin_compaction("threshold")
        try:
            compacted = self.compaction.maybe_compress_error_context(self.session.messages)
            if compacted is not self.session.messages:
                self.session.agent.state.messages = list(compacted)
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

        model.max_tokens = max(1, available_tokens)
        retained = [item for item in self.session.messages if item is not message]
        self.session.agent.state.messages = retained
        self.compaction.reset_overflow_attempts()
        if not retained or getattr(retained[-1], "role", None) == "assistant":
            return True

        self.session.agent.continue_(stream_fn=stream_fn)
        self._compact_post_response()
        return True


def _model_summarizer(model: Model, *, thinking_level: str = "off"):
    def summarize(prompt: str) -> str:
        options = SimpleStreamOptions(
            max_tokens=_summarizer_max_tokens(model),
            reasoning=thinking_level if model.reasoning and thinking_level != "off" else None,
        )
        response = complete_simple_sync(
            model,
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
