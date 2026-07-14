from __future__ import annotations

from pathlib import Path

from travis.compaction import CompressionResult, CompactionManager, ContextCompressor, SessionLineage, estimate_tokens
from travis.ai.types import UserMessage, now_ms


def _big_messages(n: int = 40, size: int = 200) -> list:
    msgs = [UserMessage(content="goal", timestamp=now_ms())]
    for i in range(n):
        msgs.append(UserMessage(content=(f"m{i} " * size), timestamp=now_ms()))
    msgs.append(UserMessage(content="latest", timestamp=now_ms()))
    return msgs


def _summarizer(prompt: str) -> str:
    return "## Goal\nshort"


def _message_with_tokens(tokens: int) -> UserMessage:
    return UserMessage(content="x" * (tokens * 4), timestamp=now_ms())


def _manager(**kwargs) -> CompactionManager:
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    return CompactionManager(compressor, summarizer=_summarizer, **kwargs)


def test_preflight_compresses_over_threshold_then_defers() -> None:
    manager = _manager()
    messages = _big_messages()
    out = manager.maybe_compress_preflight(messages)
    assert len(out) < len(messages)
    assert manager.awaiting_real_usage_after_compression is True
    assert manager.compressor._verify_compaction_cleared_threshold is True
    # second preflight defers (awaiting real usage), returns unchanged
    out2 = manager.maybe_compress_preflight(out)
    assert out2 is out


def test_manager_exposes_last_compression_result_read_only() -> None:
    manager = _manager()

    manager.maybe_compress_preflight(_big_messages())

    assert manager.last_compression_result is not None
    assert manager.last_compression_result.compressed is True
    try:
        manager.last_compression_result = None
    except AttributeError:
        pass
    else:
        raise AssertionError("last_compression_result must be read-only")


def test_preflight_records_compression_ledger_entry() -> None:
    manager = _manager()
    messages = _big_messages()

    out = manager.maybe_compress_preflight(messages)

    entry = manager.compression_ledger[-1]
    assert entry.trigger == "preflight"
    assert entry.tokens_before == estimate_tokens(messages)
    assert entry.tokens_after == estimate_tokens(out)
    assert entry.compressed is True
    assert entry.estimated_after is True
    assert entry.summary_fallback is False
    assert entry.summary_model_requested == "main"
    assert entry.summary_model_used == "main"
    assert entry.summary_model_fallback is False
    assert entry.summary_model_error is None
    assert entry.stop_reason is None
    assert entry.first_kept_message_index is not None
    assert entry.error is None


def test_preflight_compacts_once_and_preserves_latest_turn_for_next_call() -> None:
    calls: list[str] = []

    def summarizer(prompt: str) -> str:
        calls.append(prompt)
        return "## Goal\nshort"

    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    manager = CompactionManager(compressor, summarizer=summarizer)
    messages = _big_messages()

    out = manager.maybe_compress_preflight(messages)
    out2 = manager.maybe_compress_preflight(out)

    assert len(out) < len(messages)
    assert out[-1].content == "latest"
    assert out2 is out
    assert len(calls) == 1
    assert compressor.compression_count == 1


def test_preflight_below_threshold_does_not_call_summarizer() -> None:
    calls: list[str] = []
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    manager = CompactionManager(
        compressor,
        summarizer=lambda prompt: calls.append(prompt) or "## Goal\nshould not run",
    )
    messages = [UserMessage(content="goal", timestamp=now_ms()), UserMessage(content="latest", timestamp=now_ms())]

    out = manager.maybe_compress_preflight(messages)

    assert out is messages
    assert calls == []
    assert compressor.compression_count == 0


def test_post_response_sentinel_minus_one_treated_as_zero() -> None:
    manager = _manager()
    messages = _big_messages()
    out = manager.maybe_compress_post_response(messages, prompt_tokens=-1)
    assert out is messages  # 0 tokens -> below threshold -> no compress
    assert manager.awaiting_real_usage_after_compression is False


