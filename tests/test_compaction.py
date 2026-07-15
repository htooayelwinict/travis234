from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Literal

from travis.ai.providers.travis_env import convert_messages
from travis.ai.context_estimate import estimate_messages_tokens
from travis.compaction import COMPRESSED_SUMMARY_METADATA_KEY, SUMMARY_PREFIX, ContextCompressor, estimate_tokens
from travis.ai.types import (
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
    assert pruned[1].content[1].arguments.keys() == {"data"}
    data_arg = pruned[1].content[1].arguments["data"]
    assert data_arg["_travis_omitted_tool_argument"] is True
    assert data_arg["field"] == "data"
    assert data_arg["chars"] == 600
    assert isinstance(data_arg["sha256"], str)
    assert "[travis redacted tool argument" not in repr(data_arg)


def test_prune_preserves_write_path_and_metadata_while_removing_large_content_arg() -> None:
    big = "Y" * 500
    big_args = {"path": "docs/report.md", "content": "SMOKING-GUN-WRITE-CONTENT\n" + ("Z" * 2000)}
    messages = [
        _user("q"),
        _assistant("call", tool_calls=[ToolCall(id="c1", name="write", arguments=big_args)]),
        _tool_result(big),
        _user("u1"), _user("u2"), _user("u3"), _user("u4"), _user("u5"), _user("u6"), _user("u7"), _user("u8"),
    ]
    compressor = ContextCompressor(protect_last_n=2)

    pruned = compressor.prune_old_tool_results(messages)

    arguments = pruned[1].content[1].arguments
    assert arguments["path"] == "docs/report.md"
    assert "content" not in arguments
    assert arguments["content_omitted"] is True
    assert arguments["content_chars"] > 2000
    assert isinstance(arguments["content_sha256"], str)
    assert "SMOKING-GUN-WRITE-CONTENT" not in repr(arguments)
    assert "[travis redacted tool argument" not in repr(arguments)
    assert "[travis omitted historical write content:" not in repr(arguments)
    assert "_truncated" not in arguments


def test_compaction_keeps_retained_tool_arguments_verbatim_for_provider_replay() -> None:
    large_replacement = "ORIGINAL-COMPACTION-EDIT\n" + ("Z" * 2_000)
    messages = [
        _user("apply the edit"),
        _assistant(
            tool_calls=[
                ToolCall(
                    id="edit-large",
                    name="edit",
                    arguments={
                        "path": "src/example.py",
                        "oldText": "before",
                        "newText": large_replacement,
                    },
                )
            ]
        ),
        _tool_result("Edit applied", name="edit", tool_call_id="edit-large"),
    ]
    for index in range(30):
        messages.extend(
            [
                _user(f"later context {index} " + ("q" * 300)),
                _assistant(f"later response {index} " + ("r" * 300)),
            ]
        )
    compressor = ContextCompressor(
        context_length=4_000,
        protect_first_n=3,
        protect_last_n=4,
    )

    result = compressor.compress(messages, summarizer=lambda _prompt: "## Goal\ncheckpoint")

    assert result.compressed is True
    replayed_call = next(
        block
        for message in result.messages
        if getattr(message, "role", None) == "assistant"
        for block in message.content
        if isinstance(block, ToolCall) and block.id == "edit-large"
    )
    assert replayed_call.arguments["newText"] == large_replacement
    assert isinstance(replayed_call.arguments["newText"], str)
    assert "_travis_omitted_tool_argument" not in repr(result.messages)


def test_compress_appends_travis234_file_operation_tags_to_summary() -> None:
    messages = [
        _user("goal"),
        _assistant(tool_calls=[ToolCall(id="read-1", name="read", arguments={"path": "src/a.py"})]),
        _tool_result("a", name="read", tool_call_id="read-1"),
        _assistant(tool_calls=[ToolCall(id="write-1", name="write", arguments={"path": "src/b.py", "content": "b"})]),
        _tool_result("wrote", name="write", tool_call_id="write-1"),
        _assistant(tool_calls=[ToolCall(id="edit-1", name="edit", arguments={"path": "src/c.py"})]),
        _tool_result("edited", name="edit", tool_call_id="edit-1"),
    ]
    for index in range(14):
        messages.append(_user(f"old filler {index} " * 30))
        messages.append(_assistant(f"old ack {index} " * 30))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(context_length=700, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda _prompt: "## Goal\nsummary without files")

    assert result.compressed is True
    assert "<read-files>\nsrc/a.py\n</read-files>" in result.summary
    assert "<modified-files>\nsrc/b.py\nsrc/c.py\n</modified-files>" in result.summary


def test_compress_reports_travis234_file_operation_details() -> None:
    messages = [
        _user("goal"),
        _assistant(tool_calls=[ToolCall(id="read-1", name="read", arguments={"path": "src/a.py"})]),
        _tool_result("a", name="read", tool_call_id="read-1"),
        _assistant(tool_calls=[ToolCall(id="write-1", name="write", arguments={"path": "src/b.py", "content": "b"})]),
        _tool_result("wrote", name="write", tool_call_id="write-1"),
        _assistant(tool_calls=[ToolCall(id="edit-1", name="edit", arguments={"path": "src/c.py"})]),
        _tool_result("edited", name="edit", tool_call_id="edit-1"),
    ]
    for index in range(14):
        messages.append(_user(f"old filler {index} " * 30))
        messages.append(_assistant(f"old ack {index} " * 30))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(context_length=700, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda _prompt: "## Goal\nsummary without files")

    assert result.compressed is True
    assert result.details == {
        "readFiles": ["src/a.py"],
        "modifiedFiles": ["src/b.py", "src/c.py"],
    }


def test_compress_tracks_historical_bash_file_mutations_in_file_details() -> None:
    messages = [
        _user("create docs from protocol fixture"),
        _assistant(
            tool_calls=[
                ToolCall(
                    id="bash-1",
                    name="bash",
                    arguments={"command": "printf '%s\\n' '# Protocol' > docs/protocol_fixture.md"},
                )
            ]
        ),
        _tool_result("wrote protocol fixture", name="bash", tool_call_id="bash-1"),
        _assistant(
            tool_calls=[
                ToolCall(
                    id="bash-2",
                    name="bash",
                    arguments={"command": "printf '%s\\n' '# Review' | tee reports/review.md >/dev/null"},
                )
            ]
        ),
        _tool_result("wrote review", name="bash", tool_call_id="bash-2"),
    ]
    for index in range(14):
        messages.append(_user(f"old filler {index} " * 30))
        messages.append(_assistant(f"old ack {index} " * 30))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(context_length=700, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda _prompt: "## Goal\nsummary without files")

    assert result.compressed is True
    assert result.details == {
        "readFiles": [],
        "modifiedFiles": ["docs/protocol_fixture.md", "reports/review.md"],
    }
    assert "<modified-files>\ndocs/protocol_fixture.md\nreports/review.md\n</modified-files>" in result.summary


def test_compress_preserves_previous_travis234_file_operation_tags_across_iterative_summary() -> None:
    first_messages = [
        _user("goal"),
        _assistant(tool_calls=[ToolCall(id="write-1", name="write", arguments={"path": "src/old.py", "content": "old"})]),
        _tool_result("wrote", name="write", tool_call_id="write-1"),
    ]
    for index in range(14):
        first_messages.append(_user(f"old first filler {index} " * 30))
        first_messages.append(_assistant(f"old first ack {index} " * 30))
    first_messages.append(_user("latest first"))

    compressor = ContextCompressor(context_length=700, protect_first_n=1, protect_last_n=1)
    first = compressor.compress(first_messages, summarizer=lambda _prompt: "## Goal\nfirst summary")
    assert "<modified-files>\nsrc/old.py\n</modified-files>" in first.summary

    second_messages = list(first.messages)
    second_messages.extend(
        [
            _user("new task"),
            _assistant(tool_calls=[ToolCall(id="read-2", name="read", arguments={"path": "src/new.py"})]),
            _tool_result("new", name="read", tool_call_id="read-2"),
        ]
    )
    for index in range(14):
        second_messages.append(_user(f"old second filler {index} " * 30))
        second_messages.append(_assistant(f"old second ack {index} " * 30))
    second_messages.append(_user("latest second"))

    second = compressor.compress(second_messages, summarizer=lambda _prompt: "## Goal\nsecond summary dropped tags")

    assert second.compressed is True
    assert "<read-files>\nsrc/new.py\n</read-files>" in second.summary
    assert "<modified-files>\nsrc/old.py\n</modified-files>" in second.summary


def test_prune_summarizes_old_subagent_expansion_to_metadata_only() -> None:
    child_body = "sensitive child analysis body " * 80
    expanded = "\n".join(
        [
            "Subagent result expansion",
            "taskId: subagent-123",
            "role: code-reviewer",
            "status: completed",
            "section: final_response",
            "offset: 1200",
            "budget: medium",
            "truncated: true",
            "nextOffset: 7200",
            "totalChars: 9000",
            "",
            child_body,
        ]
    )
    messages = [
        _user("q"),
        _assistant(
            "call",
            tool_calls=[
                ToolCall(
                    id="expand-1",
                    name="expand_subagent_result",
                    arguments={"taskId": "subagent-123", "section": "final_response", "offset": 1200},
                )
            ],
        ),
        _tool_result(expanded, name="expand_subagent_result", tool_call_id="expand-1"),
        _user("u1"), _user("u2"), _user("u3"), _user("u4"), _user("u5"), _user("u6"), _user("u7"), _user("u8"),
    ]
    compressor = ContextCompressor(protect_last_n=2)

    pruned = compressor.prune_old_tool_results(messages)

    summary = pruned[2].content[0].text
    assert "[expand_subagent_result]" in summary
    assert "taskId=subagent-123" in summary
    assert "section=final_response" in summary
    assert "offset=1200" in summary
    assert "nextOffset=7200" in summary
    assert "sensitive child analysis body" not in summary
    assert "Use expand_subagent_result" in summary


def test_should_compress_threshold_and_antithrash() -> None:
    compressor = ContextCompressor(context_length=1000, threshold_percent=0.5)
    assert compressor.should_compress(400) is False
    assert compressor.should_compress(849) is False
    assert compressor.should_compress(850) is True
    compressor._ineffective_compression_count = 2
    assert compressor.should_compress(850) is False


def test_context_compressor_defaults_match_travis_protection_and_ratio_bounds() -> None:
    compressor = ContextCompressor(summary_target_ratio=0.95)

    assert compressor.protect_first_n == 3
    assert compressor.protect_last_n == 20
    assert compressor.summary_target_ratio == 0.80

    low_ratio = ContextCompressor(summary_target_ratio=0.01)
    assert low_ratio.summary_target_ratio == 0.10


def test_summary_budget_matches_travis_minimum_and_context_ceiling() -> None:
    small_context = ContextCompressor(context_length=32_000)
    huge_context = ContextCompressor(context_length=400_000)
    messages = [_user("x" * 400_000)]

    assert small_context.max_summary_tokens == 1600
    assert small_context._summary_budget(messages) == 2000
    assert huge_context.max_summary_tokens == 10_000
    assert huge_context._summary_budget(messages) == 10_000


def test_summary_prefix_matches_travis_latest_message_guardrail() -> None:
    assert "REFERENCE ONLY" in SUMMARY_PREFIX
    assert "Respond ONLY to the latest user message" in SUMMARY_PREFIX
    assert "latest user message WINS" in SUMMARY_PREFIX
    assert "persistent memory" in SUMMARY_PREFIX


def test_summary_prompt_scopes_constraints_and_requires_completion_evidence() -> None:
    seen_prompts: list[str] = []
    compressor = ContextCompressor()

    compressor.generate_summary(
        [
            _user("Implement extension reload support"),
            _assistant("Extension reload completed"),
            _user("Verify managed process cancellation"),
            _assistant("Cancellation verification completed"),
        ],
        summarizer=lambda prompt: seen_prompts.append(prompt) or "## Goal\ncheckpoint",
    )

    prompt = seen_prompts[0]
    assert "Preserve distinct tasks" in prompt
    assert "explicitly stated by the USER" in prompt
    assert "Scope task-local instructions to their originating task or turn" in prompt
    assert "Never infer a global preference from assistant behavior" in prompt
    assert "explicit completion or verification evidence" in prompt
    assert "no task is listed as both completed and in progress" in prompt
    assert "## Historical In-Progress State" in prompt
    assert "## Historical Remaining Work" in prompt
    assert "## Next Steps" not in prompt


def test_strip_summary_prefix_removes_historical_travis_prefix_variants() -> None:
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


def test_strip_summary_prefix_removes_generic_travis_compatibility_variant() -> None:
    generic = (
        "The conversation history before this point was compacted into the following summary:\n\n"
        "<summary>\n## Goal\nold\n\n</summary>"
    )

    assert ContextCompressor._strip_summary_prefix(generic) == "## Goal\nold"


def test_merged_summary_rehydrates_without_retained_tail() -> None:
    merged = (
        f"{SUMMARY_PREFIX}\nSUMMARY BODY\n\n{EXPECTED_SUMMARY_END_MARKER}"
        "\n\nLATEST USER TASK"
    )

    index, body = ContextCompressor._find_previous_summary([_user(merged)])

    assert index == 0
    assert body == "SUMMARY BODY"
    assert "LATEST USER TASK" not in body
    assert EXPECTED_SUMMARY_END_MARKER not in body


def test_second_compaction_summarizes_merged_retained_tail_without_marker_contamination() -> None:
    merged = (
        f"{SUMMARY_PREFIX}\nPRIOR FACT\n\n{EXPECTED_SUMMARY_END_MARKER}"
        "\n\nRETAINED TASK FACT"
    )
    messages = [_user(merged)]
    for index in range(12):
        messages.extend(
            [
                _assistant(f"work {index} " * 20),
                _user(f"follow-up {index} " * 20),
            ]
        )
    messages.append(_user("newest request"))
    seen_prompts: list[str] = []

    compressor = ContextCompressor(context_length=1_200, protect_first_n=2, protect_last_n=2)
    result = compressor.compress(
        messages,
        summarizer=lambda prompt: seen_prompts.append(prompt) or "updated summary",
    )

    assert result.compressed is True
    assert seen_prompts[0].count("PRIOR FACT") == 1
    assert seen_prompts[0].count("RETAINED TASK FACT") == 1
    assert EXPECTED_SUMMARY_END_MARKER not in seen_prompts[0]


def test_tail_budget_counts_images_with_travis_fixed_estimate() -> None:
    compressor = ContextCompressor()

    assert compressor._tail_message_tokens(_image_user("", "base64")) == 1610


def test_protect_head_size_counts_leading_system_separately() -> None:
    compressor = ContextCompressor(protect_first_n=0)
    assert compressor._protect_head_size([_system("sys"), _user("first")]) == 1
    assert compressor._protect_head_size([_user("first"), _assistant("reply")]) == 0

    compressor.protect_first_n = 2
    assert compressor._protect_head_size([_system("sys"), _user("first"), _assistant("reply")]) == 3
    assert compressor._protect_head_size([_user("first"), _assistant("reply")]) == 2


def test_protected_head_decays_after_a_previous_summary_exists() -> None:
    compressor = ContextCompressor(protect_first_n=2)
    messages = [
        _user(SUMMARY_PREFIX + "\nold handoff\n\n" + EXPECTED_SUMMARY_END_MARKER),
        _user("new task"),
        _assistant("working"),
    ]

    assert compressor._effective_protect_first_n(messages) == 0
    assert compressor._protect_head_size(messages) == 0


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


def test_compress_appends_travis_note_to_preserved_system_message() -> None:
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
    assert "## Historical Remaining Work" in prompt
    assert "## Next Steps" not in prompt
    assert "## Critical Context" in prompt
    assert "TURNS TO SUMMARIZE:" in prompt
    assert prompt.count("TURNS TO SUMMARIZE:") == 1


def test_summary_serialization_ports_travis_labels_truncation_and_tool_args() -> None:
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


def test_summary_serialization_replaces_travis_media_directives() -> None:
    serialized = ContextCompressor()._serialize_for_summary(
        [
            _user("see MEDIA:image-123 and MEDIA:file-token"),
            _tool_result("result includes MEDIA:tool-output", name="read", tool_call_id="call-1"),
        ]
    )

    assert "MEDIA:" not in serialized
    assert serialized.count("[media attachment]") == 3


def test_compaction_never_summarizes_internal_write_omission_marker() -> None:
    omitted_marker = (
        "Historical write tool call omitted from provider replay. "
        "To continue safely, inspect the file on disk or regenerate full content from current state.\n"
        "[File mutation recovery: code=write_omitted_historical_content; path=src/app.py]\n"
        "[travis omitted historical write content: path=src/app.py]"
    )
    messages = [
        _user("start task"),
        _assistant(omitted_marker),
        _user("latest request"),
    ]

    compressor = ContextCompressor(context_length=100, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(messages, summarizer=lambda prompt: prompt, force=True)

    compressed = repr(result.messages)
    assert "Historical write tool call omitted from provider replay" not in compressed
    assert "regenerate full content" not in compressed


def test_rehydrated_previous_summary_scrubs_internal_write_omission_marker_from_prompt() -> None:
    previous_summary = (
        "## Goal\nprior goal\n"
        "Historical write tool call omitted from provider replay. "
        "To continue safely, inspect the file on disk or regenerate full content from current state.\n"
        "## Relevant Files\n- docs/report.md"
    )
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
    result = compressor.compress(messages, summarizer=summarizer)

    assert result.compressed is True
    assert seen_prompts
    assert "Historical write tool call omitted from provider replay" not in seen_prompts[0]
    assert "regenerate full content" not in seen_prompts[0]
    assert "Historical write tool call omitted from provider replay" not in repr(result.messages)
    assert "regenerate full content" not in repr(result.messages)


def test_summary_serialization_scrubs_internal_write_omission_marker_from_tool_args() -> None:
    serialized = ContextCompressor()._serialize_for_summary(
        [
            _assistant(
                "wrote report",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write",
                        arguments={
                            "path": "docs/report.md",
                            "content": (
                                "[travis omitted historical write content: "
                                "1234 chars, sha256=abcdef1234567890]"
                            ),
                        },
                    )
                ],
            ),
        ]
    )

    assert "[Tool calls:" in serialized
    assert "write(" in serialized
    assert '"path": "docs/report.md"' in serialized
    assert "[travis omitted historical write content:" not in serialized


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


