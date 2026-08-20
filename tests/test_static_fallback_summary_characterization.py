"""Direct characterization coverage for deterministic compaction fallback summaries."""

from __future__ import annotations

import copy
import hashlib

import pytest

from travis.ai.types import (
    AssistantMessage,
    ContentBlock,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)
from travis.compaction.compressor import ContextCompressor


_EMPTY_SUMMARY = """## Historical Task Snapshot
Historical user ask: Unknown from deterministic fallback.
This ask is historical context and is not necessarily outstanding. Follow the newest retained user message.

## Goal
Recovered from a deterministic fallback because the LLM context summarizer was unavailable. Continue from the protected recent messages after this summary and use current file/system state for exact details.

## Constraints & Preferences
- This fallback was generated locally without an LLM summary call.
- Secrets and credentials were redacted before preservation.
- The summary may be incomplete. Inspect only the files or state needed for the latest user request before making claims.
- Run tests only when the latest request asks for tests, or when validating a code change that genuinely requires test execution.

	## Completed Actions
	None recoverable from compacted turns.

	## File Operations
	Modified files:
	None.
	Read files:
	None.

	## Active State
	Unknown from deterministic fallback. Inspect current repository/session state if needed.

## Historical In-Progress State
Unknown from deterministic fallback. Current work is defined by the protected retained tail.

## Blocked
None.

## Key Decisions
None recoverable from deterministic fallback.

## Resolved Questions
None recoverable from deterministic fallback.

## Historical Pending User Asks
None inferred. Historical asks are not automatically outstanding.

## Relevant Files
None.

## Historical Remaining Work
Use the newest retained user message after this summary.

## Last Dropped Turns
None.

## Critical Context
Summary generation was unavailable, so this is a best-effort deterministic fallback for 0 compacted message(s)."""


class _TestCompressor(ContextCompressor):
    def fallback_summary(
        self,
        middle: list[Message],
        *,
        reason: str | None = None,
        recent_user_focus: str | None = None,
    ) -> str:
        return self._static_fallback_summary(
            middle,
            reason=reason,
            recent_user_focus=recent_user_focus,
        )


def _user(text: str) -> UserMessage:
    return UserMessage(content=text, timestamp=1)


def _assistant(
    text: str = "",
    tool_calls: list[ToolCall] | None = None,
) -> AssistantMessage:
    content: list[ContentBlock] = [TextContent(text=text)] if text else []
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(
        content=content,
        api="faux",
        provider="faux",
        model="fixture",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=2,
    )