def test_post_response_real_tokens_compresses() -> None:
    manager = _manager()
    messages = _big_messages()
    out = manager.maybe_compress_post_response(messages, prompt_tokens=5000)
    assert len(out) < len(messages)


def test_post_response_compaction_noops_on_immediate_recheck_after_summary() -> None:
    calls: list[str] = []

    def summarizer(prompt: str) -> str:
        calls.append(prompt)
        return "## Goal\nshort"

    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    manager = CompactionManager(compressor, summarizer=summarizer)
    messages = _big_messages()

    first = manager.maybe_compress_post_response(messages, prompt_tokens=5000)
    second = manager.maybe_compress_post_response(first, prompt_tokens=5000)

    assert len(first) < len(messages)
    assert second is first
    assert len(calls) == 1
    assert compressor.compression_count == 1
    assert first[-1].content == "latest"


def test_compressor_update_from_response_tracks_real_usage_for_deferral() -> None:
    compressor = ContextCompressor(context_length=100_000, threshold_percent=0.5)
    compressor.awaiting_real_usage_after_compression = True
    compressor.last_compression_rough_tokens = 90_000

    compressor.update_from_response({
        "prompt_tokens": 5_000,
        "completion_tokens": 1_000,
        "total_tokens": 6_000,
    })

    assert compressor.last_prompt_tokens == 5_000
    assert compressor.last_completion_tokens == 1_000
    assert compressor.last_total_tokens == 6_000
    assert compressor.last_real_prompt_tokens == 5_000
    assert compressor.last_rough_tokens_when_real_prompt_fit == 90_000
    assert compressor.awaiting_real_usage_after_compression is False
    assert compressor.should_defer_preflight_to_real_usage(93_000) is True
    assert compressor.last_rough_tokens_when_real_prompt_fit == 93_000
    assert compressor.should_defer_preflight_to_real_usage(100_000) is False


def test_post_compaction_real_usage_above_threshold_records_one_ineffective_attempt() -> None:
    compressor = ContextCompressor(context_length=100_000, threshold_percent=0.5)
    compressor._verify_compaction_cleared_threshold = True
    compressor.awaiting_real_usage_after_compression = True

    compressor.update_from_response({
        "prompt_tokens": 80_000,
        "completion_tokens": 1_000,
        "total_tokens": 81_000,
    })

    assert compressor._ineffective_compression_count == 1
    assert compressor._verify_compaction_cleared_threshold is False
    assert compressor.awaiting_real_usage_after_compression is False


def test_post_compaction_real_usage_below_threshold_resets_ineffective_attempts() -> None:
    compressor = ContextCompressor(context_length=100_000, threshold_percent=0.5)
    compressor._ineffective_compression_count = 1
    compressor._verify_compaction_cleared_threshold = True
    compressor.awaiting_real_usage_after_compression = True
    compressor.last_compression_rough_tokens = 70_000

    compressor.update_from_response({
        "prompt_tokens": 40_000,
        "completion_tokens": 1_000,
        "total_tokens": 41_000,
    })

    assert compressor._ineffective_compression_count == 0
    assert compressor.last_rough_tokens_when_real_prompt_fit == 70_000
    assert compressor._verify_compaction_cleared_threshold is False
    assert compressor.awaiting_real_usage_after_compression is False


def test_usage_less_response_consumes_pending_compaction_verdict_without_a_strike() -> None:
    compressor = ContextCompressor(context_length=100_000, threshold_percent=0.5)
    compressor._verify_compaction_cleared_threshold = True
    compressor.awaiting_real_usage_after_compression = True

    compressor.update_from_response({})

    assert compressor._ineffective_compression_count == 0
    assert compressor._verify_compaction_cleared_threshold is False
    assert compressor.awaiting_real_usage_after_compression is False