def test_secret_redaction_is_bounded_for_large_single_token_output() -> None:
    from travis.compaction.compressor import _redact_sensitive_text

    payload = ("x" * 60_000) + '\n{"apiKey":"ordinary-secret-value"}'
    started = time.perf_counter()

    redacted = _redact_sensitive_text(payload)

    assert time.perf_counter() - started < 0.5
    assert "ordinary-secret-value" not in redacted
    assert '"apiKey":"[REDACTED]"' in redacted


def test_large_deterministic_fallback_preserves_failure_provenance_tail() -> None:
    fallback = ContextCompressor()._static_fallback_summary(
        [
            _user("inspect the large output"),
            _tool_result("x" * 60_000),
        ],
        reason="summary model unavailable",
    )

    assert len(fallback) <= 8_000
    assert "Summary generation was unavailable" in fallback
    assert "summary model unavailable" in fallback


def test_summary_excludes_inline_reasoning_from_prompt_and_persisted_output() -> None:
    messages = [
        _user("keep the verified result"),
        _assistant("Visible result.\n<think>unverified scratch conclusion</think>\nStill visible."),
    ]
    seen_prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "<reasoning>draft summary logic</reasoning>\n## Goal\nKeep the verified result."

    summary = ContextCompressor().generate_summary(messages, summarizer=summarizer)

    assert "unverified scratch conclusion" not in seen_prompts[0]
    assert "Visible result." in seen_prompts[0]
    assert "Still visible." in seen_prompts[0]
    assert summary == "## Goal\nKeep the verified result."


