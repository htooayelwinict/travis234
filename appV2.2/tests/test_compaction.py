from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from appv22.ai.providers.appv2_env import convert_messages
from appv22.compaction import COMPRESSED_SUMMARY_METADATA_KEY, SUMMARY_PREFIX, ContextCompressor, estimate_tokens
from appv22.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)

EXPECTED_SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"


@dataclass
class _SystemMessage:
    content: str
    timestamp: int = field(default_factory=now_ms)
    role: Literal["system"] = "system"


def _system(text: str) -> _SystemMessage:
    return _SystemMessage(content=text, timestamp=now_ms())


def _user(text: str) -> UserMessage:
    return UserMessage(content=text, timestamp=now_ms())


def _image_user(text: str, image_data: str) -> UserMessage:
    return UserMessage(
        content=[TextContent(text=text), ImageContent(data=image_data, mime_type="image/png")],
        timestamp=now_ms(),
    )


def _assistant(text: str = "", tool_calls=None) -> AssistantMessage:
    content = [TextContent(text=text)] if text else []
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(
        content=content, api="faux", provider="faux", model="m", usage=empty_usage(), stop_reason="stop", timestamp=now_ms()
    )


def _tool_result(text: str, name: str = "read", tool_call_id: str = "c") -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=tool_call_id, tool_name=name, content=[TextContent(text=text)], is_error=False, timestamp=now_ms()
    )


def _assert_tool_pairs_well_formed(messages) -> None:
    call_ids = {
        block.id
        for message in messages
        if getattr(message, "role", None) == "assistant"
        for block in message.content
        if isinstance(block, ToolCall)
    }
    result_ids = {
        message.tool_call_id
        for message in messages
        if getattr(message, "role", None) == "toolResult"
    }
    assert result_ids <= call_ids
    assert call_ids <= result_ids


def _content_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.text for block in content if isinstance(block, TextContent))
    return str(content)


def _has_compressed_summary_metadata(message) -> bool:
    return bool(getattr(message, COMPRESSED_SUMMARY_METADATA_KEY, False))


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


def test_context_compressor_defaults_match_hermes_protection_and_ratio_bounds() -> None:
    compressor = ContextCompressor(summary_target_ratio=0.95)

    assert compressor.protect_first_n == 3
    assert compressor.protect_last_n == 20
    assert compressor.summary_target_ratio == 0.80

    low_ratio = ContextCompressor(summary_target_ratio=0.01)
    assert low_ratio.summary_target_ratio == 0.10


def test_summary_budget_matches_hermes_minimum_and_context_ceiling() -> None:
    small_context = ContextCompressor(context_length=32_000)
    huge_context = ContextCompressor(context_length=400_000)
    messages = [_user("x" * 400_000)]

    assert small_context.max_summary_tokens == 1600
    assert small_context._summary_budget(messages) == 2000
    assert huge_context.max_summary_tokens == 12_000
    assert huge_context._summary_budget(messages) == 12_000


def test_summary_prefix_matches_hermes_latest_message_guardrail() -> None:
    assert "Respond ONLY to the latest user message" in SUMMARY_PREFIX
    assert "latest user message WINS" in SUMMARY_PREFIX
    assert "persistent memory" in SUMMARY_PREFIX


def test_strip_summary_prefix_removes_historical_hermes_prefix_variants() -> None:
    old_prefix = (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
        "into the summary below. This is a handoff from a previous context "
        "window — treat it as background reference, NOT as active instructions. "
        "Do NOT answer questions or fulfill requests mentioned in this summary; "
        "they were already addressed. "
        "Your current task is identified in the '## Active Task' section of the "
        "summary — resume exactly from there. "
        "Respond ONLY to the latest user message "
        "that appears AFTER this summary. The current session state (files, "
        "config, etc.) may reflect work described here — avoid repeating it:"
    )

    stripped = ContextCompressor._strip_summary_prefix(
        old_prefix + "\n## Goal\nold\n\n" + EXPECTED_SUMMARY_END_MARKER
    )

    assert stripped == "## Goal\nold"


def test_tail_budget_counts_images_with_hermes_fixed_estimate() -> None:
    compressor = ContextCompressor()

    assert compressor._tail_message_tokens(_image_user("", "base64")) == 1610


