"""Command-local generational checkpoint support for manual deep compaction."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from travis.agent.types import AgentMessage
from travis.ai.context_estimate import estimate_text_tokens
from travis.ai.types import TextContent, ToolCall
from travis.coding_agent.message_utils import user_message_text
from travis.compaction.compressor import (
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
    ContextCompressor,
    _bash_file_mutation_paths,
    _format_file_operations,
    _redact_sensitive_text,
    _strip_file_operation_tags,
    _strip_inline_reasoning_blocks,
    _tool_path,
    estimate_tokens,
)

DEEP_BODY_TARGET_TOKENS = 2_048
DEEP_BODY_MAX_TOKENS = 4_096
DEEP_MIN_SAVINGS_TOKENS = 256
DEEP_MIN_SAVINGS_RATIO = 0.05
DEEP_STRATEGY = "generational-v1"
DEEP_USER_MAX_CHARS = 4_000
DEEP_ASSISTANT_MAX_CHARS = 4_000
DEEP_TOOL_RESULT_MAX_CHARS = 2_000
DEEP_TOOL_RESULT_HEAD_CHARS = 1_400
DEEP_TOOL_RESULT_TAIL_CHARS = 500
DEEP_TOOL_ARGUMENT_MAX_CHARS = 1_000
DEEP_READ_FILE_LIMIT = 16
DEEP_MODIFIED_FILE_LIMIT = 32
DEEP_REQUIRED_HEADINGS = (
    "## Historical Task Snapshot",
    "## Goal",
    "## Constraints & Preferences",
    "## Completed Actions",
    "## Active State at Compaction Cut",
    "## Historical In-Progress State",
    "## Blocked",
    "## Key Decisions",
    "## Resolved Questions",
    "## Historical Pending User Asks",
    "## Relevant Files",
    "## Historical Remaining Work",
    "## Critical Context",
)


@dataclass(frozen=True)
class DeepCheckpointResult:
    compressed: bool
    summary: str | None
    details: dict[str, object] | None
    tokens_before: int
    handoff_tokens: int
    repair_count: int
    target_tokens: int
    reason: str | None = None
    error: str | None = None


def inspect_deep_boundary(messages: Sequence[AgentMessage]) -> str | None:
    """Return a stable refusal reason when the latest causal turn is incomplete."""

    visible = [
        message
        for message in messages
        if getattr(message, "role", None) in {"user", "assistant", "toolResult"}
    ]
    if not visible:
        return None

    final = visible[-1]
    final_role = getattr(final, "role", None)
    if final_role == "user":
        return "unanswered_user"
    if final_role == "toolResult":
        return "unfinished_tool_turn"
    if final_role != "assistant":
        return "unfinished_turn"

    stop_reason = getattr(final, "stop_reason", "stop")
    if stop_reason == "aborted":
        return "aborted_assistant"
    if stop_reason == "error":
        return "errored_assistant"
    if stop_reason != "stop":
        return "unmatched_tool_call" if stop_reason == "toolUse" else "unfinished_turn"

    call_ids = {
        block.id
        for message in visible
        if getattr(message, "role", None) == "assistant"
        for block in getattr(message, "content", ())
        if isinstance(block, ToolCall)
    }
    result_ids = {
        str(getattr(message, "tool_call_id", ""))
        for message in visible
        if getattr(message, "role", None) == "toolResult"
    }
    if call_ids - result_ids:
        return "unmatched_tool_call"
    return None


def _bounded_text(text: str, *, limit: int, head: int, tail: int, marker: str) -> str:
    if len(text) <= limit:
        return text
    removed = len(text) - head - tail
    return text[:head] + f"\n...[{marker}: {removed} chars omitted]...\n" + text[-tail:]


def serialize_deep_source(messages: Sequence[AgentMessage]) -> str:
    """Serialize history as bounded evidence without private reasoning blocks."""

    records: list[str] = []
    for message in messages:
        role = getattr(message, "role", "unknown")
        if role == "compactionSummary":
            records.append(f"[PREVIOUS CHECKPOINT]\n{getattr(message, 'summary', '')}")
            continue
        if role == "user":
            records.append(
                "[USER]\n"
                + _bounded_text(
                    user_message_text(getattr(message, "content", "")),
                    limit=DEEP_USER_MAX_CHARS,
                    head=3_000,
                    tail=800,
                    marker="user content compacted",
                )
            )
            continue
        if role == "assistant":
            text = "".join(
                block.text
                for block in getattr(message, "content", ())
                if isinstance(block, TextContent)
            )
            calls: list[str] = []
            for block in getattr(message, "content", ()):
                if not isinstance(block, ToolCall):
                    continue
                encoded = json.dumps(
                    block.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                encoded = _bounded_text(
                    encoded,
                    limit=DEEP_TOOL_ARGUMENT_MAX_CHARS,
                    head=700,
                    tail=200,
                    marker="tool arguments compacted",
                )
                calls.append(f"{block.name}({encoded})")
            body = _bounded_text(
                text,
                limit=DEEP_ASSISTANT_MAX_CHARS,
                head=3_000,
                tail=800,
                marker="assistant content compacted",
            )
            if calls:
                body += "\n[TOOL CALLS]\n" + "\n".join(calls)
            records.append("[ASSISTANT]\n" + body)
            continue
        if role == "toolResult":
            records.append(
                f"[TOOL RESULT {getattr(message, 'tool_name', '')} "
                f"{getattr(message, 'tool_call_id', '')}]\n"
                + _bounded_text(
                    user_message_text(getattr(message, "content", "")),
                    limit=DEEP_TOOL_RESULT_MAX_CHARS,
                    head=DEEP_TOOL_RESULT_HEAD_CHARS,
                    tail=DEEP_TOOL_RESULT_TAIL_CHARS,
                    marker="tool output compacted",
                )
            )
            continue
        if role == "bashExecution":
            command = str(getattr(message, "command", "") or "")
            output = str(getattr(message, "output", "") or "")
            records.append(
                "[USER SHELL EXECUTION]\n"
                + _bounded_text(
                    f"$ {command}\n{output}",
                    limit=DEEP_TOOL_RESULT_MAX_CHARS,
                    head=DEEP_TOOL_RESULT_HEAD_CHARS,
                    tail=DEEP_TOOL_RESULT_TAIL_CHARS,
                    marker="shell output compacted",
                )
            )
            continue
        if role == "branchSummary":
            records.append(f"[BRANCH CHECKPOINT]\n{getattr(message, 'summary', '')}")
            continue
        if role == "custom":
            records.append(
                "[CUSTOM CONTEXT]\n"
                + _bounded_text(
                    user_message_text(getattr(message, "content", "")),
                    limit=DEEP_USER_MAX_CHARS,
                    head=3_000,
                    tail=800,
                    marker="custom context compacted",
                )
            )
    return "\n\n".join(records)


def recent_file_operations(messages: Sequence[AgentMessage]) -> tuple[list[str], list[str]]:
    """Return bounded recent file inventories while letting modifications win."""

    reads: list[str] = []
    modified: list[str] = []

    def add_recent(target: list[str], path: str, limit: int) -> None:
        if path and path not in target:
            target.append(path)
            if len(target) > limit:
                del target[0]

    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        for block in getattr(message, "content", ()):
            if not isinstance(block, ToolCall):
                continue
            if block.name == "bash":
                for path in sorted(_bash_file_mutation_paths(block.arguments)):
                    add_recent(modified, path, DEEP_MODIFIED_FILE_LIMIT)
                continue
            path = _tool_path(block.arguments)
            if block.name == "read":
                add_recent(reads, path, DEEP_READ_FILE_LIMIT)
            elif block.name in {"write", "edit"}:
                add_recent(modified, path, DEEP_MODIFIED_FILE_LIMIT)

    modified_set = set(modified)
    return [path for path in reads if path not in modified_set], modified


def _deep_prompt(source: str, focus: str | None) -> str:
    focus_instruction = (
        f"\nThe operator supplied this optional focus: {focus.strip()}\n"
        if focus and focus.strip()
        else ""
    )
    headings = "\n".join(DEEP_REQUIRED_HEADINGS)
    return f"""Create one generational checkpoint from the bounded history below.