def test_preflight_defers_once_while_waiting_for_post_compaction_real_usage() -> None:
    compressor = ContextCompressor(context_length=100_000, threshold_percent=0.5)
    compressor.awaiting_real_usage_after_compression = True
    compressor.last_real_prompt_tokens = 80_000

    assert compressor.should_defer_preflight_to_real_usage(90_000) is True


def test_preflight_defers_after_real_usage_proved_rough_estimate_noisy() -> None:
    compressor = ContextCompressor(context_length=100_000, threshold_percent=0.5, protect_first_n=1, protect_last_n=4)
    manager = CompactionManager(compressor, summarizer=_summarizer)
    messages = _big_messages(n=20, size=6200)
    rough_tokens = estimate_tokens(messages)
    assert rough_tokens > compressor.threshold_tokens

    compressor.last_real_prompt_tokens = 5_000
    compressor.last_rough_tokens_when_real_prompt_fit = rough_tokens - 1_000

    out = manager.maybe_compress_preflight(messages)

    assert out is messages
    assert compressor.last_rough_tokens_when_real_prompt_fit == rough_tokens
    assert compressor.compression_count == 0


def test_overflow_recovery_force_and_bounded() -> None:
    manager = _manager(max_overflow_attempts=2)
    small = [UserMessage(content="a", timestamp=now_ms()), UserMessage(content="b", timestamp=now_ms()),
             UserMessage(content="c", timestamp=now_ms()), UserMessage(content="d", timestamp=now_ms()),
             UserMessage(content="e", timestamp=now_ms()), UserMessage(content="f", timestamp=now_ms()),
             UserMessage(content="g", timestamp=now_ms()), UserMessage(content="h", timestamp=now_ms())]
    # force compresses even below threshold (only if there is a middle to compact)
    big = _big_messages()
    _, c1 = manager.recover_overflow(big)
    _, c2 = manager.recover_overflow(big)
    _, c3 = manager.recover_overflow(big)
    assert c1 is True and c2 is True
    assert c3 is False  # bounded at 2 attempts
    _ = small


def test_overflow_recovery_arms_post_compaction_real_usage_verification() -> None:
    manager = _manager(max_overflow_attempts=1)

    recovered_messages, recovered = manager.recover_overflow(_big_messages())

    assert recovered is True
    assert recovered_messages
    assert manager.awaiting_real_usage_after_compression is True
    assert manager.compressor._verify_compaction_cleared_threshold is True


def test_manual_compression_arms_post_compaction_real_usage_verification() -> None:
    manager = _manager()

    status = manager.compress_manual_with_status(_big_messages())

    assert status.compressed is True
    assert manager.awaiting_real_usage_after_compression is True
    assert manager.compressor._verify_compaction_cleared_threshold is True


def test_overflow_recovery_retries_once_with_already_compacted_transcript_and_stops() -> None:
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    manager = CompactionManager(compressor, summarizer=_summarizer, max_overflow_attempts=1)
    messages = _big_messages()

    compacted = manager.maybe_compress_preflight(messages)
    recovered_messages, recovered = manager.recover_overflow(compacted)
    second_messages, second_recovered = manager.recover_overflow(recovered_messages)

    assert compacted is not messages
    assert recovered is True
    assert recovered_messages is compacted
    assert second_recovered is False
    assert second_messages is recovered_messages
    assert manager.overflow_attempts == 1
    assert compressor.compression_count == 1


def test_manual_force_clears_cooldown() -> None:
    fake_time = {"t": 100.0}
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)

    def failing_summarizer(prompt: str) -> str:
        raise RuntimeError("summary model down")

    manager = CompactionManager(compressor, summarizer=failing_summarizer, clock=lambda: fake_time["t"])
    messages = _big_messages()
    # default behavior: summary failure inserts a deterministic fallback, not a manager-level no-op.
    out = manager.maybe_compress_preflight(messages)
    assert len(out) < len(messages)
    assert compressor._last_summary_fallback_used is True
    assert manager._in_cooldown() is False

    # manual force still bypasses and clears an existing cooldown.
    manager._summary_failure_cooldown_until = fake_time["t"] + 600
    manager._summarizer = _summarizer
    forced = manager.compress_manual(messages)
    assert len(forced) < len(messages)
    assert manager._in_cooldown() is False