def test_protect_head_size_counts_leading_system_separately() -> None:
    compressor = ContextCompressor(protect_first_n=0)
    assert compressor._protect_head_size([_system("sys"), _user("first")]) == 1
    assert compressor._protect_head_size([_user("first"), _assistant("reply")]) == 0

    compressor.protect_first_n = 2
    assert compressor._protect_head_size([_system("sys"), _user("first"), _assistant("reply")]) == 3
    assert compressor._protect_head_size([_user("first"), _assistant("reply")]) == 2


def test_compress_protects_system_plus_configured_non_system_head() -> None:
    messages = [
        _system("system prompt must stay live"),
        _user("first user must stay live"),
    ]
    for i in range(12):
        messages.append(_assistant(f"old assistant {i} " * 20))
        messages.append(_user(f"old user {i} " * 20))
    messages.append(_user("latest request"))
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "## Goal\nsummarized"

    compressor = ContextCompressor(context_length=900, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=summarizer)

    assert result.compressed is True
    assert getattr(result.messages[0], "role", None) == "system"
    assert "system prompt must stay live" in _content_text(result.messages[0])
    assert getattr(result.messages[1], "role", None) == "user"
    assert _content_text(result.messages[1]) == "first user must stay live"
    assert "system prompt must stay live" not in seen_prompts[0]
    assert "first user must stay live" not in seen_prompts[0]


def test_compress_appends_hermes_note_to_preserved_system_message() -> None:
    messages = [
        _system("system prompt must stay live"),
        _user("first user must stay live"),
    ]
    for i in range(12):
        messages.append(_assistant(f"old assistant {i} " * 20))
        messages.append(_user(f"old user {i} " * 20))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(context_length=900, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda prompt: "summary")

    system_text = _content_text(result.messages[0])
    assert "Some earlier conversation turns have been compacted into a handoff summary" in system_text
    assert "Your persistent memory (MEMORY.md, USER.md) remains fully authoritative" in system_text

    result_again = compressor.compress(result.messages, summarizer=lambda prompt: "summary")
    system_text_again = _content_text(result_again.messages[0])
    assert system_text_again.count("Some earlier conversation turns have been compacted") == 1


def test_find_tail_start_uses_bounded_floor_with_tiny_budget() -> None:
    messages = [
        _system("sys"),
        _user("old start"),
        _assistant("old ack"),
        _user("middle work"),
        _assistant("middle ack"),
        _user("middle ask 2"),
        _assistant("middle answer 2"),
        _user("middle ask 3"),
        _assistant("middle answer 3"),
        _user("recent ask 1"),
        _assistant("recent answer 1"),
        _user("recent ask 2"),
        _assistant("recent answer 2"),
        _user("latest ask"),
    ]
    compressor = ContextCompressor(
        context_length=100,
        threshold_percent=0.5,
        protect_first_n=0,
        protect_last_n=20,
    )

    cut = compressor._find_tail_start(messages, head_end=compressor._protect_head_size(messages))

    assert len(messages) - cut == 8
    assert _content_text(messages[cut]) == "middle answer 2"
    assert _content_text(messages[-1]) == "latest ask"


def test_compress_assembles_head_summary_tail() -> None:
    messages = [_user("first goal")]
    for i in range(20):
        messages.append(_assistant(f"long assistant message number {i} " * 10))
        messages.append(_user(f"user follow up {i} " * 10))
    messages.append(_user("latest request"))
    compressor = ContextCompressor(context_length=4000, protect_first_n=1, protect_last_n=4)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nGoal text\n## Completed Actions\nDid things")
    assert result.compressed is True
    summary_messages = [m for m in result.messages if _content_text(m).startswith(SUMMARY_PREFIX)]
    assert len(summary_messages) == 1
    assert getattr(summary_messages[0], "role", None) == "assistant"
    assert f"{SUMMARY_PREFIX}\n## Goal" in _content_text(summary_messages[0])
    assert EXPECTED_SUMMARY_END_MARKER in _content_text(summary_messages[0])
    assert _has_compressed_summary_metadata(summary_messages[0]) is True
    converted_messages, _ = convert_messages(Context(messages=[summary_messages[0]]))
    assert COMPRESSED_SUMMARY_METADATA_KEY not in converted_messages[0]
    assert result.messages[0].content == "first goal"  # head preserved
    assert getattr(result.messages[-1], "content", "") == "latest request"  # tail preserved