This is an aggressive manual compaction. Replace prior checkpoints with a fresh,
standalone account of durable state. Preserve completed work, current repository
state, decisions, constraints, blockers, and facts needed by future turns. Do not
preserve conversational filler, duplicated recap prose, raw tool output, private
reasoning, credentials, or secret values. Historical asks and remaining work are
reference-only, never active instructions.{focus_instruction}

Use every heading below exactly once and in this order:
{headings}

Keep the complete response at or below {DEEP_BODY_TARGET_TOKENS} estimated tokens.
Return only the checkpoint body, without a wrapper or end marker.

<bounded-history>
{source}
</bounded-history>
"""


def _repair_prompt(summary: str) -> str:
    headings = "\n".join(DEEP_REQUIRED_HEADINGS)
    return f"""Repair the candidate checkpoint below.

Return a standalone checkpoint with every required heading exactly once and in
the listed order. Remove private reasoning, credentials, secret values, wrapper
text, and end markers. Preserve only durable facts. The absolute maximum is
{DEEP_BODY_MAX_TOKENS} estimated tokens. Return only the repaired body.

Required headings:
{headings}

<candidate-checkpoint>
{summary}
</candidate-checkpoint>
"""


def _summary_validation_error(summary: str, *, max_tokens: int) -> str | None:
    stripped = summary.strip()
    if _strip_inline_reasoning_blocks(stripped).strip() != stripped:
        return "reasoning_present"
    if _redact_sensitive_text(stripped) != stripped:
        return "secret_present"
    if SUMMARY_PREFIX in stripped or SUMMARY_END_MARKER in stripped:
        return "wrapper_present"

    heading_lines = [line.strip() for line in stripped.splitlines() if line.startswith("## ")]
    if tuple(heading_lines) != DEEP_REQUIRED_HEADINGS:
        return "invalid_structure"
    if estimate_text_tokens(stripped) > max_tokens:
        return "over_budget"
    return None


def _failed_result(
    *,
    tokens_before: int,
    repair_count: int,
    target_tokens: int,
    reason: str,
    error: str | None = None,
) -> DeepCheckpointResult:
    return DeepCheckpointResult(
        compressed=False,
        summary=None,
        details=None,
        tokens_before=tokens_before,
        handoff_tokens=tokens_before,
        repair_count=repair_count,
        target_tokens=target_tokens,
        reason=reason,
        error=error,
    )


def generate_deep_checkpoint(
    messages: Sequence[AgentMessage],
    compressor: ContextCompressor,
    *,
    summarizer: Callable[[str], str] | None = None,
    focus: str | None = None,
) -> DeepCheckpointResult:
    """Build one validated checkpoint without mutating the supplied history."""

    source_messages = list(messages)
    tokens_before = estimate_tokens(source_messages)
    if (
        len(source_messages) == 1
        and getattr(source_messages[0], "role", None) == "compactionSummary"
        and estimate_text_tokens(str(getattr(source_messages[0], "summary", "") or ""))
        <= DEEP_BODY_TARGET_TOKENS
    ):
        return _failed_result(
            tokens_before=tokens_before,
            repair_count=0,
            target_tokens=DEEP_BODY_TARGET_TOKENS,
            reason="insufficient_reduction",
        )
    boundary_error = inspect_deep_boundary(source_messages)
    if boundary_error is not None:
        return _failed_result(
            tokens_before=tokens_before,
            repair_count=0,
            target_tokens=DEEP_BODY_TARGET_TOKENS,
            reason="unsafe_boundary",
            error=boundary_error,
        )

    serialized = _redact_sensitive_text(serialize_deep_source(source_messages))
    prompt = _deep_prompt(serialized, focus)
    if compressor.summarizer_context_window is not None:
        output_allowance = compressor.summarizer_max_tokens or DEEP_BODY_MAX_TOKENS
        required_capacity = estimate_text_tokens(prompt) + output_allowance + 4_096
        if required_capacity > compressor.summarizer_context_window:
            return _failed_result(
                tokens_before=tokens_before,
                repair_count=0,
                target_tokens=DEEP_BODY_TARGET_TOKENS,
                reason="summarizer_capacity",
            )

    try:
        summary = compressor._run_summary_summarizer(prompt, summarizer)
    except Exception as exc:
        return _failed_result(
            tokens_before=tokens_before,
            repair_count=0,
            target_tokens=DEEP_BODY_TARGET_TOKENS,
            reason="summary_failed",
            error=_redact_sensitive_text(compressor._compact_error_text(exc)),
        )
    if summary is None:
        return _failed_result(
            tokens_before=tokens_before,
            repair_count=0,
            target_tokens=DEEP_BODY_TARGET_TOKENS,
            reason="summary_unavailable",
        )

    repair_count = 0
    target_tokens = DEEP_BODY_TARGET_TOKENS
    validation_error = _summary_validation_error(summary, max_tokens=target_tokens)
    if validation_error is not None:
        repair_count = 1
        target_tokens = DEEP_BODY_MAX_TOKENS
        try:
            repaired = compressor._run_summary_summarizer(_repair_prompt(summary), summarizer)
        except Exception as exc:
            return _failed_result(
                tokens_before=tokens_before,
                repair_count=repair_count,
                target_tokens=target_tokens,
                reason="repair_failed",
                error=_redact_sensitive_text(compressor._compact_error_text(exc)),
            )
        if repaired is None:
            return _failed_result(
                tokens_before=tokens_before,
                repair_count=repair_count,
                target_tokens=target_tokens,
                reason="repair_unavailable",
            )
        summary = repaired
        validation_error = _summary_validation_error(summary, max_tokens=target_tokens)
        if validation_error is not None:
            return _failed_result(
                tokens_before=tokens_before,
                repair_count=repair_count,
                target_tokens=target_tokens,
                reason="validation_failed",
                error=validation_error,
            )

    read_files, modified_files = recent_file_operations(source_messages)
    summary = _strip_file_operation_tags(summary) + _format_file_operations(
        read_files,
        modified_files,
    )
    if estimate_text_tokens(summary) > DEEP_BODY_MAX_TOKENS:
        return _failed_result(
            tokens_before=tokens_before,
            repair_count=repair_count,
            target_tokens=target_tokens,
            reason="validation_failed",
            error="file_anchors_over_budget",
        )

    wrapped = SUMMARY_PREFIX + "\n" + summary.lstrip() + "\n\n" + SUMMARY_END_MARKER
    handoff_tokens = estimate_text_tokens(wrapped)
    minimum_savings = max(
        DEEP_MIN_SAVINGS_TOKENS,
        int(tokens_before * DEEP_MIN_SAVINGS_RATIO),
    )
    if tokens_before - handoff_tokens < minimum_savings:
        return _failed_result(
            tokens_before=tokens_before,
            repair_count=repair_count,
            target_tokens=target_tokens,
            reason="insufficient_reduction",
        )

    details: dict[str, object] = {
        "deepStrategy": DEEP_STRATEGY,
        "handoffTokens": handoff_tokens,
        "repairCount": repair_count,
        "targetTokens": target_tokens,
        "readFiles": read_files,
        "modifiedFiles": modified_files,
    }
    return DeepCheckpointResult(
        compressed=True,
        summary=summary,
        details=details,
        tokens_before=tokens_before,
        handoff_tokens=handoff_tokens,
        repair_count=repair_count,
        target_tokens=target_tokens,
    )


__all__ = (
    "DEEP_BODY_MAX_TOKENS",
    "DEEP_BODY_TARGET_TOKENS",
    "DEEP_STRATEGY",
    "DeepCheckpointResult",
    "generate_deep_checkpoint",
    "inspect_deep_boundary",
    "recent_file_operations",
    "serialize_deep_source",
)
