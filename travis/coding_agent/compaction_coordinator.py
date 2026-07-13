"""Coordinates manual compaction with the active agent run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from travis.agent.agent import Agent
from travis.agent.types import AgentMessage
from travis.coding_agent.compaction_adapter import SessionCompactionAdapter, to_compressor_messages
from travis.compaction.compressor import estimate_tokens
from travis.compaction.timing import CompactionManager


class CompactionDeferredError(RuntimeError):
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


class CompactionTransactionCoordinator:
    """Pairs lifecycle, manager execution, persistence, and error reporting."""

    def __init__(
        self,
        *,
        manager: CompactionManager,
        run_coordinator: CompactionCoordinator,
        adapter: SessionCompactionAdapter,
        continue_agent: Callable[..., object],
    ) -> None:
        self.manager = manager
        self._run_coordinator = run_coordinator
        self._adapter = adapter
        self._continue_agent = continue_agent

    def manual(self, focus: str | None = None, summarizer=None, deep: bool = False):
        if self._run_coordinator.prepare() == "deferred":
            raise CompactionDeferredError("Compaction deferred until the active run completes")
        source = list(self._adapter.messages)

        def operation() -> CompactionOutcome:
            status = self.manager.compress_manual_with_status(
                to_compressor_messages(source),
                summarizer=summarizer,
                focus=focus,
                deep=deep,
            )
            self._adapter.apply_manual_status(status, source)
            return CompactionOutcome(
                messages=status.messages,
                compressed=bool(status.compressed),
                result=status,
            )

        return self._transaction(
            reason="manual",
            operation=operation,
            failure_prefix="Compaction failed",
            include_noop_result=True,
        ).result

    def preflight(self, messages: list[AgentMessage]) -> CompactionOutcome:
        source = list(messages)
        compressor_messages = to_compressor_messages(source)
        should_emit = self._should_compact_preflight(compressor_messages)
        before_compressions = self.manager.compressor.compression_count

        def operation() -> CompactionOutcome:
            compacted = self.manager.maybe_compress_preflight(compressor_messages)
            if compacted is not compressor_messages:
                result = self.manager.last_compression_result
                applied = self._adapter.apply_result(compacted, result, source_messages=source)
                messages[:] = applied
                output = messages
            else:
                output = compacted
            compressed = self.manager.compressor.compression_count > before_compressions
            return CompactionOutcome(
                messages=output,
                compressed=compressed,
                result=self._adapter.messages if compressed else None,
            )

        if not should_emit:
            return operation()
        return self._transaction(reason="threshold", operation=operation, failure_prefix="Auto-compaction failed")

    def post_response(
        self,
        messages: Sequence[AgentMessage],
        prompt_tokens: int,
    ) -> CompactionOutcome:
        source = list(messages)
        compressor_messages = to_compressor_messages(source)
        real_tokens = 0 if prompt_tokens == -1 else prompt_tokens
        should_emit = self.manager.compressor.should_compress(real_tokens)
        before_compressions = self.manager.compressor.compression_count

        def operation() -> CompactionOutcome:
            compacted = self.manager.maybe_compress_post_response(compressor_messages, prompt_tokens)
            if compacted is not compressor_messages:
                result = self.manager.last_compression_result
                output = self._adapter.apply_result(compacted, result, source_messages=source)
            else:
                output = compacted
            compressed = self.manager.compressor.compression_count > before_compressions
            return CompactionOutcome(
                messages=output,
                compressed=compressed,
                result=self._adapter.messages if compressed else None,
            )

        try:
            if not should_emit:
                return operation()
            return self._transaction(reason="threshold", operation=operation, failure_prefix="Auto-compaction failed")
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
            compacted, recovered = self.manager.recover_overflow(to_compressor_messages(source))
            if not recovered:
                output = self._adapter.replace_messages(source)
                return CompactionOutcome(messages=output, compressed=False, recovered=False)
            result = self.manager.last_compression_result
            output = self._adapter.apply_result(compacted, result, source_messages=source)
            return CompactionOutcome(
                messages=output,
                compressed=True,
                recovered=True,
                result=output,
                will_retry=True,
            )

        outcome = self._transaction(
            reason="overflow",
            operation=operation,
            failure_prefix="Context overflow recovery failed",
        )
        if outcome.recovered:
            self._continue_agent(stream_fn=stream_fn)
        return outcome

    def compact_error_context(
        self,
        messages: Sequence[AgentMessage],
        *,
        force: bool,
        retain_source_suffix: bool = True,
    ) -> CompactionOutcome:
        source = list(messages)
        compressor_messages = to_compressor_messages(source)
        before_compressions = self.manager.compressor.compression_count

        def operation() -> CompactionOutcome:
            try:
                compacted = (
                    self.manager.force_compress_error_context(compressor_messages)
                    if force
                    else self.manager.maybe_compress_error_context(compressor_messages)
                )
                result = self.manager.last_compression_result
                if compacted is not compressor_messages or force:
                    output = self._adapter.apply_result(
                        compacted,
                        result,
                        source_messages=source,
                        retain_source_suffix=retain_source_suffix,
                    )
                else:
                    output = compacted
            except Exception:
                if force:
                    self._adapter.replace_messages(source)
                raise
            compressed = self.manager.compressor.compression_count > before_compressions
            return CompactionOutcome(
                messages=output,
                compressed=compressed,
                result=self._adapter.messages if compressed else (source if force else None),
            )

        return self._transaction(reason="threshold", operation=operation, failure_prefix="Auto-compaction failed")

    def _should_compact_preflight(self, messages: list[AgentMessage]) -> bool:
        if self.manager.awaiting_real_usage_after_compression:
            return False
        tokens = estimate_tokens(messages)
        if self.manager.compressor.should_defer_preflight_to_real_usage(tokens):
            return False
        return self.manager.compressor.should_compress(tokens)

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
            result=outcome.result if outcome.compressed or include_noop_result else None,
            aborted=False,
            will_retry=outcome.will_retry,
        )
        return outcome


__all__ = [
    "CompactionCoordinator",
    "CompactionDeferredError",
    "CompactionOutcome",
    "CompactionTransactionCoordinator",
]