def test_compress_merges_summary_into_tail_when_both_roles_collide() -> None:
    messages = [_user("first goal")]
    for i in range(8):
        messages.append(_assistant(f"old assistant {i} " * 20))
    messages.append(_assistant("tail assistant reply"))

    compressor = ContextCompressor(context_length=600, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nmerged summary")

    summary_bearing_tail_messages = [
        message
        for message in result.messages
        if _content_text(message).startswith(SUMMARY_PREFIX)
    ]
    assert len(summary_bearing_tail_messages) == 1
    tail_text = _content_text(summary_bearing_tail_messages[0])
    assert getattr(summary_bearing_tail_messages[0], "role", None) == "assistant"
    assert tail_text.startswith(SUMMARY_PREFIX)
    assert f"{SUMMARY_PREFIX}\n## Goal" in tail_text
    assert EXPECTED_SUMMARY_END_MARKER in tail_text
    assert _has_compressed_summary_metadata(summary_bearing_tail_messages[0]) is True
    assert "old assistant" in tail_text or tail_text.rstrip().endswith("tail assistant reply")


def test_compress_keeps_tool_result_when_head_boundary_lands_on_it() -> None:
    messages = [
        _user("first goal"),
        _assistant(tool_calls=[ToolCall(id="head-call", name="read", arguments={"path": "a.txt"})]),
        _tool_result("original result " * 100, tool_call_id="head-call"),
    ]
    for i in range(12):
        messages.append(_user(f"middle {i} " * 20))
        messages.append(_assistant(f"assistant {i} " * 20))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(context_length=2000, protect_first_n=2, protect_last_n=2)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nsummarized")

    _assert_tool_pairs_well_formed(result.messages)
    preserved_result = next(
        message
        for message in result.messages
        if getattr(message, "role", None) == "toolResult" and message.tool_call_id == "head-call"
    )
    assert "earlier conversation" not in preserved_result.content[0].text
    assert preserved_result.tool_name == "read"


def test_compress_removes_orphaned_tool_result_from_tail() -> None:
    messages = [
        _user("first goal"),
        _assistant(tool_calls=[ToolCall(id="tail-result", name="read", arguments={"path": "old.txt"})]),
        _user("middle text " * 50),
        _tool_result("tail result " * 80, tool_call_id="tail-result"),
        _user("latest request"),
    ]

    compressor = ContextCompressor(context_length=400, protect_first_n=1, protect_last_n=2)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nsummarized")

    _assert_tool_pairs_well_formed(result.messages)
    assert all(
        not (
            getattr(message, "role", None) == "toolResult"
            and message.tool_call_id == "tail-result"
        )
        for message in result.messages
    )


def test_compress_keeps_latest_user_before_large_tool_tail() -> None:
    messages = [_user("first goal")]
    for i in range(8):
        messages.append(_assistant(f"old assistant {i} " * 20))
        messages.append(_user(f"old user {i} " * 20))
    messages.extend(
        [
            _user("critical latest request"),
            _assistant(tool_calls=[ToolCall(id="latest-tool", name="read", arguments={"path": "large.log"})]),
            _tool_result("large result " * 300, tool_call_id="latest-tool"),
            _assistant("final visible answer"),
        ]
    )

    compressor = ContextCompressor(context_length=400, protect_first_n=1, protect_last_n=2)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nsummarized")

    assert any(
        getattr(message, "role", None) == "user" and message.content == "critical latest request"
        for message in result.messages
    )
    _assert_tool_pairs_well_formed(result.messages)


def test_compress_keeps_last_visible_assistant_before_latest_user() -> None:
    messages = [_user("first goal")]
    for i in range(8):
        messages.append(_assistant(f"old assistant {i} " * 20))
        messages.append(_user(f"old user {i} " * 20))
    messages.extend(
        [
            _assistant("last visible assistant reply " * 20),
            _user("latest user follow-up"),
        ]
    )

    compressor = ContextCompressor(context_length=400, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nsummarized")

    assert any(
        getattr(message, "role", None) == "assistant"
        and any(isinstance(block, TextContent) and "last visible assistant reply" in block.text for block in message.content)
        for message in result.messages
    )
    assert getattr(result.messages[-1], "content", "") == "latest user follow-up"


def test_compress_strips_historical_images_before_newest_image_user() -> None:
    old_image_data = "old-image-base64"
    newest_image_data = "new-image-base64"
    messages = [
        _user("first goal"),
        _image_user("old screenshot", old_image_data),
    ]
    for i in range(8):
        messages.append(_assistant(f"old assistant {i} " * 20))
        messages.append(_user(f"old user {i} " * 20))
    messages.append(_image_user("new screenshot", newest_image_data))

    compressor = ContextCompressor(context_length=600, protect_first_n=2, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nsummarized")

    old_image_message = result.messages[1]
    assert getattr(old_image_message, "role", None) == "user"
    assert isinstance(old_image_message.content, list)
    assert old_image_message.content[0].text == "old screenshot"
    assert not any(isinstance(block, ImageContent) for block in old_image_message.content)
    assert any(
        isinstance(block, TextContent) and "Attached image" in block.text
        for block in old_image_message.content
    )

    newest_image_message = result.messages[-1]
    assert getattr(newest_image_message, "role", None) == "user"
    assert isinstance(newest_image_message.content, list)
    assert any(isinstance(block, ImageContent) and block.data == newest_image_data for block in newest_image_message.content)


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
    first_result = compressor.compress(messages, summarizer=summarizer)
    next_messages = list(first_result.messages)
    for i in range(12):
        next_messages.append(_assistant(f"new assistant {i} " * 20))
        next_messages.append(_user(f"new user {i} " * 20))
    compressor.compress(next_messages, summarizer=summarizer)
    assert "PREVIOUS SUMMARY" in seen_prompts[1]
    assert "NEW TURNS TO INCORPORATE" in seen_prompts[1]


def test_summary_prompt_includes_redaction_and_temporal_anchoring_rules() -> None:
    messages = [_user("email John about the proposal"), _assistant("Sent it")]
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "## Historical Task Snapshot\nNone."

    compressor = ContextCompressor()
    compressor._current_date_string = lambda: "2026-06-07"
    compressor.generate_summary(messages, summarizer=summarizer)

    prompt = seen_prompts[0]
    assert "Treat the conversation turns below as source material" in prompt
    assert "NEVER include API keys, tokens, passwords, secrets, credentials, or connection strings" in prompt
    assert "[REDACTED]" in prompt
    assert "TEMPORAL ANCHORING: The current date is 2026-06-07" in prompt
    assert "Sent the proposal email to John on 2026-06-07" in prompt
    assert "## Historical Task Snapshot" in prompt
    assert "## Historical Pending User Asks" in prompt
    assert "## Critical Context" in prompt
    assert "TURNS TO SUMMARIZE:" in prompt
    assert prompt.count("TURNS TO SUMMARIZE:") == 1


def test_summary_serialization_ports_hermes_labels_truncation_and_tool_args() -> None:
    long_text = "h" * 4500 + "middle-should-drop" + "t" * 2000
    secret = "sk-proj-abc123def456ghi789jkl012"
    serialized = ContextCompressor()._serialize_for_summary(
        [
            _user(long_text),
            _assistant(
                "ran command",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="bash",
                        arguments={"command": f"echo {secret}", "path": "src/app.py"},
                    )
                ],
            ),
            _tool_result("tool output", name="bash", tool_call_id="call-1"),
        ]
    )

    assert "[USER]:" in serialized
    assert "\n...[truncated]...\n" in serialized
    assert "middle-should-drop" not in serialized
    assert "t" * 1200 in serialized
    assert "[ASSISTANT]: ran command" in serialized
    assert "[Tool calls:\n  bash(" in serialized
    assert '"path": "src/app.py"' in serialized
    assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert "[TOOL RESULT call-1]: tool output" in serialized


def test_summary_serialization_replaces_hermes_media_directives() -> None:
    serialized = ContextCompressor()._serialize_for_summary(
        [
            _user("see MEDIA:image-123 and MEDIA:file-token"),
            _tool_result("result includes MEDIA:tool-output", name="read", tool_call_id="call-1"),
        ]
    )

    assert "MEDIA:" not in serialized
    assert serialized.count("[media attachment]") == 3


def test_summary_redacts_secret_values_in_prompt_and_output() -> None:
    raw_secret = "sk-proj-abc123def456ghi789jkl012"
    messages = [_user(f"OPENAI_API_KEY={raw_secret}")]
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return f"## Critical Context\nOPENAI_API_KEY={raw_secret}"

    summary = ContextCompressor().generate_summary(messages, summarizer=summarizer)

    assert raw_secret not in seen_prompts[0]
    assert raw_secret not in summary
    assert "[REDACTED]" in seen_prompts[0]
    assert "[REDACTED]" in summary


def test_summary_failure_uses_deterministic_fallback_and_bookkeeping() -> None:
    messages = [_user("first goal")]
    for i in range(12):
        messages.append(_assistant(f"assistant work {i} " * 20))
        messages.append(_user(f"user followup {i} " * 20))
    messages.append(_user("latest request"))

    def failing_summarizer(prompt: str) -> str:
        raise RuntimeError("summary provider down")

    compressor = ContextCompressor(context_length=1400, protect_first_n=1, protect_last_n=2)
    result = compressor.compress(messages, summarizer=failing_summarizer)

    assert result.compressed is True
    assert compressor._last_summary_fallback_used is True
    assert compressor._last_summary_dropped_count > 0
    assert compressor._last_summary_error == "summary provider down"
    fallback_text = "\n".join(_content_text(message) for message in result.messages)
    assert "LLM context summarizer was unavailable" in fallback_text
    assert "deterministic fallback" in fallback_text
    assert "latest request" in fallback_text


def test_deterministic_fallback_preserves_hermes_continuity_anchors() -> None:
    fallback = ContextCompressor()._static_fallback_summary(
        [
            _user("scan /tmp/project/src and explain the issue"),
            _assistant(tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "rg issue /tmp/project/src"})]),
            _tool_result("fatal error in /tmp/project/src/app.py\nTraceback: boom", name="bash", tool_call_id="c1"),
        ],
        reason="summary provider down",
    )

    assert "User asked: 'scan /tmp/project/src and explain the issue'" in fallback
    assert "Called tool(s): bash" in fallback
    assert "/tmp/project/src/app.py" in fallback
    assert "## Last Dropped Turns" in fallback
    assert len(fallback) <= 8_000