def test_empty_auxiliary_summary_falls_back_to_main_summarizer() -> None:
    calls: list[str] = []

    def auxiliary(_prompt: str) -> str:
        calls.append("aux")
        return "<think>reasoning without a summary body</think>"

    def main(_prompt: str) -> str:
        calls.append("main")
        return "## Goal\nRecovered through the main model."

    compressor = ContextCompressor(
        model="main-model",
        summary_model_override="aux-model",
        summarizer=main,
        summary_summarizer=auxiliary,
    )

    summary = compressor.generate_summary([_user("preserve this")], summarizer=main)

    assert calls == ["aux", "main"]
    assert summary == "## Goal\nRecovered through the main model."
    assert compressor.summary_model == "aux-model"
    assert compressor._last_aux_model_failure_model == "aux-model"
    assert compressor._last_aux_model_failure_error == "Context compression LLM returned empty content"
    assert compressor._last_summary_model_requested == "aux-model"
    assert compressor._last_summary_model_used == "main-model"
    assert compressor._last_summary_model_fallback_used is True


def test_empty_main_summary_uses_deterministic_handoff_instead_of_blank_summary() -> None:
    messages = [_user("first goal")]
    for index in range(12):
        messages.append(_assistant(f"assistant work {index} " * 20))
        messages.append(_user(f"user followup {index} " * 20))
    messages.append(_user("latest request"))
    compressor = ContextCompressor(context_length=1400, protect_first_n=1, protect_last_n=2)

    result = compressor.compress(messages, summarizer=lambda _prompt: "   ")

    rendered = "\n".join(_content_text(message) for message in result.messages)
    assert result.compressed is True
    assert compressor._last_summary_fallback_used is True
    assert compressor._last_summary_error == "Context compression LLM returned empty content"
    assert "deterministic fallback" in rendered
    assert SUMMARY_PREFIX in rendered


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