def test_network_failed_compaction_ledger_records_abort_without_context_loss() -> None:
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    manager = CompactionManager(
        compressor,
        summarizer=lambda _prompt: (_ for _ in ()).throw(TimeoutError("provider timed out")),
    )
    messages = _big_messages()

    output = manager.maybe_compress_preflight(messages)
    entry = manager.compression_ledger[-1]

    assert output is messages
    assert entry.compressed is False
    assert entry.stop_reason == "aborted"
    assert entry.tokens_after == entry.tokens_before


def test_summary_failure_sets_compressor_cooldown_and_skips_retry() -> None:
    fake_time = {"t": 100.0}
    compressor = ContextCompressor(
        context_length=2000,
        protect_first_n=1,
        protect_last_n=4,
        clock=lambda: fake_time["t"],
    )
    messages = _big_messages()
    calls = {"count": 0}

    def failing_summarizer(prompt: str) -> str:
        calls["count"] += 1
        raise RuntimeError("summary provider down")

    first = compressor.compress(messages, summarizer=failing_summarizer)
    second = compressor.compress(messages, summarizer=failing_summarizer)

    assert first.compressed is True
    assert second.compressed is True
    assert calls["count"] == 1
    assert compressor._summary_failure_cooldown_until == fake_time["t"] + 600.0
    assert compressor._last_summary_fallback_used is True


def test_manager_level_summary_failure_uses_travis_long_cooldown() -> None:
    fake_time = {"t": 100.0}

    class RaisingCompressor(ContextCompressor):
        def compress(self, messages, summarizer=None, focus_topic=None, force=False):
            raise RuntimeError("manager-level failure")

    compressor = RaisingCompressor(
        context_length=2000,
        protect_first_n=1,
        protect_last_n=4,
        clock=lambda: fake_time["t"],
    )
    manager = CompactionManager(compressor, summarizer=_summarizer, clock=lambda: fake_time["t"])
    messages = _big_messages()

    out = manager.maybe_compress_preflight(messages)

    assert out is messages
    assert manager._summary_failure_cooldown_until == fake_time["t"] + 600.0


def test_manual_compression_force_clears_compressor_cooldown() -> None:
    fake_time = {"t": 100.0}
    compressor = ContextCompressor(
        context_length=2000,
        protect_first_n=1,
        protect_last_n=4,
        clock=lambda: fake_time["t"],
    )
    manager = CompactionManager(compressor, summarizer=_summarizer, clock=lambda: fake_time["t"])
    compressor._summary_failure_cooldown_until = fake_time["t"] + 999.0
    calls: list[str] = []

    def ok_summarizer(prompt: str) -> str:
        calls.append(prompt)
        return "## Historical Task Snapshot\nmanual retry worked"

    messages = _big_messages()
    forced = manager.compress_manual(messages, summarizer=ok_summarizer)

    assert len(forced) < len(messages)
    assert calls
    assert compressor._summary_failure_cooldown_until == 0.0
    assert compressor._last_summary_fallback_used is False


def test_manual_compression_focus_reaches_summary_prompt() -> None:
    seen_prompts: list[str] = []

    def focused_summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "## Historical Task Snapshot\nNone."

    manager = _manager()
    manager.compress_manual(_big_messages(), summarizer=focused_summarizer, focus="database schema")

    assert 'FOCUS TOPIC: "database schema"' in seen_prompts[0]
    assert "PRIORITISE preserving all information related to the focus topic above" in seen_prompts[0]
    assert "60-70% of the summary token budget" in seen_prompts[0]
    assert "NEVER preserve API keys, tokens, passwords, or credentials" in seen_prompts[0]