def test_missing_summary_provider_uses_hermes_fallback_bookkeeping_and_cooldown() -> None:
    messages = [_user("first goal")]
    for i in range(12):
        messages.append(_assistant(f"assistant work {i} " * 20))
        messages.append(_user(f"user followup {i} " * 20))
    messages.append(_user("latest request"))

    fake_time = {"t": 100.0}
    compressor = ContextCompressor(
        context_length=1400,
        protect_first_n=1,
        protect_last_n=2,
        clock=lambda: fake_time["t"],
    )

    result = compressor.compress(messages)

    assert result.compressed is True
    assert compressor._last_summary_fallback_used is True
    assert compressor._last_summary_dropped_count > 0
    assert compressor._last_summary_error == "no auxiliary LLM provider configured"
    assert compressor._summary_failure_cooldown_until == fake_time["t"] + 600.0
    fallback_text = "\n".join(_content_text(message) for message in result.messages)
    assert "LLM context summarizer was unavailable" in fallback_text


def test_summary_failure_flags_clear_on_subsequent_success() -> None:
    messages = [_user("first goal")]
    for i in range(12):
        messages.append(_assistant(f"assistant work {i} " * 20))
        messages.append(_user(f"user followup {i} " * 20))
    messages.append(_user("latest request"))

    fake_time = {"t": 100.0}
    compressor = ContextCompressor(
        context_length=1400,
        protect_first_n=1,
        protect_last_n=2,
        clock=lambda: fake_time["t"],
    )
    compressor.compress(messages, summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))
    assert compressor._last_summary_fallback_used is True
    assert compressor._summary_failure_cooldown_until == fake_time["t"] + 600.0

    fake_time["t"] += 601.0
    compressor.compress(messages, summarizer=lambda prompt: "## Historical Task Snapshot\nNone.")

    assert compressor._last_summary_fallback_used is False
    assert compressor._last_summary_dropped_count == 0
    assert compressor._last_summary_error is None