def test_deterministic_fallback_preserves_travis_continuity_anchors() -> None:
    fallback = ContextCompressor()._static_fallback_summary(
        [
            _user("scan /tmp/project/src and explain the issue"),
            _assistant(tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "rg issue /tmp/project/src"})]),
            _tool_result("fatal error in /tmp/project/src/app.py\nTraceback: boom", name="bash", tool_call_id="c1"),
        ],
        reason="summary provider down",
    )

    assert "Historical user ask: scan /tmp/project/src and explain the issue" in fallback
    assert "Called tool(s): bash" in fallback
    assert "/tmp/project/src/app.py" in fallback
    assert "## Last Dropped Turns" in fallback
    assert len(fallback) <= 8_000


def test_deterministic_fallback_preserves_travis234_file_operation_inventory() -> None:
    fallback = ContextCompressor()._static_fallback_summary(
        [
            _user("build ledger tools and document the result"),
            _assistant(tool_calls=[ToolCall(id="w1", name="write", arguments={"path": "ledger_tools/parser.py", "content": "parser"})]),
            _tool_result("Successfully wrote 6 bytes to ledger_tools/parser.py", name="write", tool_call_id="w1"),
            _assistant(tool_calls=[ToolCall(id="e1", name="edit", arguments={"path": "ledger_tools/cli.py", "oldText": "old", "newText": "new"})]),
            _tool_result("Successfully edited ledger_tools/cli.py", name="edit", tool_call_id="e1"),
            _assistant(tool_calls=[ToolCall(id="r1", name="read", arguments={"path": "README.md"})]),
            _tool_result("README contents", name="read", tool_call_id="r1"),
        ],
        reason="summary provider down",
    )

    assert "## File Operations" in fallback
    assert "Modified files:" in fallback
    assert "ledger_tools/parser.py" in fallback
    assert "ledger_tools/cli.py" in fallback
    assert "Read files:" in fallback
    assert "README.md" in fallback