def test_manual_compression_status_reports_success_and_focus() -> None:
    manager = _manager()
    messages = _big_messages()

    status = manager.compress_manual_with_status(
        messages,
        summarizer=lambda prompt: "## Historical Task Snapshot\nmanual summary",
        focus="database schema",
    )

    assert status.noop is False
    assert status.compressed is True
    assert status.focus == "database schema"
    assert status.headline == f"Compressed: {len(messages)} → {len(status.messages)} messages"
    assert "Approx request size:" in status.token_line
    assert status.warning is None
    assert status.info is None


def test_manual_compression_noops_when_summary_would_increase_prompt_size() -> None:
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=2)
    manager = CompactionManager(compressor, summarizer=_summarizer)
    messages = [
        UserMessage(content=f"historical turn {index}", timestamp=now_ms())
        for index in range(24)
    ]
    before_tokens = estimate_tokens(messages)

    status = manager.compress_manual_with_status(
        messages,
        summarizer=lambda prompt: "## Historical Task Snapshot\n" + ("expanded manual summary " * 4_000),
    )

    assert status.compressed is False
    assert status.noop is True
    assert status.messages == messages
    assert estimate_tokens(status.messages) == before_tokens
    assert status.headline == f"No changes from compression: {len(messages)} messages"
    assert "unchanged" in status.token_line
    assert status.note == "Compression skipped because the generated summary would increase context size."
    assert compressor.compression_count == 0

    entry = manager.compression_ledger[-1]
    assert entry.trigger == "manual"
    assert entry.compressed is False
    assert entry.tokens_after == before_tokens
    assert entry.stop_reason == "context_size_increase"


def test_manual_compression_status_warns_when_compression_aborts() -> None:
    compressor = ContextCompressor(
        context_length=2000,
        protect_first_n=1,
        protect_last_n=4,
        abort_on_summary_failure=True,
    )
    manager = CompactionManager(compressor, summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))
    status = manager.compress_manual_with_status(_big_messages())

    assert status.compressed is False
    assert status.noop is True
    assert status.warning is not None
    assert "Compression aborted" in status.warning
    assert "down" in status.warning
    assert "No messages were dropped" in status.warning


def test_manual_compression_status_surfaces_manager_level_failure() -> None:
    class RaisingCompressor(ContextCompressor):
        def compress(self, messages, summarizer=None, focus_topic=None, force=False):
            raise RuntimeError("manager-level failure")

    manager = CompactionManager(RaisingCompressor(), summarizer=_summarizer)

    status = manager.compress_manual_with_status(_big_messages())

    assert status.compressed is False
    assert status.noop is True
    assert status.headline.startswith("Compression failed:")
    assert "No changes from compression" not in status.headline
    assert status.warning is not None
    assert "manager-level failure" in status.warning
    assert "No messages were dropped" in status.warning


def test_manual_compression_noop_reports_protected_recent_context() -> None:
    compressor = ContextCompressor(
        context_length=128_000,
        threshold_percent=0.80,
        protect_first_n=3,
        protect_last_n=20,
    )
    manager = CompactionManager(compressor, summarizer=_summarizer)
    messages = [UserMessage(content="latest huge only " + ("x" * 440_000), timestamp=now_ms())]
    assert estimate_tokens(messages) > compressor.threshold_tokens

    status = manager.compress_manual_with_status(messages)

    assert status.compressed is False
    assert status.noop is True
    assert status.note == "No compactable history; recent context is protected."

    entry = manager.compression_ledger[-1]
    assert entry.trigger == "manual"
    assert entry.tokens_before == estimate_tokens(messages)
    assert entry.tokens_after == estimate_tokens(status.messages)
    assert entry.compressed is False
    assert entry.estimated_after is False
    assert entry.summary_fallback is False
    assert entry.stop_reason == "protected_recent_context"
    assert entry.first_kept_message_index is None
    assert entry.error is None