def test_summary_failure_abort_option_preserves_messages_and_sets_abort_flag() -> None:
    messages = [_user("first goal")]
    for i in range(8):
        messages.append(_assistant(f"assistant work {i} " * 20))
        messages.append(_user(f"user followup {i} " * 20))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(
        context_length=1000,
        protect_first_n=1,
        protect_last_n=2,
        abort_on_summary_failure=True,
    )
    result = compressor.compress(
        messages,
        summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("summary unavailable")),
    )

    assert result.compressed is False
    assert result.messages == messages
    assert compressor._last_compress_aborted is True
    assert compressor._last_summary_error == "summary unavailable"
    assert compressor._last_summary_fallback_used is False
    assert compressor._last_summary_dropped_count == 0
    assert all("Summary generation was unavailable" not in _content_text(message) for message in result.messages)


def test_summary_model_failure_falls_back_to_main_summarizer_and_records_aux_failure() -> None:
    messages = [_user("first goal")]
    for i in range(12):
        messages.append(_assistant(f"assistant work {i} " * 20))
        messages.append(_user(f"user followup {i} " * 20))
    messages.append(_user("latest request"))
    calls: list[str] = []

    def aux_summary_model(prompt: str) -> str:
        calls.append("aux")
        raise RuntimeError("400 provider rejected configured model")

    def main_model(prompt: str) -> str:
        calls.append("main")
        return "## Historical Task Snapshot\nsummary via main model"

    compressor = ContextCompressor(
        context_length=1400,
        protect_first_n=1,
        protect_last_n=2,
        model="main-model",
        summary_model_override="broken-aux-model",
        summarizer=main_model,
        summary_summarizer=aux_summary_model,
    )

    result = compressor.compress(messages)

    assert result.compressed is True
    assert calls == ["aux", "main"]
    assert compressor.summary_model == ""
    assert compressor._last_summary_fallback_used is False
    assert compressor._last_aux_model_failure_model == "broken-aux-model"
    assert compressor._last_aux_model_failure_error is not None
    assert "400" in compressor._last_aux_model_failure_error
    summary_text = "\n".join(_content_text(message) for message in result.messages)
    assert "summary via main model" in summary_text
    assert "deterministic fallback" not in summary_text