def test_deterministic_fallback_does_not_force_broad_verification() -> None:
    fallback = ContextCompressor()._static_fallback_summary(
        [
            _user("read docs/report.md and refine the summary only"),
            _assistant(tool_calls=[ToolCall(id="r1", name="read", arguments={"path": "docs/report.md"})]),
            _tool_result("report", name="read", tool_call_id="r1"),
            _assistant(tool_calls=[ToolCall(id="e1", name="edit", arguments={"path": "docs/report.md"})]),
            _tool_result("edited", name="edit", tool_call_id="e1"),
        ],
        reason="no auxiliary LLM provider configured",
    )

    assert "git state" not in fallback
    assert "processes" not in fallback
    assert "test results" not in fallback
    assert "Verify state with tools before making claims" not in fallback
    assert "Inspect only the files or state needed for the latest user request" in fallback
    assert "Run tests only when the latest request asks for tests" in fallback


def test_missing_summary_provider_uses_travis_fallback_bookkeeping_and_cooldown() -> None:
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


def test_compress_does_not_rewrite_existing_summary_when_no_new_middle_turns() -> None:
    messages = [_user("scan the codebase")]
    for i in range(32):
        messages.append(_user(f"user turn {i} " * 120))
        messages.append(_assistant(f"assistant turn {i} " * 120))
    messages.append(_user("latest request"))

    compressor = ContextCompressor(
        context_length=30_000,
        threshold_percent=0.5,
        protect_first_n=3,
        protect_last_n=20,
    )
    first = compressor.compress(
        messages,
        summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("summary provider rejected first pass")),
        force=True,
    )
    assert first.compressed is True
    assert compressor._last_summary_fallback_used is True

    calls: list[str] = []

    def expanding_summarizer(prompt: str) -> str:
        calls.append(prompt)
        return "## Historical Task Snapshot\n" + ("expanded successful summary " * 500)

    before_tokens = estimate_tokens(first.messages)
    second = compressor.compress(first.messages, summarizer=expanding_summarizer, force=True)

    assert second.compressed is False
    assert second.messages == first.messages
    assert estimate_tokens(second.messages) == before_tokens
    assert calls == []


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
    assert compressor.summary_model == "broken-aux-model"
    assert compressor._last_summary_fallback_used is False
    assert compressor._last_aux_model_failure_model == "broken-aux-model"
    assert compressor._last_aux_model_failure_error is not None
    assert "400" in compressor._last_aux_model_failure_error
    assert result.summary_model_requested == "broken-aux-model"
    assert result.summary_model_used == "main-model"
    assert result.summary_model_fallback is True
    assert result.summary_model_error == "400 provider rejected configured model"
    summary_text = "\n".join(_content_text(message) for message in result.messages)
    assert "summary via main model" in summary_text
    assert "deterministic fallback" not in summary_text