def test_manual_deep_compression_uses_tighter_tail_boundary() -> None:
    messages = _big_messages(n=80, size=80)
    normal = CompactionManager(
        ContextCompressor(context_length=12_000, threshold_percent=0.5, protect_first_n=1, protect_last_n=20),
        summarizer=_summarizer,
    ).compress_manual_with_status(messages)
    deep = CompactionManager(
        ContextCompressor(context_length=12_000, threshold_percent=0.5, protect_first_n=1, protect_last_n=20),
        summarizer=_summarizer,
    ).compress_manual_with_status(messages, deep=True)

    assert normal.compressed is True
    assert deep.compressed is True
    assert normal.deep is False
    assert deep.deep is True
    assert deep.first_kept_message_index > normal.first_kept_message_index
    assert len(deep.messages) < len(normal.messages)
    assert estimate_tokens(deep.messages) < estimate_tokens(normal.messages)


def test_manual_deep_compression_loops_until_minimum_target() -> None:
    class SequenceCompressor(ContextCompressor):
        def __init__(self) -> None:
            super().__init__(context_length=128_000)
            self.calls: list[bool] = []
            self.outputs = [20_000, 10_000, 3_000]

        def compress(self, messages, summarizer=None, focus_topic=None, force=False, deep=False):
            self.calls.append(bool(deep))
            tokens = self.outputs[len(self.calls) - 1]
            return CompressionResult(
                messages=[_message_with_tokens(tokens)],
                compressed=True,
                savings_pct=50.0,
                summary=f"pass {len(self.calls)}",
                tokens_before=estimate_tokens(messages),
                first_kept_message_index=0,
            )

    compressor = SequenceCompressor()
    manager = CompactionManager(compressor, summarizer=_summarizer)

    status = manager.compress_manual_with_status([_message_with_tokens(40_000)], deep=True)

    assert compressor.calls == [True, True, True]
    assert status.compressed is True
    assert status.deep is True
    assert status.compression_passes == 3
    assert status.deep_stop_reason == "target_reached"
    assert status.target_tokens == 6_400
    assert estimate_tokens(status.messages) == 3_000
    assert "Deep compression: 3 passes" in status.note
    assert "stopped near baseline target" in status.note


def test_manual_deep_compression_stops_when_passes_stop_making_progress() -> None:
    class SequenceCompressor(ContextCompressor):
        def __init__(self) -> None:
            super().__init__(context_length=32_000)
            self.calls: list[bool] = []
            self.outputs = [10_000, 9_700, 4_000]

        def compress(self, messages, summarizer=None, focus_topic=None, force=False, deep=False):
            self.calls.append(bool(deep))
            tokens = self.outputs[len(self.calls) - 1]
            return CompressionResult(
                messages=[_message_with_tokens(tokens)],
                compressed=True,
                savings_pct=3.0,
                summary=f"pass {len(self.calls)}",
                tokens_before=estimate_tokens(messages),
                first_kept_message_index=0,
            )

    compressor = SequenceCompressor()
    manager = CompactionManager(compressor, summarizer=_summarizer)

    status = manager.compress_manual_with_status([_message_with_tokens(16_000)], deep=True)

    assert compressor.calls == [True, True]
    assert status.compression_passes == 2
    assert status.deep_stop_reason == "insufficient_progress"


def test_manual_deep_compression_stops_at_safety_limit() -> None:
    class SequenceCompressor(ContextCompressor):
        def __init__(self) -> None:
            super().__init__(context_length=100_000)
            self.calls: list[bool] = []
            self.tokens = [30_000, 20_000, 10_000, 8_000]

        def compress(self, messages, summarizer=None, focus_topic=None, force=False, deep=False):
            self.calls.append(bool(deep))
            next_tokens = self.tokens.pop(0)
            before = estimate_tokens(messages)
            self.compression_count += 1
            return CompressionResult(
                messages=[_message_with_tokens(next_tokens)],
                compressed=True,
                savings_pct=max(0.0, ((before - next_tokens) / before) * 100.0),
                tokens_before=before,
                first_kept_message_index=0,
            )

    compressor = SequenceCompressor()
    manager = CompactionManager(compressor, summarizer=_summarizer)

    status = manager.compress_manual_with_status([_message_with_tokens(40_000)], deep=True)

    assert compressor.calls == [True, True, True, True]
    assert status.deep is True
    assert status.compressed is True
    assert status.compression_passes == 4
    assert status.deep_stop_reason == "max_passes"
    assert status.target_tokens == 5_000
    assert "4-pass safety limit" in status.note


