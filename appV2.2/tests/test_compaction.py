from __future__ import annotations

from appv22.compaction import SUMMARY_PREFIX, ContextCompressor, estimate_tokens
from appv22.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)


def _user(text: str) -> UserMessage:
    return UserMessage(content=text, timestamp=now_ms())


def _assistant(text: str = "", tool_calls=None) -> AssistantMessage:
    content = [TextContent(text=text)] if text else []
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(
        content=content, api="faux", provider="faux", model="m", usage=empty_usage(), stop_reason="stop", timestamp=now_ms()
    )


def _tool_result(text: str, name: str = "read") -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id="c", tool_name=name, content=[TextContent(text=text)], is_error=False, timestamp=now_ms()
    )


def test_prune_dedups_identical_tool_outputs() -> None:
    big = "X" * 400
    messages = [_user("q"), _tool_result(big), _assistant("a"), _tool_result(big), _user("u"), _assistant("b")]
    compressor = ContextCompressor(protect_last_n=2)
    pruned = compressor.prune_old_tool_results(messages)
    texts = [pruned[1].content[0].text, pruned[3].content[0].text]
    assert any("Duplicate tool output" in t for t in texts)


def test_prune_summarizes_old_tool_result_and_truncates_args() -> None:
    big = "Y" * 500
    big_args = {"data": "Z" * 600}
    messages = [
        _user("q"),
        _assistant("call", tool_calls=[ToolCall(id="c1", name="write", arguments=big_args)]),
        _tool_result(big),
        _user("u1"), _user("u2"), _user("u3"), _user("u4"), _user("u5"), _user("u6"), _user("u7"), _user("u8"),
    ]
    compressor = ContextCompressor(protect_last_n=2)
    pruned = compressor.prune_old_tool_results(messages)
    assert "elided" in pruned[2].content[0].text
    assert pruned[1].content[1].arguments == {"_truncated": "612 chars of arguments elided"}


def test_should_compress_threshold_and_antithrash() -> None:
    compressor = ContextCompressor(context_length=1000, threshold_percent=0.5)
    assert compressor.should_compress(400) is False
    assert compressor.should_compress(600) is True
    compressor._ineffective_compression_count = 2
    assert compressor.should_compress(600) is False


def test_compress_assembles_head_summary_tail() -> None:
    messages = [_user("first goal")]
    for i in range(20):
        messages.append(_assistant(f"long assistant message number {i} " * 10))
        messages.append(_user(f"user follow up {i} " * 10))
    messages.append(_user("latest request"))
    compressor = ContextCompressor(context_length=4000, protect_first_n=1, protect_last_n=4)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nGoal text\n## Completed Actions\nDid things")
    assert result.compressed is True
    summary_messages = [m for m in result.messages if getattr(m, "role", None) == "user" and str(getattr(m, "content", "")).startswith(SUMMARY_PREFIX)]
    assert len(summary_messages) == 1
    assert result.messages[0].content == "first goal"  # head preserved
    assert getattr(result.messages[-1], "content", "") == "latest request"  # tail preserved


def test_iterative_update_uses_previous_summary() -> None:
    messages = []
    messages.append(_user("goal"))
    for i in range(20):
        messages.append(_assistant(f"assistant {i} " * 20))
        messages.append(_user(f"user {i} " * 20))
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "## Goal\nupdated"

    compressor = ContextCompressor(context_length=3000, protect_first_n=1, protect_last_n=4)
    compressor.compress(messages, summarizer=summarizer)
    compressor.compress(messages, summarizer=summarizer)
    assert "EXISTING SUMMARY" in seen_prompts[1]


def test_estimate_tokens_counts_text() -> None:
    messages = [_user("a" * 40)]
    assert estimate_tokens(messages) == 10