def _tool_result(
    text: str,
    *,
    name: str = "read",
    tool_call_id: str = "call-1",
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=tool_call_id,
        tool_name=name,
        content=[TextContent(text=text)],
        is_error=False,
        timestamp=3,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_empty_fallback_summary_preserves_every_byte_and_default_branch() -> None:
    summary = _TestCompressor().fallback_summary([])

    assert summary == _EMPTY_SUMMARY
    assert len(summary) == 1706


def test_basic_fallback_preserves_last_user_precedence_sections_and_reason() -> None:
    summary = _TestCompressor().fallback_summary(
        [_user("first ask"), _assistant("finished it"), _user("newest ask")],
        reason="summary unavailable",
        recent_user_focus="continue latest",
    )

    assert _digest(summary) == "771b67838147a8c47998a382c0ec880c71a756ff2eea17bed9d0b713b1a27bde"
    assert len(summary) == 1716
    assert "Historical user ask: newest ask" in summary
    assert "\t## Completed Actions\n\t1. finished it" in summary
    assert "## Historical Remaining Work\ncontinue latest" in summary
    assert ("## Last Dropped Turns\n- USER: first ask\n- ASSISTANT: finished it\n- USER: newest ask") in summary
    assert summary.endswith(
        "Summary generation was unavailable, so this is a best-effort deterministic fallback "
        "for 3 compacted message(s). Summary failure reason: summary unavailable."
    )


def test_tool_pass_preserves_inventory_result_blocker_and_relevant_path_order() -> None:
    summary = _TestCompressor().fallback_summary(
        [
            _user("inspect /work/src"),
            _assistant(
                "working",
                [
                    ToolCall(id="r", name="read", arguments={"path": "README.md"}),
                    ToolCall(
                        id="w",
                        name="write",
                        arguments={
                            "file_path": "src/app.py",
                            "content": "see /tmp/input.txt",
                        },
                    ),
                ],
            ),
            _tool_result(
                "fatal error in /work/src/app.py\nTraceback: boom",
                name="read",
                tool_call_id="r",
            ),
            _tool_result("ok /other/file.txt", name="write", tool_call_id="w"),
        ],
        reason="down",
    )

    assert _digest(summary) == "e0af25bd7d4bac9c5ae08a396fa6847575f865d3bd9d9c952eeb139fe4f2f83c"
    assert "1. Called tool(s): read, write" in summary
    assert "2. [read] result elided (47 chars, 1 lines)" in summary
    assert "3. [write] result elided (18 chars, 1 lines)" in summary
    assert "Modified files:\n\t- src/app.py\n\tRead files:\n\t- README.md" in summary
    assert "## Blocked\n- fatal error in /work/src/app.py Traceback: boom" in summary
    relevant = summary.split("## Relevant Files\n", 1)[1].split("\n\n", 1)[0]
    assert relevant.splitlines() == [
        "- /app.py",
        "- /tmp/input.txt",
        "- /work/src",
        "- /work/src/app.py",
        "- /other/file.txt",
    ]


def test_turn_compaction_sanitizes_before_whitespace_and_length_truncation() -> None:
    secret = "sk-proj-abc123def456ghi789jkl012"
    github_secret = "ghp_short"
    replay_marker = "Historical write tool call omitted from provider replay."
    text = f"  OPENAI_API_KEY={secret}\n{replay_marker}\nMEDIA:/tmp/private.png   keep   {github_secret}  "

    summary = _TestCompressor().fallback_summary([_user(text)])

    assert secret not in summary
    assert github_secret not in summary
    assert replay_marker not in summary
    assert "Historical user ask: OPENAI_API_KEY=[REDACTED] [media attachment] keep [REDACTED]" in summary
    assert "- USER: OPENAI_API_KEY=[REDACTED] [media attachment] keep [REDACTED]" in summary


def test_each_turn_is_truncated_to_the_exact_700_character_contract() -> None:
    text = "prefix " + ("x" * 900)
    expected = ("prefix " + ("x" * 678)) + " ...[truncated]"

    summary = _TestCompressor().fallback_summary([_user(text)])

    assert len(expected) == 700
    assert f"Historical user ask: {expected}\n" in summary
    assert f"- USER: {expected}\n" in summary
    assert "x" * 679 not in summary


def test_falsey_reason_and_focus_follow_the_same_default_precedence_as_none() -> None:
    compressor = _TestCompressor()
    messages: list[Message] = [_user("ask")]

    empty_values = compressor.fallback_summary(messages, reason="", recent_user_focus="")
    none_values = compressor.fallback_summary(messages, reason=None, recent_user_focus=None)

    assert empty_values == none_values
    assert "Summary failure reason:" not in empty_values
    assert "Use the newest retained user message after this summary." in empty_values


def test_tool_name_completed_actions_and_last_turns_keep_their_exact_caps() -> None:
    calls = [ToolCall(id=f"call-{index}", name=f"tool{index}", arguments={}) for index in range(8)]
    messages: list[Message] = [_assistant("ignored because calls win", calls)]
    messages.extend(_assistant(f"action-{index}") for index in range(14))

    summary = _TestCompressor().fallback_summary(messages)

    assert "1. Called tool(s): tool0, tool1, tool2, tool3, tool4, tool5" in summary
    assert "2. action-0" in summary
    assert "12. action-10" in summary
    assert "action-11\n" not in summary.split("\t## File Operations", 1)[0]
    dropped = summary.split("## Last Dropped Turns\n", 1)[1].split("\n\n", 1)[0]
    assert dropped.splitlines() == [f"- ASSISTANT: action-{index}" for index in range(6, 14)]


def test_file_and_blocker_lists_preserve_dedupe_order_and_limits() -> None:
    calls: list[ToolCall] = []
    results: list[ToolResultMessage] = []
    for index in range(14):
        calls.append(
            ToolCall(
                id=f"read-{index}",
                name="read",
                arguments={"path": f"read-{index}.txt"},
            )
        )
        calls.append(
            ToolCall(
                id=f"write-{index}",
                name="write" if index % 2 == 0 else "edit",
                arguments={"file_path": f"modified-{index}.txt"},
            )
        )
        results.append(
            _tool_result(
                f"fatal unique-{index}",
                name="read",
                tool_call_id=f"read-{index}",
            )
        )
    calls.append(ToolCall(id="duplicate", name="read", arguments={"path": "read-0.txt"}))
    calls.append(ToolCall(id="empty", name="write", arguments={"path": "  "}))
    messages: list[Message] = [_assistant(tool_calls=calls), *results]

    summary = _TestCompressor().fallback_summary(messages)

    file_section = summary.split("\t## File Operations\n", 1)[1].split("\n\n\t## Active State", 1)[0]
    assert [f"- modified-{index}.txt" for index in range(12)] == [
        line.lstrip() for line in file_section.splitlines() if "- modified-" in line
    ]
    assert [f"- read-{index}.txt" for index in range(12)] == [
        line.lstrip() for line in file_section.splitlines() if "- read-" in line
    ]
    blocked = summary.split("## Blocked\n", 1)[1].split("\n\n", 1)[0]
    assert blocked.splitlines() == [f"- fatal unique-{index}" for index in range(5)]


def test_system_compatibility_role_and_blank_turns_preserve_remember_rules() -> None:
    system_like = _user("system context")
    object.__setattr__(system_like, "role", "system")
    messages: list[Message] = [
        _user("   "),
        _assistant(),
        system_like,
        _tool_result("", name="read", tool_call_id="missing"),
    ]

    summary = _TestCompressor().fallback_summary(messages)

    assert "Historical user ask: Unknown from deterministic fallback." in summary
    assert "\t1. [read] result elided (0 chars, 1 lines)" in summary
    assert "## Last Dropped Turns\n- SYSTEM: system context" in summary
    assert "USER:" not in summary.split("## Last Dropped Turns\n", 1)[1]
    assert "TOOLRESULT:" not in summary.split("## Last Dropped Turns\n", 1)[1]


def test_expand_subagent_result_summary_keeps_metadata_branch_bytes() -> None:
    text = "taskId: child-1\nsection: details\noffset: 20\ntruncated: true\nnextOffset: 40\ntotalChars: 90\n\npayload"

    summary = _TestCompressor().fallback_summary([_tool_result(text, name="expand_subagent_result")])

    assert (
        "1. [expand_subagent_result] child result expansion elided "
        "(97 chars, 1 lines; taskId=child-1 section: details offset: 20 truncated: true "
        "nextOffset: 40 totalChars: 90 payload, section=unknown, offset=0). "
        "Use expand_subagent_result "
        "with the same taskId/section/offset if the child detail is needed again."
    ) in summary


@pytest.mark.parametrize(
    "marker",
    ["error", "failed", "exception", "traceback", "timeout", "timed out", "fatal"],
)
def test_blocker_detection_is_case_insensitive_for_every_marker(marker: str) -> None:
    summary = _TestCompressor().fallback_summary([_tool_result(f"PREFIX {marker.upper()} suffix")])

    assert f"## Blocked\n- PREFIX {marker.upper()} suffix" in summary


def test_summary_sections_keep_their_exact_relative_order() -> None:
    summary = _TestCompressor().fallback_summary([_user("ask"), _assistant("done")])
    headings = [
        "## Historical Task Snapshot",
        "## Goal",
        "## Constraints & Preferences",
        "\t## Completed Actions",
        "\t## File Operations",
        "\t## Active State",
        "## Historical In-Progress State",
        "## Blocked",
        "## Key Decisions",
        "## Resolved Questions",
        "## Historical Pending User Asks",
        "## Relevant Files",
        "## Historical Remaining Work",
        "## Last Dropped Turns",
        "## Critical Context",
    ]

    assert [summary.index(heading) for heading in headings] == sorted(summary.index(heading) for heading in headings)


def test_large_summary_uses_the_exact_head_tail_budget_and_preserves_reason_tail() -> None:
    messages: list[Message] = [_user("initial /root/project")]
    messages.extend(_assistant(f"action-{index} " + (chr(65 + index % 26) * 1200)) for index in range(20))
    messages.extend(
        _tool_result(
            f"fatal-{index} /tmp/file-{index}.txt " + ("z" * 900),
            name="bash",
            tool_call_id=f"result-{index}",
        )
        for index in range(8)
    )

    summary = _TestCompressor().fallback_summary(
        messages,
        reason="tail provenance",
        recent_user_focus="latest focus",
    )

    assert len(summary) == 8000
    assert _digest(summary) == "6bbb70d02496b91c1f2df3f938064b26bb59a67dbb943a062cac8656cb4d2b53"
    assert summary.count("\n...[fallback summary middle truncated]...\n") == 1
    assert summary.index("\n...[fallback summary middle truncated]...\n") == 6757
    assert summary.endswith(
        "Summary generation was unavailable, so this is a best-effort deterministic fallback "
        "for 29 compacted message(s). Summary failure reason: tail provenance."
    )


def test_summary_does_not_mutate_messages_tool_calls_or_arguments() -> None:
    messages: list[Message] = [
        _user("inspect"),
        _assistant(
            tool_calls=[
                ToolCall(
                    id="read",
                    name="read",
                    arguments={"path": "README.md", "nested": {"values": [1, "two"]}},
                )
            ]
        ),
        _tool_result("contents", tool_call_id="read"),
    ]
    before = copy.deepcopy(messages)

    _TestCompressor().fallback_summary(messages, reason="down")

    assert messages == before


def test_non_mapping_tool_arguments_preserve_the_uncaught_attribute_error() -> None:
    call = ToolCall(id="bad", name="read", arguments={})
    object.__setattr__(call, "arguments", ["not", "a", "mapping"])

    with pytest.raises(AttributeError, match="'list' object has no attribute 'values'"):
        _TestCompressor().fallback_summary([_assistant(tool_calls=[call])])


def test_non_iterable_assistant_content_preserves_the_uncaught_type_error() -> None:
    message = _assistant("content")
    object.__setattr__(message, "content", None)

    with pytest.raises(TypeError, match="'NoneType' object is not iterable"):
        _TestCompressor().fallback_summary([message])