def test_configured_summary_model_is_retried_after_recovered_failure() -> None:
    auxiliary_calls = 0

    def auxiliary(_prompt: str) -> str:
        nonlocal auxiliary_calls
        auxiliary_calls += 1
        if auxiliary_calls == 1:
            raise RuntimeError("temporary summary route failure")
        return "## Historical Task Snapshot\nsummary via configured model"

    compressor = ContextCompressor(
        model="main-model",
        summary_model_override="openrouter/openai/gpt-5.6-luna-pro",
        summarizer=lambda _prompt: "## Historical Task Snapshot\nsummary via main model",
        summary_summarizer=auxiliary,
    )

    first = compressor.generate_summary([_user("first checkpoint")], summarizer=compressor._summarizer)
    second = compressor.generate_summary([_user("second checkpoint")], summarizer=compressor._summarizer)

    assert "summary via main model" in first
    assert "summary via configured model" in second
    assert auxiliary_calls == 2
    assert compressor.summary_model == "openrouter/openai/gpt-5.6-luna-pro"
    assert compressor._last_summary_model_used == "openrouter/openai/gpt-5.6-luna-pro"


def test_auth_failure_aborts_compaction_and_preserves_transcript_exactly() -> None:
    messages = [_user("first goal")]
    for index in range(12):
        messages.append(_assistant(f"assistant work {index} " * 20))
        messages.append(_user(f"user followup {index} " * 20))
    messages.append(_user("latest request"))
    compressor = ContextCompressor(context_length=1400, protect_first_n=1, protect_last_n=2)

    result = compressor.compress(
        messages,
        summarizer=lambda _prompt: (_ for _ in ()).throw(
            RuntimeError("401 unauthorized: invalid API key sk-proj-secret123456")
        ),
    )

    assert result.compressed is False
    assert result.messages is messages
    assert result.savings_pct == 0.0
    assert result.summary_model_error is not None
    assert "sk-proj-secret123456" not in result.summary_model_error
    assert compressor._last_compress_aborted is True
    assert compressor._last_summary_auth_failure is True
    assert compressor._last_summary_fallback_used is False
    assert compressor._last_summary_dropped_count == 0


