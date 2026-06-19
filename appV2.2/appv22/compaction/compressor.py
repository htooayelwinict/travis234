"""Hermes dual-pass context compaction.

Port of hermes-agent/agent/context_compressor.py `ContextCompressor.compress`:
Pass 1 = deterministic prune (dedup identical tool outputs, summarize old tool
results, strip images, truncate huge tool-call args). Pass 2 = LLM structured
summary (iterative-update vs from-scratch). Head + token-budgeted tail protected.
Anti-thrash: skip after two consecutive <10%-effective passes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Optional

from appv22.ai.types import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    now_ms,
)

CHARS_PER_TOKEN = 4
SUMMARY_PREFIX = "[CONTEXT COMPACTION - REFERENCE ONLY] The following summarizes earlier conversation. "
_TOOL_RESULT_SUMMARY_MIN = 200
_TOOL_ARGS_MAX = 500

_SUMMARY_TEMPLATE_SECTIONS = (
    "## Goal",
    "## Completed Actions",
    "## Active State",
    "## Key Decisions",
    "## Relevant Files",
    "## Remaining Work",
)

Summarizer = Callable[[str], str]


def estimate_tokens(messages: list[Message]) -> int:
    return sum(len(_message_text(m)) for m in messages) // CHARS_PER_TOKEN


def _message_text(message: Message) -> str:
    role = getattr(message, "role", None)
    if role == "user":
        content = message.content
        return content if isinstance(content, str) else "".join(_block_text(b) for b in content)
    if role == "assistant":
        parts = []
        for block in message.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
            elif isinstance(block, ToolCall):
                parts.append(block.name + str(block.arguments))
        return "".join(parts)
    if role == "toolResult":
        return "".join(_block_text(b) for b in message.content)
    return ""


def _block_text(block) -> str:
    return block.text if isinstance(block, TextContent) else ""


@dataclass
class CompressionResult:
    messages: list[Message]
    compressed: bool
    savings_pct: float


class ContextCompressor:
    def __init__(
        self,
        *,
        context_length: int = 32000,
        threshold_percent: float = 0.5,
        protect_first_n: int = 2,
        protect_last_n: int = 8,
        summary_target_ratio: float = 0.20,
        summarizer: Optional[Summarizer] = None,
    ) -> None:
        self.context_length = context_length
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.summary_target_ratio = summary_target_ratio
        self._summarizer = summarizer
        self._previous_summary: str | None = None
        self._ineffective_compression_count = 0
        self.last_compression_savings_pct = 0.0
        self.compression_count = 0

    @property
    def threshold_tokens(self) -> int:
        return int(self.context_length * self.threshold_percent)

    @property
    def tail_token_budget(self) -> int:
        return int(self.threshold_tokens * self.summary_target_ratio)

    def should_compress(self, tokens: int) -> bool:
        if tokens < self.threshold_tokens:
            return False
        if self._ineffective_compression_count >= 2:
            return False
        return True

    # --- Pass 1: deterministic prune ---

    def prune_old_tool_results(self, messages: list[Message]) -> list[Message]:
        pruned = copy.deepcopy(messages)
        boundary = max(0, len(pruned) - self.protect_last_n)

        # 1. Dedup identical tool outputs (keep newest).
        seen: dict[str, int] = {}
        for index in range(len(pruned) - 1, -1, -1):
            message = pruned[index]
            if getattr(message, "role", None) != "toolResult":
                continue
            text = _message_text(message)
            if len(text) < _TOOL_RESULT_SUMMARY_MIN:
                continue
            if text in seen:
                pruned[index].content = [TextContent(text="[Duplicate tool output - same content as a more recent call]")]
            else:
                seen[text] = index

        # 2. Summarize old tool results before the protected tail.
        for index in range(boundary):
            message = pruned[index]
            if getattr(message, "role", None) != "toolResult":
                continue
            text = _message_text(message)
            if len(text) > _TOOL_RESULT_SUMMARY_MIN:
                pruned[index].content = [TextContent(text=self._summarize_tool_result(message, text))]
            pruned[index].content = [b for b in pruned[index].content if isinstance(b, TextContent)]

        # 3. Truncate huge tool-call args in assistant messages before the tail.
        for index in range(boundary):
            message = pruned[index]
            if getattr(message, "role", None) != "assistant":
                continue
            for block in message.content:
                if isinstance(block, ToolCall):
                    encoded = str(block.arguments)
                    if len(encoded) > _TOOL_ARGS_MAX:
                        block.arguments = {"_truncated": f"{len(encoded)} chars of arguments elided"}
        return pruned

    def _summarize_tool_result(self, message: ToolResultMessage, text: str) -> str:
        line_count = text.count("\n") + 1
        return f"[{message.tool_name}] result elided ({len(text)} chars, {line_count} lines)"

    # --- Pass 2: LLM structured summary ---

    def generate_summary(self, middle: list[Message], summarizer: Optional[Summarizer]) -> str:
        serialized = self._serialize_for_summary(middle)
        if self._previous_summary:
            prompt = (
                "You are updating a context compaction summary. PRESERVE existing info, ADD new actions, "
                "and move In-Progress items to Completed when done.\n\n"
                f"EXISTING SUMMARY:\n{self._previous_summary}\n\nNEW CONVERSATION:\n{serialized}\n\n"
                f"Produce an updated summary using these sections:\n" + "\n".join(_SUMMARY_TEMPLATE_SECTIONS)
            )
        else:
            prompt = (
                "Summarize the following conversation for context compaction using these sections:\n"
                + "\n".join(_SUMMARY_TEMPLATE_SECTIONS)
                + f"\n\nCONVERSATION:\n{serialized}"
            )
        if summarizer is not None:
            return summarizer(prompt)
        return self._static_fallback_summary(middle)

    def _serialize_for_summary(self, middle: list[Message]) -> str:
        lines = []
        for message in middle:
            role = getattr(message, "role", "?")
            text = _message_text(message)[:6000]
            lines.append(f"[{role}] {text}")
        return "\n".join(lines)

    def _static_fallback_summary(self, middle: list[Message]) -> str:
        roles = [getattr(m, "role", "?") for m in middle]
        return (
            "## Goal\n(deterministic fallback summary)\n"
            f"## Completed Actions\nCompacted {len(middle)} earlier messages ({', '.join(roles)})."
        )

    # --- Orchestrator ---

    def compress(self, messages: list[Message], summarizer: Optional[Summarizer] = None) -> CompressionResult:
        summarizer = summarizer or self._summarizer
        before = estimate_tokens(messages)

        pruned = self.prune_old_tool_results(messages)
        head_end = min(self.protect_first_n, len(pruned))
        tail_start = self._find_tail_start(pruned, head_end)

        if tail_start <= head_end:
            after = estimate_tokens(pruned)
            return CompressionResult(messages=pruned, compressed=False, savings_pct=_savings(before, after))

        middle = pruned[head_end:tail_start]
        summary_text = self.generate_summary(middle, summarizer)
        summary_message = UserMessage(content=SUMMARY_PREFIX + summary_text, timestamp=now_ms())
        result = [*pruned[:head_end], summary_message, *pruned[tail_start:]]

        after = estimate_tokens(result)
        savings = _savings(before, after)
        self.last_compression_savings_pct = savings
        if savings < 10:
            self._ineffective_compression_count += 1
        else:
            self._ineffective_compression_count = 0
        self._previous_summary = summary_text
        self.compression_count += 1
        return CompressionResult(messages=result, compressed=True, savings_pct=savings)

    def _find_tail_start(self, messages: list[Message], head_end: int) -> int:
        budget = self.tail_token_budget
        accumulated = 0
        index = len(messages)
        count = 0
        while index > head_end:
            candidate = index - 1
            tokens = len(_message_text(messages[candidate])) // CHARS_PER_TOKEN
            if accumulated + tokens > budget and count >= self.protect_last_n:
                break
            accumulated += tokens
            count += 1
            index = candidate
        return index


def _savings(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return (before - after) / before * 100.0