def test_compress_rehydrates_existing_summary_message() -> None:
    previous_summary = "## Goal\nprior goal\n## Remaining Work\nprior next"
    messages = [
        _user("first goal"),
        _assistant(SUMMARY_PREFIX + previous_summary + "\n\n" + EXPECTED_SUMMARY_END_MARKER),
    ]
    for i in range(12):
        messages.append(_assistant(f"old assistant {i} " * 20))
        messages.append(_user(f"old user {i} " * 20))
    messages.append(_user("latest request"))
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "## Goal\nupdated goal"

    compressor = ContextCompressor(context_length=1500, protect_first_n=1, protect_last_n=2)
    compressor.compress(messages, summarizer=summarizer)

    assert f"PREVIOUS SUMMARY:\n{previous_summary}\n\nNEW TURNS TO INCORPORATE:" in seen_prompts[0]
    new_conversation = seen_prompts[0].split("NEW TURNS TO INCORPORATE:", 1)[1]
    assert SUMMARY_PREFIX not in new_conversation
    assert EXPECTED_SUMMARY_END_MARKER not in new_conversation
    assert "prior goal" not in new_conversation


def test_stale_previous_summary_is_cleared_without_current_handoff() -> None:
    messages = [_system("sys"), _user("head")]
    for i in range(10):
        messages.append(_assistant(f"old work {i} " * 30))
        messages.append(_user(f"old ask {i} " * 30))
    messages.append(_user("latest request"))
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "fresh summary"

    compressor = ContextCompressor(context_length=1200, protect_first_n=1, protect_last_n=2)
    compressor._previous_summary = "STALE SUMMARY FROM OTHER SESSION"

    result = compressor.compress(messages, summarizer=summarizer)

    assert result.compressed is True
    assert seen_prompts
    assert "STALE SUMMARY FROM OTHER SESSION" not in seen_prompts[0]
    assert compressor._previous_summary == "fresh summary"


def test_compress_splits_oversized_protected_head_tool_result() -> None:
    huge_tool_output = "important output\n" + ("x" * 80_000)
    messages = [
        _user("read huge file"),
        _assistant(tool_calls=[ToolCall(id="call-1", name="read", arguments={"path": "huge.txt"})]),
        _tool_result(huge_tool_output, name="read", tool_call_id="call-1"),
    ]
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "## Historical Task Snapshot\nsummarized huge read output"

    compressor = ContextCompressor(context_length=2000)

    result = compressor.compress(messages, summarizer=summarizer)

    assert result.compressed is True
    assert seen_prompts
    rendered = "\n".join(_content_text(message) for message in result.messages)
    assert "summarized huge read output" in rendered
    assert huge_tool_output not in rendered
    assert estimate_tokens(result.messages) < estimate_tokens(messages)
    _assert_tool_pairs_well_formed(result.messages)


def test_estimate_tokens_counts_text() -> None:
    messages = [_user("a" * 40)]
    assert estimate_tokens(messages) == 10