def test_manual_deep_compression_stops_at_safety_limit_duplicate_guard() -> None:
    class SequenceCompressor(ContextCompressor):
        def __init__(self) -> None:
            super().__init__(context_length=100_000)
            self.calls: list[bool] = []
            self.tokens = [30_000, 20_000, 10_000, 8_000]

        def compress(self, messages, summarizer=None, focus_topic=None, force=False, deep=False):
            self.calls.append(bool(deep))
            next_tokens = self.tokens.pop(0)
            before = estimate_tokens(messages)
            self.compression_count += 1
            return CompressionResult(
                messages=[_message_with_tokens(next_tokens)],
                compressed=True,
                savings_pct=max(0.0, ((before - next_tokens) / before) * 100.0),
                tokens_before=before,
                first_kept_message_index=0,
            )

    compressor = SequenceCompressor()
    manager = CompactionManager(compressor, summarizer=_summarizer)

    status = manager.compress_manual_with_status([_message_with_tokens(40_000)], deep=True)

    assert compressor.calls == [True, True, True, True]
    assert status.deep is True
    assert status.compressed is True
    assert status.compression_passes == 4
    assert status.deep_stop_reason == "max_passes"
    assert status.target_tokens == 5_000
    assert "4-pass safety limit" in status.note


def test_manual_compression_noops_when_existing_summary_has_no_new_middle_turns() -> None:
    compressor = ContextCompressor(
        context_length=30_000,
        threshold_percent=0.5,
        protect_first_n=3,
        protect_last_n=20,
    )
    manager = CompactionManager(
        compressor,
        summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("summary provider rejected first pass")),
    )
    messages = _big_messages(n=40, size=200)
    first = manager.compress_manual_with_status(messages)
    calls: list[str] = []

    def expanding_summarizer(prompt: str) -> str:
        calls.append(prompt)
        return "## Historical Task Snapshot\n" + ("expanded successful summary " * 500)

    before_tokens = estimate_tokens(first.messages)
    second = manager.compress_manual_with_status(first.messages, summarizer=expanding_summarizer)

    assert first.compressed is True
    assert first.warning is not None
    assert second.compressed is False
    assert second.noop is True
    assert second.messages == first.messages
    assert estimate_tokens(second.messages) == before_tokens
    assert calls == []


def test_session_lineage_rotation() -> None:
    ids = iter(["s1", "s2", "s3"])
    lineage = SessionLineage(id_factory=lambda: next(ids))
    assert lineage.current.id == "s1"
    rotated = lineage.rotate(reason="compression")
    assert rotated.id == "s2"
    assert rotated.parent_session_id == "s1"
    assert lineage.history[0].end_reason == "compression"
    lineage.rotate()
    assert lineage.lineage() == ["s1", "s2", "s3"]


def test_session_lineage_persists_parent_chain_across_reload(tmp_path: Path) -> None:
    from travis.compaction import SessionLineageStore

    ids = iter(["s1", "s2", "s3"])
    store = SessionLineageStore(tmp_path / "state.db")
    lineage = SessionLineage(id_factory=lambda: next(ids), store=store)

    lineage.rotate(reason="compression")
    lineage.rotate(reason="compression")

    reloaded = SessionLineage.load(
        SessionLineageStore(tmp_path / "state.db"),
        current_id=lineage.current.id,
    )

    assert reloaded.current.id == "s3"
    assert reloaded.current.parent_session_id == "s2"
    assert reloaded.lineage() == ["s1", "s2", "s3"]
    assert [record.end_reason for record in reloaded.history] == ["compression", "compression", None]
