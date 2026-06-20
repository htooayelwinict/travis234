"""Integrated pi+hermes coding app: ai + agent + coding_agent + compaction + tui.

Capstone composition that wires the ported parity packages into one end-to-end
application, with no imports of external source packages.
"""

from __future__ import annotations

from typing import Optional

from appv22.ai.model_resolver import ScopedModel
from appv22.ai.overflow import is_context_overflow
from appv22.ai.types import Model
from appv22.ai.types import AssistantMessage
from appv22.coding_agent.agent_session import AgentSession
from appv22.compaction.compressor import ContextCompressor
from appv22.compaction.timing import CompactionManager
from appv22.tui.interactive import InteractiveRenderer
from appv22.tui.terminal import ProcessTerminal, Terminal
from appv22.tui.tui import TUI


class CodingApp:
    """End-to-end app: AgentSession + hermes compaction (preflight) + tui rendering."""

    def __init__(
        self,
        *,
        cwd: str,
        model: Model,
        terminal: Optional[Terminal] = None,
        context_length: int = 32000,
        summarizer=None,
        thinking_level: str = "off",
        scoped_models: list[ScopedModel] | None = None,
        enable_tui: bool = True,
    ) -> None:
        self.cwd = cwd
        self.compressor = ContextCompressor(context_length=context_length, summarizer=summarizer)
        self.compaction = CompactionManager(self.compressor, summarizer=summarizer)
        self.session = AgentSession(
            cwd=cwd,
            model=model,
            transform_context=self._transform_context,
            thinking_level=thinking_level,
            scoped_models=scoped_models,
        )
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
        return self.compaction.maybe_compress_preflight(messages)

    def run_turn(self, prompt: str, stream_fn=None):
        new_messages = self.session.prompt(prompt, stream_fn=stream_fn)
        if self._recover_context_overflow(stream_fn=stream_fn):
            return new_messages
        self._compact_post_response()
        return new_messages

    @property
    def messages(self):
        return self.session.messages

    def _compact_post_response(self) -> None:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason in {"error", "aborted"}:
            return
        prompt_tokens = message.usage.total_tokens or (
            message.usage.input + message.usage.output + message.usage.cache_read + message.usage.cache_write
        )
        compacted = self.compaction.maybe_compress_post_response(self.session.messages, prompt_tokens)
        if compacted is not self.session.messages:
            self.session.agent.state.messages = compacted
        self.compaction.reset_overflow_attempts()

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
        compacted, recovered = self.compaction.recover_overflow(retained)
        if not recovered:
            self.session.agent.state.messages = retained
            return True
        self.session.agent.state.messages = compacted
        self.session.agent.continue_(stream_fn=stream_fn)
        self._compact_post_response()
        return True


def _last_assistant_message(messages) -> AssistantMessage | None:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return message
    return None
