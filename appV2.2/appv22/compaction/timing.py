"""Hermes timing compaction: trigger matrix + session rotation/lineage + cooldowns.

Port of the timing logic in hermes-agent/agent/turn_context.py (preflight),
conversation_loop.py (post-response + overflow recovery), slash_commands.py
(manual /compress force), and conversation_compression.py session rotation /
parent_session_id lineage.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from appv22.ai.types import Message
from appv22.compaction.compressor import ContextCompressor, Summarizer, estimate_tokens

SUMMARY_FAILURE_COOLDOWN_SECONDS = 60.0


class CompactionManager:
    """Wires a ContextCompressor into the four hermes timing phases."""

    def __init__(
        self,
        compressor: ContextCompressor,
        *,
        summarizer: Optional[Summarizer] = None,
        max_overflow_attempts: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.compressor = compressor
        self._summarizer = summarizer
        self.max_overflow_attempts = max_overflow_attempts
        self._clock = clock
        self.last_prompt_tokens = 0
        self.awaiting_real_usage_after_compression = False
        self.overflow_attempts = 0
        self._summary_failure_cooldown_until = 0.0

    def _in_cooldown(self) -> bool:
        return self._clock() < self._summary_failure_cooldown_until

    def _run_compress(self, messages: list[Message], summarizer, force: bool) -> tuple[list[Message], bool]:
        summarizer = summarizer or self._summarizer
        if not force and self._in_cooldown():
            return messages, False
        try:
            result = self.compressor.compress(messages, summarizer=summarizer)
        except Exception:  # noqa: BLE001 - summary failure => cooldown, no crash
            self._summary_failure_cooldown_until = self._clock() + SUMMARY_FAILURE_COOLDOWN_SECONDS
            return messages, False
        if force:
            self._summary_failure_cooldown_until = 0.0
        return result.messages, result.compressed

    # Phase 1: preflight (rough estimate before the call; defer right after compaction).
    def maybe_compress_preflight(self, messages: list[Message], summarizer=None) -> list[Message]:
        if self.awaiting_real_usage_after_compression:
            return messages
        tokens = estimate_tokens(messages)
        if not self.compressor.should_compress(tokens):
            return messages
        new_messages, compressed = self._run_compress(messages, summarizer, force=False)
        if compressed:
            self.awaiting_real_usage_after_compression = True
            self.last_prompt_tokens = -1
        return new_messages

    # Phase 2: post-response (real provider prompt tokens; -1 sentinel = just compacted).
    def maybe_compress_post_response(self, messages: list[Message], prompt_tokens: int, summarizer=None) -> list[Message]:
        self.awaiting_real_usage_after_compression = False
        real_tokens = 0 if prompt_tokens == -1 else prompt_tokens
        self.last_prompt_tokens = real_tokens
        if not self.compressor.should_compress(real_tokens):
            return messages
        new_messages, compressed = self._run_compress(messages, summarizer, force=False)
        if compressed:
            self.last_prompt_tokens = -1
            self.awaiting_real_usage_after_compression = True
        return new_messages

    # Phase 3: overflow recovery (provider rejected; force, bounded attempts).
    def recover_overflow(self, messages: list[Message], summarizer=None) -> tuple[list[Message], bool]:
        if self.overflow_attempts >= self.max_overflow_attempts:
            return messages, False
        self.overflow_attempts += 1
        return self._run_compress(messages, summarizer, force=True)

    def reset_overflow_attempts(self) -> None:
        self.overflow_attempts = 0

    # Phase 4: manual /compress (force=True clears cooldown).
    def compress_manual(self, messages: list[Message], summarizer=None, focus: str | None = None) -> list[Message]:
        new_messages, _ = self._run_compress(messages, summarizer, force=True)
        return new_messages


@dataclass
class SessionRecord:
    id: str
    parent_session_id: str | None = None
    end_reason: str | None = None


def _default_session_id() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class SessionLineage:
    """Session-id rotation with parent_session_id lineage (hermes compaction rotation)."""

    def __init__(self, initial_id: str | None = None, *, id_factory: Callable[[], str] = _default_session_id) -> None:
        self._id_factory = id_factory
        first = initial_id or id_factory()
        self.current = SessionRecord(id=first, parent_session_id=None)
        self.history: list[SessionRecord] = [self.current]

    def rotate(self, reason: str = "compression") -> SessionRecord:
        self.current.end_reason = reason
        new_record = SessionRecord(id=self._id_factory(), parent_session_id=self.current.id)
        self.current = new_record
        self.history.append(new_record)
        return new_record

    def lineage(self) -> list[str]:
        return [record.id for record in self.history]
