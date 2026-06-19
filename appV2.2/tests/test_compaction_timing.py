from __future__ import annotations

from appv22.compaction import CompactionManager, ContextCompressor, SessionLineage
from appv22.ai.types import UserMessage, now_ms


def _big_messages(n: int = 40, size: int = 200) -> list:
    msgs = [UserMessage(content="goal", timestamp=now_ms())]
    for i in range(n):
        msgs.append(UserMessage(content=(f"m{i} " * size), timestamp=now_ms()))
    msgs.append(UserMessage(content="latest", timestamp=now_ms()))
    return msgs


def _summarizer(prompt: str) -> str:
    return "## Goal\nshort"


def _manager(**kwargs) -> CompactionManager:
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)
    return CompactionManager(compressor, summarizer=_summarizer, **kwargs)


def test_preflight_compresses_over_threshold_then_defers() -> None:
    manager = _manager()
    messages = _big_messages()
    out = manager.maybe_compress_preflight(messages)
    assert len(out) < len(messages)
    assert manager.awaiting_real_usage_after_compression is True
    # second preflight defers (awaiting real usage), returns unchanged
    out2 = manager.maybe_compress_preflight(out)
    assert out2 is out


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


def test_manual_force_clears_cooldown() -> None:
    fake_time = {"t": 100.0}
    compressor = ContextCompressor(context_length=2000, protect_first_n=1, protect_last_n=4)

    def failing_summarizer(prompt: str) -> str:
        raise RuntimeError("summary model down")

    manager = CompactionManager(compressor, summarizer=failing_summarizer, clock=lambda: fake_time["t"])
    messages = _big_messages()
    # preflight hits summarizer failure -> cooldown set, no compress
    out = manager.maybe_compress_preflight(messages)
    assert out is messages
    assert manager._in_cooldown() is True
    # within cooldown, preflight skips even over threshold
    assert manager.maybe_compress_preflight(messages) is messages
    # manual force bypasses cooldown (and with a working summarizer compresses)
    manager._summarizer = _summarizer
    forced = manager.compress_manual(messages)
    assert len(forced) < len(messages)
    assert manager._in_cooldown() is False


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