def test_network_failure_aborts_until_a_forced_retry_succeeds() -> None:
    messages = [_user("first goal")]
    for index in range(12):
        messages.append(_assistant(f"assistant work {index} " * 20))
        messages.append(_user(f"user followup {index} " * 20))
    messages.append(_user("latest request"))
    compressor = ContextCompressor(context_length=1400, protect_first_n=1, protect_last_n=2)

    failed = compressor.compress(
        messages,
        summarizer=lambda _prompt: (_ for _ in ()).throw(TimeoutError("provider timed out")),
    )
    recovered = compressor.compress(
        messages,
        summarizer=lambda _prompt: "## Historical Task Snapshot\nrecovered checkpoint",
        force=True,
    )

    assert failed.compressed is False
    assert failed.messages is messages
    assert recovered.compressed is True
    assert compressor._last_summary_auth_failure is False
    assert compressor._last_summary_network_failure is False
    assert compressor._last_compress_aborted is False


def test_summary_failure_fallback_redacts_secrets_from_inserted_summary() -> None:
    raw_secret = "sk-proj-abc123def456ghi789jkl012"
    messages = [
        _user("start"),
        _user(f"OPENAI_API_KEY={raw_secret}"),
        _assistant("I used the configured key."),
        _user("latest request"),
    ]

    compressor = ContextCompressor(context_length=600, protect_first_n=1, protect_last_n=1)
    result = compressor.compress(
        messages,
        summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("summary down")),
        force=True,
    )

    rendered = "\n".join(_content_text(message) for message in result.messages)
    assert result.compressed is True
    assert compressor._last_summary_fallback_used is True
    assert raw_secret not in rendered
    assert "OPENAI_API_KEY=[REDACTED]" in rendered


