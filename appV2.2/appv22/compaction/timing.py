"""Hermes timing compaction: trigger matrix + session rotation/lineage + cooldowns.

Port of the timing logic in hermes-agent/agent/turn_context.py (preflight),
conversation_loop.py (post-response + overflow recovery), slash_commands.py
(manual /compress force), and conversation_compression.py session rotation /
parent_session_id lineage.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from appv22.ai.types import Message
from appv22.compaction.compressor import ContextCompressor, Summarizer, estimate_tokens

SUMMARY_FAILURE_COOLDOWN_SECONDS = 600.0
AGGRESSIVE_MAX_PASSES = 3
AGGRESSIVE_TARGET_CONTEXT_RATIO = 0.05
AGGRESSIVE_MIN_TARGET_TOKENS = 2048
AGGRESSIVE_MIN_PASS_REDUCTION_PCT = 5.0


@dataclass
class ManualCompressionStatus:
    messages: list[Message]
    compressed: bool
    noop: bool
    headline: str
    token_line: str
    note: str | None = None
    focus: str | None = None
    warning: str | None = None
    info: str | None = None
    summary: str | None = None
    tokens_before: int = 0
    first_kept_message_index: int | None = None
    first_kept_entry_id: str | None = None
    aggressive: bool = False
    compression_passes: int = 1
    aggressive_stop_reason: str | None = None
    target_tokens: int | None = None


def summarize_manual_compression(
    before_messages: list[Message],
    after_messages: list[Message],
    before_tokens: int,
    after_tokens: int,
) -> dict[str, object]:
    """Return consistent Hermes-style user-facing feedback for manual compression."""
    before_count = len(before_messages)
    after_count = len(after_messages)
    noop = list(after_messages) == list(before_messages)

    if noop:
        headline = f"No changes from compression: {before_count} messages"
        if after_tokens == before_tokens:
            token_line = f"Approx request size: ~{before_tokens:,} tokens (unchanged)"
        else:
            token_line = f"Approx request size: ~{before_tokens:,} → ~{after_tokens:,} tokens"
    else:
        headline = f"Compressed: {before_count} → {after_count} messages"
        token_line = f"Approx request size: ~{before_tokens:,} → ~{after_tokens:,} tokens"

    note = None
    if not noop and after_count < before_count and after_tokens > before_tokens:
        note = (
            "Note: fewer messages can still raise this estimate when "
            "compression rewrites the transcript into denser summaries."
        )

    return {
        "noop": noop,
        "headline": headline,
        "token_line": token_line,
        "note": note,
    }


def _token_reduction_pct(before_tokens: int, after_tokens: int) -> float:
    if before_tokens <= 0:
        return 0.0
    return max(0.0, ((before_tokens - after_tokens) / before_tokens) * 100.0)


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
        self.last_compression_before_tokens = 0
        self.last_compression_after_tokens = 0
        self.awaiting_real_usage_after_compression = False
        self.overflow_attempts = 0
        self._summary_failure_cooldown_until = 0.0
        self._last_compression_error: str | None = None
        self._last_compression_result = None

    def _in_cooldown(self) -> bool:
        return self._clock() < self._summary_failure_cooldown_until

    @property
    def last_prompt_tokens(self) -> int:
        return self.compressor.last_prompt_tokens

    @last_prompt_tokens.setter
    def last_prompt_tokens(self, value: int) -> None:
        self.compressor.last_prompt_tokens = value

    @property
    def awaiting_real_usage_after_compression(self) -> bool:
        return self.compressor.awaiting_real_usage_after_compression

    @awaiting_real_usage_after_compression.setter
    def awaiting_real_usage_after_compression(self, value: bool) -> None:
        self.compressor.awaiting_real_usage_after_compression = value

    def _mark_compressed(self, messages: list[Message]) -> None:
        self.compressor.last_compression_rough_tokens = estimate_tokens(messages)
        self.compressor.last_prompt_tokens = -1
        self.compressor.last_completion_tokens = 0
        self.compressor.awaiting_real_usage_after_compression = True

    def _run_compress(
        self,
        messages: list[Message],
        summarizer,
        force: bool,
        *,
        focus: str | None = None,
        aggressive: bool = False,
    ) -> tuple[list[Message], bool]:
        summarizer = summarizer or self._summarizer
        self._last_compression_error = None
        self._last_compression_result = None
        if not force and self._in_cooldown():
            return messages, False
        before_tokens = estimate_tokens(messages)
        try:
            kwargs = {"summarizer": summarizer, "focus_topic": focus, "force": force}
            if aggressive:
                kwargs["aggressive"] = True
            result = self.compressor.compress(messages, **kwargs)
        except Exception as error:  # noqa: BLE001 - summary failure => cooldown, no crash
            self._summary_failure_cooldown_until = self._clock() + SUMMARY_FAILURE_COOLDOWN_SECONDS
            self._last_compression_error = str(error) or error.__class__.__name__
            return messages, False
        self._last_compression_result = result
        if force:
            self._summary_failure_cooldown_until = 0.0
        if result.compressed:
            self.last_compression_before_tokens = before_tokens
            self.last_compression_after_tokens = estimate_tokens(result.messages)
        return result.messages, result.compressed

    # Phase 1: preflight (rough estimate before the call; defer right after compaction).
    def maybe_compress_preflight(self, messages: list[Message], summarizer=None) -> list[Message]:
        if self.awaiting_real_usage_after_compression:
            return messages
        tokens = estimate_tokens(messages)
        if self.compressor.should_defer_preflight_to_real_usage(tokens):
            return messages
        if self.compressor.last_prompt_tokens >= 0 and tokens > self.compressor.last_prompt_tokens:
            self.compressor.last_prompt_tokens = tokens
        if not self.compressor.should_compress(tokens):
            return messages
        new_messages, compressed = self._run_compress(messages, summarizer, force=False)
        if compressed:
            self._mark_compressed(new_messages)
        return new_messages

    # Phase 2: post-response (real provider prompt tokens; -1 sentinel = just compacted).
    def maybe_compress_post_response(self, messages: list[Message], prompt_tokens: int, summarizer=None) -> list[Message]:
        real_tokens = 0 if prompt_tokens == -1 else prompt_tokens
        self.compressor.update_from_response({
            "prompt_tokens": real_tokens,
            "completion_tokens": 0,
            "total_tokens": real_tokens,
        })
        if not self.compressor.should_compress(real_tokens):
            return messages
        new_messages, compressed = self._run_compress(messages, summarizer, force=False)
        if compressed:
            self._mark_compressed(new_messages)
        return new_messages

    # Pi checks failed assistant turns using estimated context tokens so a
    # persistent provider error cannot leave a large transcript uncompactable.
    def maybe_compress_error_context(self, messages: list[Message], summarizer=None) -> list[Message]:
        tokens = estimate_tokens(messages)
        if not self.compressor.should_compress(tokens):
            return messages
        new_messages, compressed = self._run_compress(messages, summarizer, force=False)
        if compressed:
            self._mark_compressed(new_messages)
        return new_messages

    # Provider guardrail failures are not size failures. Force compression so
    # the next prompt does not resend the same blocked tool-result payload.
    def force_compress_error_context(self, messages: list[Message], summarizer=None) -> list[Message]:
        new_messages, compressed = self._run_compress(messages, summarizer, force=True)
        if compressed:
            self._mark_compressed(new_messages)
        return new_messages

    # Phase 3: overflow recovery (provider rejected; force, bounded attempts).
    def recover_overflow(self, messages: list[Message], summarizer=None) -> tuple[list[Message], bool]:
        if self.overflow_attempts >= self.max_overflow_attempts:
            return messages, False
        self.overflow_attempts += 1
        recovered_messages, compressed = self._run_compress(messages, summarizer, force=True)
        if compressed:
            return recovered_messages, True
        already_compacted = any(
            self.compressor._is_context_summary_message(message)  # noqa: SLF001 - recovery needs compressor boundary state.
            for message in recovered_messages
        )
        return recovered_messages, already_compacted

    def reset_overflow_attempts(self) -> None:
        self.overflow_attempts = 0

    # Phase 4: manual /compress (force=True clears cooldown).
    def compress_manual(self, messages: list[Message], summarizer=None, focus: str | None = None) -> list[Message]:
        return self.compress_manual_with_status(messages, summarizer=summarizer, focus=focus).messages

    def _aggressive_target_tokens(self) -> int:
        return max(AGGRESSIVE_MIN_TARGET_TOKENS, int(self.compressor.context_length * AGGRESSIVE_TARGET_CONTEXT_RATIO))

    def _aggressive_noop_reason(self) -> str:
        if self._last_compression_error:
            return "failed"
        if self.compressor._last_compress_aborted:
            return "aborted"
        if getattr(self.compressor, "_last_noop_reason", None) == "protected_recent_context":
            return "protected_recent_context"
        return "no_changes"

    def _find_context_summary_index(self, messages: list[Message]) -> int | None:
        for index, message in enumerate(messages):
            if self.compressor._is_context_summary_message(message):  # noqa: SLF001 - maps compressor output lineage.
                return index
        return None

    def _map_aggressive_source_indices(
        self,
        messages: list[Message],
        previous_sources: list[int | None],
        result,
    ) -> list[int | None]:
        summary_index = self._find_context_summary_index(messages)
        first_kept = getattr(result, "first_kept_message_index", None)
        if summary_index is None or first_kept is None:
            return [None] * len(messages)
        mapped = previous_sources[:summary_index] + [None] + previous_sources[first_kept:]
        return mapped if len(mapped) == len(messages) else [None] * len(messages)

    def _first_kept_source_after_summary(
        self,
        messages: list[Message],
        source_indices: list[int | None],
    ) -> int | None:
        summary_index = self._find_context_summary_index(messages)
        start = (summary_index + 1) if summary_index is not None else 0
        for source_index in source_indices[start:]:
            if source_index is not None:
                return source_index
        return None

    def _run_aggressive_manual_compress(
        self,
        messages: list[Message],
        summarizer,
        focus: str | None,
    ) -> tuple[list[Message], bool, int, str, int]:
        target_tokens = self._aggressive_target_tokens()
        current_messages = messages
        current_tokens = estimate_tokens(current_messages)
        source_indices: list[int | None] = list(range(len(messages)))
        compressed_any = False
        passes = 0
        stop_reason = "max_passes"
        first_kept_original_index: int | None = None

        for pass_index in range(1, AGGRESSIVE_MAX_PASSES + 1):
            before_pass_tokens = current_tokens
            previous_sources = source_indices
            new_messages, compressed = self._run_compress(
                current_messages,
                summarizer,
                force=True,
                focus=focus,
                aggressive=True,
            )
            passes = pass_index
            after_pass_tokens = estimate_tokens(new_messages)
            result = self._last_compression_result
            if compressed and result is not None:
                source_indices = self._map_aggressive_source_indices(new_messages, previous_sources, result)
                first_kept_original_index = self._first_kept_source_after_summary(new_messages, source_indices)

            current_messages = new_messages
            current_tokens = after_pass_tokens

            if self._last_compression_error:
                stop_reason = "failed"
                break
            if self.compressor._last_compress_aborted:
                stop_reason = "aborted"
                break
            if self.compressor._last_summary_fallback_used and self.compressor._last_summary_error:
                compressed_any = compressed_any or compressed
                stop_reason = "summary_fallback"
                break
            if not compressed:
                stop_reason = self._aggressive_noop_reason()
                break

            compressed_any = True
            if current_tokens <= target_tokens:
                stop_reason = "target_reached"
                break
            if _token_reduction_pct(before_pass_tokens, current_tokens) < AGGRESSIVE_MIN_PASS_REDUCTION_PCT:
                stop_reason = "insufficient_progress"
                break

        if compressed_any and first_kept_original_index is not None and self._last_compression_result is not None:
            self._last_compression_result.first_kept_message_index = first_kept_original_index
        return current_messages, compressed_any, passes, stop_reason, target_tokens

    def _format_aggressive_note(
        self,
        *,
        passes: int,
        stop_reason: str | None,
        target_tokens: int | None,
        after_tokens: int,
    ) -> str | None:
        if not stop_reason or target_tokens is None:
            return None
        pass_word = "pass" if passes == 1 else "passes"
        prefix = f"Aggressive compression: {passes} {pass_word}; "
        if stop_reason == "target_reached":
            return f"{prefix}target reached (~{after_tokens:,} <= ~{target_tokens:,} tokens)."
        if stop_reason == "max_passes":
            return f"{prefix}stopped at the 3-pass safety limit (~{after_tokens:,} tokens, target ~{target_tokens:,})."
        if stop_reason == "insufficient_progress":
            return f"{prefix}stopped because the last pass made insufficient progress."
        if stop_reason == "protected_recent_context":
            return f"{prefix}stopped because recent context is protected."
        if stop_reason == "summary_fallback":
            return f"{prefix}stopped after fallback summary recovery."
        if stop_reason == "failed":
            return f"{prefix}stopped after compression failed."
        if stop_reason == "aborted":
            return f"{prefix}stopped after compression aborted."
        if stop_reason == "no_changes":
            return f"{prefix}stopped because no more changes were available."
        return f"{prefix}stopped: {stop_reason}."

    def compress_manual_with_status(
        self,
        messages: list[Message],
        summarizer=None,
        focus: str | None = None,
        aggressive: bool = False,
    ) -> ManualCompressionStatus:
        before_tokens = estimate_tokens(messages)
        compression_passes = 1
        aggressive_stop_reason = None
        target_tokens = None
        if aggressive:
            new_messages, compressed, compression_passes, aggressive_stop_reason, target_tokens = (
                self._run_aggressive_manual_compress(messages, summarizer, focus)
            )
        else:
            new_messages, compressed = self._run_compress(
                messages,
                summarizer,
                force=True,
                focus=focus,
                aggressive=False,
            )
        after_tokens = estimate_tokens(new_messages)
        summary = summarize_manual_compression(messages, new_messages, before_tokens, after_tokens)
        warning = None
        if self._last_compression_error:
            warning = (
                f"⚠️ Compression failed: {self._last_compression_error}. "
                "No messages were dropped — conversation continues unchanged. "
                "Run /compress to retry, or /new to start a fresh session."
            )
            summary["headline"] = f"Compression failed: {self._last_compression_error}"
        elif self.compressor._last_compress_aborted:
            error = self.compressor._last_summary_error or "unknown error"
            warning = (
                f"⚠️ Compression aborted: {error}. "
                "No messages were dropped — conversation continues unchanged. "
                "Run /compress to retry, or /new to start a fresh session."
            )
        elif self.compressor._last_summary_fallback_used and self.compressor._last_summary_error:
            warning = (
                f"⚠️ Compression summary failed: {self.compressor._last_summary_error}. "
                "Inserted a fallback context marker."
            )

        info = None
        if self.compressor._last_aux_model_failure_model:
            error = self.compressor._last_aux_model_failure_error or "unknown error"
            info = (
                f"ℹ️ Configured compression model '{self.compressor._last_aux_model_failure_model}' "
                f"failed ({error}). Recovered using main model; context is intact. "
                "Check auxiliary.compression.model."
            )

        note = summary["note"] if isinstance(summary["note"], str) else None
        if (
            not compressed
            and not self._last_compression_error
            and getattr(self.compressor, "_last_noop_reason", None) == "protected_recent_context"
        ):
            note = "No compactable history; recent context is protected."
        aggressive_note = self._format_aggressive_note(
            passes=compression_passes,
            stop_reason=aggressive_stop_reason,
            target_tokens=target_tokens,
            after_tokens=after_tokens,
        )
        if aggressive_note:
            note = f"{note} {aggressive_note}" if note else aggressive_note

        result = self._last_compression_result
        return ManualCompressionStatus(
            messages=new_messages,
            compressed=compressed,
            noop=bool(summary["noop"]),
            headline=str(summary["headline"]),
            token_line=str(summary["token_line"]),
            note=note,
            focus=focus,
            warning=warning,
            info=info,
            summary=getattr(result, "summary", None),
            tokens_before=int(getattr(result, "tokens_before", before_tokens) or before_tokens),
            first_kept_message_index=getattr(result, "first_kept_message_index", None),
            aggressive=aggressive,
            compression_passes=compression_passes,
            aggressive_stop_reason=aggressive_stop_reason,
            target_tokens=target_tokens,
        )


@dataclass
class SessionRecord:
    id: str
    parent_session_id: str | None = None
    end_reason: str | None = None


class SessionLineageStore:
    """Small SQLite session-lineage store matching Hermes parent_session_id rows."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                parent_session_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id)"
        )
        self._conn.commit()

    def ensure_session(self, record: SessionRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions (id, parent_session_id, started_at, end_reason)
                VALUES (?, ?, ?, ?)
                """,
                (record.id, record.parent_session_id, time.time(), record.end_reason),
            )

    def end_session(self, session_id: str, end_reason: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
                (time.time(), end_reason, session_id),
            )

    def get_record(self, session_id: str) -> SessionRecord | None:
        cursor = self._conn.execute(
            "SELECT id, parent_session_id, end_reason FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SessionRecord(
            id=row["id"],
            parent_session_id=row["parent_session_id"],
            end_reason=row["end_reason"],
        )

    def lineage(self, current_id: str) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        seen: set[str] = set()
        cursor_id: str | None = current_id
        while cursor_id and cursor_id not in seen:
            seen.add(cursor_id)
            record = self.get_record(cursor_id)
            if record is None:
                break
            records.append(record)
            cursor_id = record.parent_session_id
        return list(reversed(records))

    def close(self) -> None:
        self._conn.close()


def _default_session_id() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class SessionLineage:
    """Session-id rotation with parent_session_id lineage (hermes compaction rotation)."""

    def __init__(
        self,
        initial_id: str | None = None,
        *,
        id_factory: Callable[[], str] = _default_session_id,
        store: SessionLineageStore | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._store = store
        first = initial_id or id_factory()
        self.current = SessionRecord(id=first, parent_session_id=None)
        self.history: list[SessionRecord] = [self.current]
        if self._store is not None:
            self._store.ensure_session(self.current)

    @classmethod
    def load(
        cls,
        store: SessionLineageStore,
        *,
        current_id: str,
        id_factory: Callable[[], str] = _default_session_id,
    ) -> "SessionLineage":
        history = store.lineage(current_id)
        if not history:
            return cls(initial_id=current_id, id_factory=id_factory, store=store)
        lineage = cls.__new__(cls)
        lineage._id_factory = id_factory
        lineage._store = store
        lineage.history = history
        lineage.current = history[-1]
        return lineage

    def rotate(self, reason: str = "compression") -> SessionRecord:
        self.current.end_reason = reason
        if self._store is not None:
            self._store.end_session(self.current.id, reason)
        new_record = SessionRecord(id=self._id_factory(), parent_session_id=self.current.id)
        self.current = new_record
        self.history.append(new_record)
        if self._store is not None:
            self._store.ensure_session(new_record)
        return new_record

    def lineage(self) -> list[str]:
        return [record.id for record in self.history]
