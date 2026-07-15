"""Coordinates manual compaction with the active agent run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from travis.agent.agent import Agent
from travis.agent.types import AgentMessage
from travis.coding_agent.compaction_adapter import (
    SessionCompactionAdapter,
    merge_summary_model_compaction_details,
    to_compressor_context,
)
from travis.coding_agent.deep_compaction_command import generate_deep_checkpoint
from travis.compaction.compressor import estimate_tokens
from travis.compaction.timing import CompactionManager, ManualCompressionStatus, summarize_manual_compression
from travis.compaction.strategy import prepare_compaction


def _deep_refusal_note(reason: str | None) -> str:
    notes = {
        "unanswered_user": (
            "Deep checkpoint refused because the latest user message has no completed answer."
        ),
        "aborted_assistant": (
            "Deep checkpoint refused because the latest assistant response was aborted."
        ),
        "errored_assistant": (
            "Deep checkpoint refused because the latest assistant response failed."
        ),
        "unmatched_tool_call": "Deep checkpoint refused because a tool call is unfinished.",
        "unfinished_tool_turn": (
            "Deep checkpoint refused because the latest tool turn has no final assistant response."
        ),
        "summarizer_capacity": (
            "Deep checkpoint refused because the summarizer cannot fit the checkpoint source. "
            "Run normal /compact first."
        ),
        "summary_failed": (
            "Deep checkpoint summary generation failed; the original context was preserved."
        ),
        "summary_unavailable": (
            "Deep checkpoint has no configured summarizer; the original context was preserved."
        ),
        "repair_failed": "Deep checkpoint repair failed; the original context was preserved.",
        "repair_unavailable": (
            "Deep checkpoint repair was unavailable; the original context was preserved."
        ),
        "validation_failed": (
            "Deep checkpoint validation failed; the original context was preserved."
        ),
        "secret_present": (
            "Deep checkpoint validation rejected secret-shaped output; the original context "
            "was preserved."
        ),
        "reasoning_present": (
            "Deep checkpoint validation rejected reasoning output; the original context was "
            "preserved."
        ),
        "invalid_structure": (
            "Deep checkpoint validation rejected an invalid handoff; the original context "
            "was preserved."
        ),
        "over_budget": (
            "Deep checkpoint remained over its absolute budget; the original context was "
            "preserved."
        ),
        "insufficient_reduction": (
            "Deep checkpoint was skipped because it would not materially reduce context."
        ),
    }
    return notes.get(reason, "Deep checkpoint refused; the original context was preserved.")


class CompactionDeferredError(RuntimeError):
    pass


class CompactionCancelledError(RuntimeError):
    pass


class CompactionCoordinator:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def prepare(self, timeout: float | None = 30.0) -> Literal["ready", "deferred"]:
        lease = self._agent.run_lease
        if not lease.active:
            return "ready"
        if lease.owned_by_current_thread:
            return "deferred"
        self._agent.abort()
        if not lease.wait(timeout):
            raise TimeoutError("Timed out waiting for the active run before compaction")
        return "ready"


@dataclass(frozen=True)
class CompactionOutcome:
    messages: list[AgentMessage]
    compressed: bool
    recovered: bool = False
    result: object | None = None
    will_retry: bool = False
    aborted: bool = False


class CompactionTransactionCoordinator:
    """Pairs lifecycle, manager execution, persistence, and error reporting."""

    def __init__(
        self,
        *,
        manager: CompactionManager,
        run_coordinator: CompactionCoordinator,
        adapter: SessionCompactionAdapter,
        continue_agent: Callable[..., object],
        extension_runner: object | None = None,
        branch_entries: Callable[[], list[dict]] | None = None,
        signal: Callable[[], object] | None = None,
    ) -> None:
        self.manager = manager
        self._run_coordinator = run_coordinator
        self._adapter = adapter
        self._continue_agent = continue_agent
        self._extension_runner = extension_runner
        self._branch_entries = branch_entries or (lambda: [])
        self._signal = signal or (lambda: None)

    def manual(self, focus: str | None = None, summarizer=None, deep: bool = False):
        if self._run_coordinator.prepare() == "deferred":
            raise CompactionDeferredError("Compaction deferred until the active run completes")
        source = list(self._adapter.messages)

        def operation() -> CompactionOutcome:
            extension_result = self._before_compact(
                source,
                reason="manual",
                will_retry=False,
                custom_instructions=focus,
            )
            extension_compaction = self._extension_compaction_payload(extension_result)
            if extension_compaction is not None:
                output, _record, _entry = self._apply_extension_compaction(
                    extension_compaction,
                    source_messages=source,
                    trigger="manual",
                    reason="manual",
                    will_retry=False,
                )
                summary = str(extension_compaction["summary"])
                tokens_before = int(extension_compaction["tokensBefore"])
                first_kept_entry_id = str(extension_compaction.get("firstKeptEntryId") or "")
                feedback = summarize_manual_compression(
                    source,
                    output,
                    tokens_before,
                    estimate_tokens(output),
                )
                status = ManualCompressionStatus(
                    messages=output,
                    compressed=True,
                    noop=False,
                    headline=str(feedback["headline"]),
                    token_line=str(feedback["token_line"]),
                    note=feedback["note"] if isinstance(feedback["note"], str) else None,
                    focus=focus,
                    summary=summary,
                    details=extension_compaction.get("details") if isinstance(extension_compaction.get("details"), dict) else None,
                    tokens_before=tokens_before,
                    first_kept_entry_id=first_kept_entry_id,
                    deep=deep,
                )
                return CompactionOutcome(messages=output, compressed=True, result=status)
            if deep:
                deep_result = generate_deep_checkpoint(
                    source,
                    self.manager.compressor,
                    summarizer=summarizer or self.manager._summarizer,  # noqa: SLF001
                    focus=focus,
                )
                if not deep_result.compressed:
                    status = ManualCompressionStatus(
                        messages=source,
                        compressed=False,
                        noop=True,
                        headline="Deep checkpoint made no changes",
                        token_line=(
                            f"Approx request size: ~{deep_result.tokens_before:,} "
                            "tokens (unchanged)"
                        ),
                        note=_deep_refusal_note(deep_result.error or deep_result.reason),
                        warning=(
                            f"Deep checkpoint failed: {deep_result.error}"
                            if deep_result.error
                            else None
                        ),
                        focus=focus,
                        tokens_before=deep_result.tokens_before,
                        deep=True,
                        compression_passes=1 + deep_result.repair_count,
                        deep_stop_reason=deep_result.reason,
                        target_tokens=deep_result.target_tokens,
                    )
                    return CompactionOutcome(messages=source, compressed=False, result=status)

                compaction = {
                    "summary": deep_result.summary,
                    "firstKeptEntryId": "",
                    "tokensBefore": deep_result.tokens_before,
                    "details": deep_result.details,
                }
                output, entry = self._adapter.apply_extension_compaction(
                    compaction,
                    source_messages=source,
                )
                persisted_details = (
                    entry.get("details")
                    if isinstance(entry.get("details"), dict)
                    else deep_result.details
                )
                record = self.manager.record_extension_compaction(
                    output,
                    summary=deep_result.summary or "",
                    tokens_before=deep_result.tokens_before,
                    details=persisted_details,
                    trigger="manual",
                )
                self._emit_session_compact(
                    entry,
                    from_extension=False,
                    reason="manual",
                    will_retry=False,
                )
                status = ManualCompressionStatus(
                    messages=output,
                    compressed=True,
                    noop=False,
                    headline=f"Deep checkpoint: {len(source)} → {len(output)} messages",
                    token_line=(
                        f"Approx request size: ~{deep_result.tokens_before:,} → "
                        f"~{deep_result.handoff_tokens:,} tokens"
                    ),
                    note=(
                        "Created one bounded generational handoff with no retained raw suffix."
                        + (" One repair pass was used." if deep_result.repair_count else "")
                    ),
                    focus=focus,
                    summary=deep_result.summary,
                    details=persisted_details,
                    tokens_before=deep_result.tokens_before,
                    first_kept_entry_id="",
                    summary_model_requested=record.summary_model_requested,
                    summary_model_used=record.summary_model_used,
                    summary_model_fallback=record.summary_model_fallback,
                    summary_model_error=record.summary_model_error,
                    summary_model_dedicated=record.summary_model_dedicated,
                    deep=True,
                    compression_passes=1 + deep_result.repair_count,
                    deep_stop_reason="target_reached",
                    target_tokens=deep_result.target_tokens,
                )
                return CompactionOutcome(messages=output, compressed=True, result=status)
            compressor_context = to_compressor_context(source)
            status = self.manager.compress_manual_with_status(
                compressor_context.messages,
                summarizer=summarizer,
                focus=focus,
                deep=deep,
                durable=self._adapter.is_persistent,
            )
            self._adapter.apply_manual_status(
                status,
                source,
                source_indices=compressor_context.source_indices,
            )
            if status.compressed:
                entry = self._adapter.latest_compaction_entry() or {
                    "type": "compaction",
                    "summary": status.summary or "",
                    "firstKeptEntryId": status.first_kept_entry_id or "",
                    "tokensBefore": status.tokens_before,
                    "details": status.details,
                }
                self._emit_session_compact(
                    entry,
                    from_extension=False,
                    reason="manual",
                    will_retry=False,
                )
            return CompactionOutcome(
                messages=status.messages,
                compressed=bool(status.compressed),
                result=status,
                aborted=bool(self.manager.compressor._last_compress_aborted),
            )

        return self._transaction(
            reason="manual",
            operation=operation,
            failure_prefix="Compaction failed",
            include_noop_result=True,
        ).result

    def preflight(self, messages: list[AgentMessage]) -> CompactionOutcome:
        source = list(messages)
        compressor_context = to_compressor_context(source)
        compressor_messages = compressor_context.messages
        should_emit = self._should_compact_preflight(compressor_messages)
        before_compressions = self.manager.compressor.compression_count

        def operation() -> CompactionOutcome:
            if should_emit:
                extension_result = self._before_compact(
                    source,
                    reason="threshold",
                    will_retry=False,
                )
                extension_compaction = self._extension_compaction_payload(extension_result)
                if extension_compaction is not None:
                    output, record, _entry = self._apply_extension_compaction(
                        extension_compaction,
                        source_messages=source,
                        trigger="preflight",
                        reason="threshold",
                        will_retry=False,
                    )
                    messages[:] = output
                    return CompactionOutcome(
                        messages=messages,
                        compressed=True,
                        result=record,
                    )
            compacted = self.manager.maybe_compress_preflight(
                compressor_messages,
                durable=self._adapter.is_persistent,
            )
            if compacted is not compressor_messages:
                result = self.manager.last_compression_result
                applied = self._adapter.apply_result(
                    compacted,
                    result,
                    source_messages=source,
                    source_indices=compressor_context.source_indices,
                )
                messages[:] = applied
                output = messages
            else:
                output = compacted
            compressed = self.manager.compressor.compression_count > before_compressions
            if compressed:
                self._emit_builtin_compaction(reason="threshold", will_retry=False)
            return CompactionOutcome(
                messages=output,
                compressed=compressed,
                result=(
                    self._adapter.messages
                    if compressed
                    else self.manager.last_compression_result
                    if self.manager.compressor._last_compress_aborted
                    else None
                ),
                aborted=bool(self.manager.compressor._last_compress_aborted),
            )

        if not should_emit:
            return operation()
        try:
            return self._transaction(reason="threshold", operation=operation, failure_prefix="Auto-compaction failed")
        except CompactionCancelledError:
            return CompactionOutcome(messages=messages, compressed=False)

    def post_response(
        self,
        messages: Sequence[AgentMessage],
        prompt_tokens: int,
    ) -> CompactionOutcome:
        source = list(messages)
        compressor_context = to_compressor_context(source)
        compressor_messages = compressor_context.messages
        real_tokens = 0 if prompt_tokens == -1 else prompt_tokens
        should_emit = self.manager.compressor.should_compress(real_tokens)
        before_compressions = self.manager.compressor.compression_count

        def operation() -> CompactionOutcome:
            if should_emit:
                extension_result = self._before_compact(
                    source,
                    reason="threshold",
                    will_retry=False,
                )
                extension_compaction = self._extension_compaction_payload(extension_result)
                if extension_compaction is not None:
                    output, record, _entry = self._apply_extension_compaction(
                        extension_compaction,
                        source_messages=source,
                        trigger="post_response",
                        reason="threshold",
                        will_retry=False,
                    )
                    return CompactionOutcome(messages=output, compressed=True, result=record)
            compacted = self.manager.maybe_compress_post_response(
                compressor_messages,
                prompt_tokens,
                durable=self._adapter.is_persistent,
            )
            if compacted is not compressor_messages:
                result = self.manager.last_compression_result
                output = self._adapter.apply_result(
                    compacted,
                    result,
                    source_messages=source,
                    source_indices=compressor_context.source_indices,
                )
            else:
                output = compacted
            compressed = self.manager.compressor.compression_count > before_compressions
            if compressed:
                self._emit_builtin_compaction(reason="threshold", will_retry=False)
            return CompactionOutcome(
                messages=output,
                compressed=compressed,
                result=(
                    self._adapter.messages
                    if compressed
                    else self.manager.last_compression_result
                    if self.manager.compressor._last_compress_aborted
                    else None
                ),
                aborted=bool(self.manager.compressor._last_compress_aborted),
            )

        try:
            if not should_emit:
                return operation()
            try:
                return self._transaction(reason="threshold", operation=operation, failure_prefix="Auto-compaction failed")
            except CompactionCancelledError:
                unchanged = messages if isinstance(messages, list) else source
                return CompactionOutcome(messages=unchanged, compressed=False)
        finally:
            self.manager.reset_overflow_attempts()

    def recover_overflow(
        self,
        messages: Sequence[AgentMessage],
        *,
        stream_fn=None,
    ) -> CompactionOutcome:
        source = list(messages)

        def operation() -> CompactionOutcome:
            extension_result = self._before_compact(
                source,
                reason="overflow",
                will_retry=True,
            )
            extension_compaction = self._extension_compaction_payload(extension_result)
            if extension_compaction is not None:
                output, record, _entry = self._apply_extension_compaction(
                    extension_compaction,
                    source_messages=source,
                    trigger="overflow",
                    reason="overflow",
                    will_retry=True,
                )
                return CompactionOutcome(
                    messages=output,
                    compressed=True,
                    recovered=True,
                    result=record,
                    will_retry=True,
                )
            compressor_context = to_compressor_context(source)
            compacted, recovered = self.manager.recover_overflow(
                compressor_context.messages,
                durable=self._adapter.is_persistent,
            )
            if not recovered:
                output = self._adapter.replace_messages(source)
                return CompactionOutcome(
                    messages=output,
                    compressed=False,
                    recovered=False,
                    result=(
                        self.manager.last_compression_result
                        if self.manager.compressor._last_compress_aborted
                        else None
                    ),
                    aborted=bool(self.manager.compressor._last_compress_aborted),
                )
            result = self.manager.last_compression_result
            output = self._adapter.apply_result(
                compacted,
                result,
                source_messages=source,
                source_indices=compressor_context.source_indices,
            )
            self._emit_builtin_compaction(reason="overflow", will_retry=True)
            return CompactionOutcome(
                messages=output,
                compressed=True,
                recovered=True,
                result=output,
                will_retry=True,
            )

        try:
            outcome = self._transaction(
                reason="overflow",
                operation=operation,
                failure_prefix="Context overflow recovery failed",
            )
        except CompactionCancelledError:
            outcome = CompactionOutcome(messages=source, compressed=False, recovered=False)
        if outcome.recovered:
            self._continue_agent(stream_fn=stream_fn)
        return outcome

    def compact_error_context(
        self,
        messages: Sequence[AgentMessage],
        *,
        retain_source_suffix: bool = True,
    ) -> CompactionOutcome:
        source = list(messages)
        compressor_context = to_compressor_context(source)
        compressor_messages = compressor_context.messages
        before_compressions = self.manager.compressor.compression_count

        def operation() -> CompactionOutcome:
            extension_result = self._before_compact(
                source,
                reason="threshold",
                will_retry=False,
            )
            extension_compaction = self._extension_compaction_payload(extension_result)
            if extension_compaction is not None:
                output, record, _entry = self._apply_extension_compaction(
                    extension_compaction,
                    source_messages=source,
                    trigger="error_context",
                    reason="threshold",
                    will_retry=False,
                )
                return CompactionOutcome(messages=output, compressed=True, result=record)
            compacted = self.manager.maybe_compress_error_context(
                compressor_messages,
                durable=self._adapter.is_persistent,
                retain_recent=retain_source_suffix,
            )
            result = self.manager.last_compression_result
            if compacted is not compressor_messages:
                output = self._adapter.apply_result(
                    compacted,
                    result,
                    source_messages=source,
                    source_indices=compressor_context.source_indices,
                    retain_source_suffix=retain_source_suffix,
                )
            else:
                output = compacted
            compressed = self.manager.compressor.compression_count > before_compressions
            if compressed:
                self._emit_builtin_compaction(reason="threshold", will_retry=False)
            return CompactionOutcome(
                messages=output,
                compressed=compressed,
                result=(
                    self._adapter.messages
                    if compressed
                    else self.manager.last_compression_result
                    if self.manager.compressor._last_compress_aborted
                    else None
                ),
                aborted=bool(self.manager.compressor._last_compress_aborted),
            )

        try:
            return self._transaction(reason="threshold", operation=operation, failure_prefix="Auto-compaction failed")
        except CompactionCancelledError:
            return CompactionOutcome(messages=source, compressed=False)

    def _should_compact_preflight(self, messages: list[AgentMessage]) -> bool:
        if self.manager.awaiting_real_usage_after_compression:
            return False
        tokens = estimate_tokens(messages)
        if self.manager.compressor.should_defer_preflight_to_real_usage(tokens):
            return False
        return self.manager.compressor.should_compress(tokens)

    def _before_compact(
        self,
        messages: Sequence[AgentMessage],
        *,
        reason: str,
        will_retry: bool,
        custom_instructions: str | None = None,
    ) -> object | None:
        runner = self._extension_runner
        has_handlers = getattr(runner, "has_handlers", None)
        emit = getattr(runner, "emit", None)
        if not callable(has_handlers) or not has_handlers("session_before_compact") or not callable(emit):
            return None
        source = list(messages)
        compressor_context = to_compressor_context(source)
        context_entry_ids = self._adapter.context_message_entry_ids()
        logical_entry_ids = [
            context_entry_ids[index]
            for index in compressor_context.source_indices
            if 0 <= index < len(context_entry_ids)
        ]
        preparation = prepare_compaction(
            compressor_context.messages,
            self.manager.compressor,
            logical_entry_ids,
        )
        result = emit(
            {
                "type": "session_before_compact",
                "preparation": preparation.as_extension_event(),
                "branchEntries": list(self._branch_entries()),
                "customInstructions": custom_instructions,
                "reason": reason,
                "willRetry": will_retry,
                "signal": self._signal(),
            }
        )
        if isinstance(result, dict) and result.get("cancel") is True:
            raise CompactionCancelledError("Compaction cancelled")
        return result

    @staticmethod
    def _extension_compaction_payload(result: object) -> dict[str, object] | None:
        if not isinstance(result, dict) or not isinstance(result.get("compaction"), dict):
            return None
        compaction = dict(result["compaction"])
        summary = compaction.get("summary")
        first_kept = compaction.get("firstKeptEntryId")
        tokens_before = compaction.get("tokensBefore")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Extension compaction summary must be a non-empty string")
        if not isinstance(first_kept, str):
            raise ValueError("Extension compaction firstKeptEntryId must be a string")
        if not isinstance(tokens_before, int) or isinstance(tokens_before, bool) or tokens_before < 0:
            raise ValueError("Extension compaction tokensBefore must be a non-negative integer")
        return compaction

    def _apply_extension_compaction(
        self,
        compaction: dict[str, object],
        *,
        source_messages: Sequence[AgentMessage],
        trigger: str,
        reason: str,
        will_retry: bool,
    ) -> tuple[list[AgentMessage], object, dict[str, object]]:
        output, entry = self._adapter.apply_extension_compaction(
            compaction,
            source_messages=source_messages,
        )
        record = self.manager.record_extension_compaction(
            output,
            summary=str(compaction["summary"]),
            tokens_before=int(compaction["tokensBefore"]),
            details=compaction.get("details"),
            trigger=trigger,
        )
        self._emit_session_compact(
            entry,
            from_extension=True,
            reason=reason,
            will_retry=will_retry,
        )
        return output, record, entry

    def _emit_session_compact(
        self,
        entry: dict[str, object],
        *,
        from_extension: bool,
        reason: str,
        will_retry: bool,
    ) -> None:
        runner = self._extension_runner
        has_handlers = getattr(runner, "has_handlers", None)
        emit = getattr(runner, "emit", None)
        if callable(has_handlers) and has_handlers("session_compact") and callable(emit):
            emit(
                {
                    "type": "session_compact",
                    "compactionEntry": dict(entry),
                    "fromExtension": from_extension,
                    "reason": reason,
                    "willRetry": will_retry,
                }
            )

    def _emit_builtin_compaction(self, *, reason: str, will_retry: bool) -> None:
        result = self.manager.last_compression_result
        entry = self._adapter.latest_compaction_entry() or {
            "type": "compaction",
            "summary": getattr(result, "summary", "") or "",
            "firstKeptEntryId": "",
            "tokensBefore": int(getattr(result, "tokens_before", 0) or 0),
            "details": merge_summary_model_compaction_details(
                getattr(result, "details", None),
                result,
            ),
        }
        self._emit_session_compact(
            entry,
            from_extension=False,
            reason=reason,
            will_retry=will_retry,
        )

    def _transaction(
        self,
        *,
        reason: str,
        operation: Callable[[], CompactionOutcome],
        failure_prefix: str,
        include_noop_result: bool = False,
    ) -> CompactionOutcome:
        self._adapter.begin(reason)
        try:
            outcome = operation()
        except Exception as error:
            message = str(error)
            aborted = message == "Compaction cancelled"
            self._adapter.end(
                reason=reason,
                result=None,
                aborted=aborted,
                will_retry=False,
                error_message=None if aborted else f"{failure_prefix}: {message}",
            )
            raise
        self._adapter.end(
            reason=reason,
            result=(
                outcome.result
                if outcome.compressed or include_noop_result or outcome.aborted
                else None
            ),
            aborted=outcome.aborted,
            will_retry=outcome.will_retry,
        )
        return outcome


__all__ = [
    "CompactionCancelledError",
    "CompactionCoordinator",
    "CompactionDeferredError",
    "CompactionOutcome",
    "CompactionTransactionCoordinator",
]
