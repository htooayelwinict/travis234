from __future__ import annotations

from travis.ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from travis.coding_agent.deep_compaction_command import (
    DEEP_BODY_MAX_TOKENS,
    generate_deep_checkpoint,
    inspect_deep_boundary,
    recent_file_operations,
    serialize_deep_source,
)
from travis.compaction.compressor import ContextCompressor

VALID_SUMMARY = """## Historical Task Snapshot
None.
## Goal
Preserve the completed checkpoint.
## Constraints & Preferences
(none)
## Completed Actions
1. Verified the requested state.
## Active State at Compaction Cut
Idle.
## Historical In-Progress State
None.
## Blocked
(none)
## Key Decisions
- Use an atomic checkpoint.
## Resolved Questions
None.
## Historical Pending User Asks
None.
## Relevant Files
(none)
## Historical Remaining Work
None.
## Critical Context
Checkpoint complete.
"""


def _assistant(
    text: str = "done",
    *,
    stop_reason: str = "stop",
    calls: tuple[ToolCall, ...] = (),
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text), *calls],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=empty_usage(),
        stop_reason=stop_reason,
        timestamp=now_ms(),
    )


def _large_completed_messages():
    return [
        UserMessage(content="finish the checkpoint", timestamp=now_ms()),
        _assistant("completed evidence " + ("x" * 20_000)),
    ]


def test_deep_boundary_accepts_a_completed_turn() -> None:
    messages = [UserMessage(content="inspect", timestamp=now_ms()), _assistant("inspection complete")]

    assert inspect_deep_boundary(messages) is None


def test_deep_boundary_refuses_an_unanswered_user() -> None:
    messages = [UserMessage(content="unfinished", timestamp=now_ms())]

    assert inspect_deep_boundary(messages) == "unanswered_user"


def test_deep_boundary_refuses_an_aborted_final_assistant() -> None:
    messages = [UserMessage(content="run", timestamp=now_ms()), _assistant(stop_reason="aborted")]

    assert inspect_deep_boundary(messages) == "aborted_assistant"


def test_deep_boundary_refuses_an_errored_final_assistant() -> None:
    messages = [UserMessage(content="run", timestamp=now_ms()), _assistant(stop_reason="error")]

    assert inspect_deep_boundary(messages) == "errored_assistant"


def test_deep_boundary_refuses_an_unmatched_tool_call() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "large.log"})
    messages = [
        UserMessage(content="read it", timestamp=now_ms()),
        _assistant(stop_reason="toolUse", calls=(call,)),
    ]

    assert inspect_deep_boundary(messages) == "unmatched_tool_call"


def test_deep_boundary_refuses_a_tool_result_without_final_assistant() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "large.log"})
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="result")],
        is_error=False,
        timestamp=now_ms(),
    )
    messages = [
        UserMessage(content="read it", timestamp=now_ms()),
        _assistant(stop_reason="toolUse", calls=(call,)),
        result,
    ]

    assert inspect_deep_boundary(messages) == "unfinished_tool_turn"


def test_deep_boundary_accepts_a_completed_tool_turn() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "large.log"})
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="result")],
        is_error=False,
        timestamp=now_ms(),
    )
    messages = [
        UserMessage(content="read it", timestamp=now_ms()),
        _assistant(stop_reason="toolUse", calls=(call,)),
        result,
        _assistant("read complete"),
    ]

    assert inspect_deep_boundary(messages) is None


def test_deep_serialization_bounds_tool_output_and_excludes_reasoning() -> None:
    call = ToolCall(id="read-1", name="read", arguments={"path": "logs/huge.log"})
    assistant = AssistantMessage(
        content=[ThinkingContent(thinking="PRIVATE-REASONING" * 500), call],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    result = ToolResultMessage(
        tool_call_id="read-1",
        tool_name="read",
        content=[TextContent(text="HEAD\n" + "x" * 20_000 + "\nTAIL")],
        is_error=False,
        timestamp=now_ms(),
    )

    serialized = serialize_deep_source(
        [UserMessage(content="inspect", timestamp=now_ms()), assistant, result, _assistant()]
    )

    assert "PRIVATE-REASONING" not in serialized
    assert "HEAD" in serialized and "TAIL" in serialized
    assert "tool output compacted" in serialized
    assert len(serialized) < 8_000


def test_recent_file_operations_are_bounded_and_modified_wins() -> None:
    messages = []
    for index in range(40):
        messages.append(
            _assistant(
                stop_reason="toolUse",
                calls=(
                    ToolCall(
                        id=f"read-{index}",
                        name="read",
                        arguments={"path": f"src/read_{index}.py"},
                    ),
                ),
            )
        )
        messages.append(
            _assistant(
                stop_reason="toolUse",
                calls=(
                    ToolCall(
                        id=f"edit-{index}",
                        name="edit",
                        arguments={"path": f"src/edit_{index}.py"},
                    ),
                ),
            )
        )

    read_files, modified_files = recent_file_operations(messages)

    assert len(read_files) == 16
    assert len(modified_files) == 32
    assert read_files[0] == "src/read_24.py"
    assert read_files[-1] == "src/read_39.py"
    assert modified_files[0] == "src/edit_8.py"
    assert modified_files[-1] == "src/edit_39.py"


def test_deep_generation_accepts_a_bounded_valid_summary() -> None:
    compressor = ContextCompressor(context_length=1_048_576)

    result = generate_deep_checkpoint(
        _large_completed_messages(),
        compressor,
        summarizer=lambda _prompt: VALID_SUMMARY,
    )

    assert result.compressed is True
    assert result.summary is not None
    assert result.handoff_tokens <= DEEP_BODY_MAX_TOKENS
    assert result.repair_count == 0
    assert result.details is not None
    assert result.details["deepStrategy"] == "generational-v1"


def test_deep_generation_repairs_an_oversized_summary_once() -> None:
    calls: list[str] = []

    def summarize(prompt: str) -> str:
        calls.append(prompt)
        return (VALID_SUMMARY + "x" * 20_000) if len(calls) == 1 else VALID_SUMMARY

    result = generate_deep_checkpoint(
        _large_completed_messages(),
        ContextCompressor(context_length=1_048_576),
        summarizer=summarize,
    )

    assert result.compressed is True
    assert result.repair_count == 1
    assert len(calls) == 2


def test_deep_generation_rolls_back_after_failed_repair() -> None:
    calls: list[str] = []

    def summarize(prompt: str) -> str:
        calls.append(prompt)
        return VALID_SUMMARY + "x" * 20_000

    result = generate_deep_checkpoint(
        _large_completed_messages(),
        ContextCompressor(context_length=1_048_576),
        summarizer=summarize,
    )

    assert result.compressed is False
    assert result.reason == "validation_failed"
    assert result.repair_count == 1
    assert len(calls) == 2


def test_deep_generation_refuses_secret_shaped_output() -> None:
    leaked = VALID_SUMMARY + "\nOPENAI_API_KEY=sk-secret-value"

    result = generate_deep_checkpoint(
        _large_completed_messages(),
        ContextCompressor(context_length=1_048_576),
        summarizer=lambda _prompt: leaked,
    )

    assert result.compressed is False
    assert result.reason == "validation_failed"
    assert result.error == "secret_present"