def test_summary_failure_fallback_labels_one_historical_ask_without_repeating_it() -> None:
    compressor = ContextCompressor()
    summary = compressor._static_fallback_summary(
        [_user("older ask"), _assistant("worked"), _user("newest historical ask")],
        reason="summary unavailable",
    )

    assert summary.count("Historical user ask:") == 1
    assert "Historical user ask: newest historical ask" in summary
    assert (
        "This ask is historical context and is not necessarily outstanding. "
        "Follow the newest retained user message."
    ) in summary


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


def test_compression_result_reports_travis234_cut_boundary_for_session_compaction() -> None:
    messages = [_user(f"message {index} " + ("x" * 80)) for index in range(12)]
    compressor = ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1)
    expected_cut = compressor._find_tail_start(messages, compressor._protect_head_size(messages))

    result = compressor.compress(messages, summarizer=lambda prompt: "## Goal\nBoundary summary.", force=True)

    assert result.compressed is True
    assert result.summary == "## Goal\nBoundary summary."
    assert result.tokens_before == estimate_tokens(messages)
    assert result.first_kept_message_index == expected_cut
    assert _content_text(result.messages[2]) == _content_text(messages[result.first_kept_message_index])


def test_estimate_tokens_counts_text() -> None:
    messages = [_user("a" * 40)]
    assert estimate_tokens(messages) == 10


def test_compaction_estimator_counts_appv231_replay_envelope_fields() -> None:
    assistant = AssistantMessage(
        content=[
            TextContent(text="visible", text_signature="s" * 120),
            ToolCall(
                id="call-" + "i" * 80,
                name="read",
                arguments={"path": "p" * 160},
                thought_signature="t" * 120,
            ),
        ],
        api="openai-responses",
        provider="openai",
        model="gpt-5.4",
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    assistant.codex_reasoning_items = [{"summary": "r" * 240}]

    assert estimate_tokens([assistant]) == estimate_messages_tokens([assistant])
    assert estimate_tokens([assistant]) > len("visibleread") // 4
