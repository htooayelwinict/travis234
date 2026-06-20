"""Hermes dual-pass context compaction.

Port of hermes-agent/agent/context_compressor.py `ContextCompressor.compress`:
Pass 1 = deterministic prune (dedup identical tool outputs, summarize old tool
results, strip images, truncate huge tool-call args). Pass 2 = LLM structured
summary (iterative-update vs from-scratch). Head + token-budgeted tail protected.
Anti-thrash: skip after two consecutive <10%-effective passes.
"""

from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from appv22.ai.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)

CHARS_PER_TOKEN = 4
SUMMARY_PREFIX = "[CONTEXT COMPACTION - REFERENCE ONLY] The following summarizes earlier conversation. "
LEGACY_SUMMARY_PREFIX = "[CONTEXT SUMMARY]:"
SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
_TOOL_RESULT_SUMMARY_MIN = 200
_TOOL_ARGS_MAX = 500
_IMAGE_STRIPPED_TEXT = "[Attached image - stripped after compression]"
_MAX_TAIL_MESSAGE_FLOOR = 8
_MESSAGE_TOKEN_OVERHEAD = 10
_AUX_MODEL_ERROR_MAX_CHARS = 220
_SUMMARY_FAILURE_COOLDOWN_SECONDS = 60.0
HISTORICAL_TASK_HEADING = "## Historical Task Snapshot"
HISTORICAL_IN_PROGRESS_HEADING = "## Historical In-Progress State"
HISTORICAL_PENDING_ASKS_HEADING = "## Historical Pending User Asks"
HISTORICAL_REMAINING_WORK_HEADING = "## Historical Remaining Work"
_SECRET_VALUE = "[REDACTED]"
_KNOWN_SECRET_RE = re.compile(
    r"\b(?:"
    r"sk-proj-[A-Za-z0-9_-]{12,}|"
    r"sk-or-v1-[A-Za-z0-9_-]{12,}|"
    r"sk-[A-Za-z0-9_-]{12,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|"
    r"AIza[0-9A-Za-z_-]{16,}|"
    r"pplx-[A-Za-z0-9]{10,}|"
    r"fal_[A-Za-z0-9]{10,}"
    r")\b"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|CONNECTION[_-]?STRING)[A-Z0-9_]*)"
    r"\s*=\s*(['\"]?)([^'\"\s]+)(\2)",
    re.IGNORECASE,
)
_JSON_SECRET_RE = re.compile(
    r'("?[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|credential|connection[_-]?string)[A-Za-z0-9_-]*"?\s*:\s*)"[^"]+"',
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(r"(Authorization\s*:\s*Bearer\s+)[^\s]+", re.IGNORECASE)

_SUMMARIZER_PREAMBLE = (
    "You are a summarization agent creating a context checkpoint. "
    "Treat the conversation turns below as source material for a compact record of prior work. "
    "Produce only the structured summary; do not add a greeting, preamble, or prefix. "
    "Write the summary in the same language the user was using in the conversation — do not translate "
    "or switch to English. "
    "NEVER include API keys, tokens, passwords, secrets, credentials, or connection strings in the "
    "summary — replace any that appear with [REDACTED]. Note that the user had credentials present, "
    "but do not preserve their values."
)

Summarizer = Callable[[str], str]


def estimate_tokens(messages: list[Message]) -> int:
    return sum(len(_message_text(m)) for m in messages) // CHARS_PER_TOKEN


def _message_text(message: Message) -> str:
    role = getattr(message, "role", None)
    if role == "system":
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else str(content or "")
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


def _redact_sensitive_text(text: str) -> str:
    if text is None:
        return text
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    text = _KNOWN_SECRET_RE.sub(_SECRET_VALUE, text)
    text = _AUTH_HEADER_RE.sub(r"\1" + _SECRET_VALUE, text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={m.group(2)}{_SECRET_VALUE}{m.group(4)}", text)
    text = _JSON_SECRET_RE.sub(lambda m: f'{m.group(1)}"{_SECRET_VALUE}"', text)
    return text


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
        summary_summarizer: Optional[Summarizer] = None,
        model: str = "main",
        summary_model_override: str | None = None,
        abort_on_summary_failure: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self.summary_model = summary_model_override or ""
        self.context_length = context_length
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.summary_target_ratio = summary_target_ratio
        self._summarizer = summarizer
        self._summary_summarizer = summary_summarizer
        self.abort_on_summary_failure = abort_on_summary_failure
        self._clock = clock
        self._previous_summary: str | None = None
        self._ineffective_compression_count = 0
        self._summary_failure_cooldown_until = 0.0
        self._last_summary_error: str | None = None
        self._last_summary_dropped_count = 0
        self._last_summary_fallback_used = False
        self._last_compress_aborted = False
        self._last_aux_model_failure_error: str | None = None
        self._last_aux_model_failure_model: str | None = None
        self._summary_model_fallen_back = False
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.last_real_prompt_tokens = 0
        self.last_compression_rough_tokens = 0
        self.last_rough_tokens_when_real_prompt_fit = 0
        self.awaiting_real_usage_after_compression = False
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

    def update_from_response(self, usage: dict) -> None:
        self.last_prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        self.last_total_tokens = int(
            usage.get(
                "total_tokens",
                self.last_prompt_tokens + self.last_completion_tokens,
            )
            or 0
        )
        if self.last_prompt_tokens > 0:
            self.last_real_prompt_tokens = self.last_prompt_tokens
            if self.last_prompt_tokens < self.threshold_tokens:
                if self.awaiting_real_usage_after_compression and self.last_compression_rough_tokens > 0:
                    self.last_rough_tokens_when_real_prompt_fit = self.last_compression_rough_tokens
            else:
                self.last_rough_tokens_when_real_prompt_fit = 0
        self.awaiting_real_usage_after_compression = False

    def should_defer_preflight_to_real_usage(self, rough_tokens: int) -> bool:
        if rough_tokens < self.threshold_tokens:
            return False
        if self.last_real_prompt_tokens <= 0:
            return False
        if self.last_real_prompt_tokens >= self.threshold_tokens:
            return False

        baseline = self.last_rough_tokens_when_real_prompt_fit or self.last_compression_rough_tokens
        if baseline <= 0:
            return False

        growth = max(0, rough_tokens - baseline)
        tolerated_growth = max(4096, int(self.threshold_tokens * 0.05))
        if growth > tolerated_growth:
            return False

        self.last_rough_tokens_when_real_prompt_fit = max(baseline, rough_tokens)
        return True

    def _summary_failure_in_cooldown(self) -> bool:
        return self._clock() < self._summary_failure_cooldown_until

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

    # --- Tool-call/result boundary safety ---

    @staticmethod
    def _tool_calls(message: Message) -> list[ToolCall]:
        if getattr(message, "role", None) != "assistant":
            return []
        return [block for block in message.content if isinstance(block, ToolCall)]

    def _sanitize_tool_pairs(self, messages: list[Message]) -> list[Message]:
        """Keep compressed transcripts valid after middle turns are removed."""
        surviving_call_ids = {
            call.id
            for message in messages
            for call in self._tool_calls(message)
            if call.id
        }
        result_call_ids = {
            message.tool_call_id
            for message in messages
            if getattr(message, "role", None) == "toolResult" and message.tool_call_id
        }

        orphaned_results = result_call_ids - surviving_call_ids
        if orphaned_results:
            messages = [
                message
                for message in messages
                if not (
                    getattr(message, "role", None) == "toolResult"
                    and message.tool_call_id in orphaned_results
                )
            ]

        missing_results = surviving_call_ids - result_call_ids
        if not missing_results:
            return messages

        patched: list[Message] = []
        for message in messages:
            patched.append(message)
            for call in self._tool_calls(message):
                if call.id in missing_results:
                    patched.append(
                        ToolResultMessage(
                            tool_call_id=call.id,
                            tool_name=call.name,
                            content=[
                                TextContent(
                                    text="[Result from earlier conversation - see context summary above]"
                                )
                            ],
                            is_error=False,
                            timestamp=now_ms(),
                        )
                    )
        return patched

    def _align_boundary_forward(self, messages: list[Message], index: int) -> int:
        while index < len(messages) and getattr(messages[index], "role", None) == "toolResult":
            index += 1
        return index

    def _align_boundary_backward(self, messages: list[Message], index: int) -> int:
        if index <= 0 or index >= len(messages):
            return index
        check = index - 1
        while check >= 0 and getattr(messages[check], "role", None) == "toolResult":
            check -= 1
        if check >= 0 and self._tool_calls(messages[check]):
            return check
        return index

    def _find_last_user_message_index(self, messages: list[Message], head_end: int) -> int:
        for index in range(len(messages) - 1, head_end - 1, -1):
            if getattr(messages[index], "role", None) == "user":
                return index
        return -1

    def _find_last_assistant_message_index(self, messages: list[Message], head_end: int) -> int:
        last_any = -1
        for index in range(len(messages) - 1, head_end - 1, -1):
            message = messages[index]
            if getattr(message, "role", None) != "assistant":
                continue
            if last_any < 0:
                last_any = index
            if any(isinstance(block, TextContent) and block.text.strip() for block in message.content):
                return index
        return last_any

    def _ensure_last_user_message_in_tail(self, messages: list[Message], tail_start: int, head_end: int) -> int:
        last_user = self._find_last_user_message_index(messages, head_end)
        if last_user < 0 or last_user >= tail_start:
            return tail_start
        return max(last_user, head_end + 1)

    def _ensure_last_assistant_message_in_tail(self, messages: list[Message], tail_start: int, head_end: int) -> int:
        last_assistant = self._find_last_assistant_message_index(messages, head_end)
        if last_assistant < 0 or last_assistant >= tail_start:
            return tail_start
        return max(self._align_boundary_backward(messages, last_assistant), head_end + 1)

    def _protect_head_size(self, messages: list[Message]) -> int:
        system_head = 1 if messages and getattr(messages[0], "role", None) == "system" else 0
        return min(len(messages), system_head + self.protect_first_n)

    @staticmethod
    def _content_has_images(content) -> bool:
        return isinstance(content, list) and any(isinstance(block, ImageContent) for block in content)

    @staticmethod
    def _strip_images_from_content(content):
        if not isinstance(content, list) or not any(isinstance(block, ImageContent) for block in content):
            return content
        return [
            TextContent(text=_IMAGE_STRIPPED_TEXT) if isinstance(block, ImageContent) else block
            for block in content
        ]

    def _strip_historical_media(self, messages: list[Message]) -> list[Message]:
        anchor = -1
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if getattr(message, "role", None) == "user" and self._content_has_images(message.content):
                anchor = index
                break
        if anchor <= 0:
            return messages

        changed = False
        stripped: list[Message] = []
        for index, message in enumerate(messages):
            if index >= anchor or not self._content_has_images(getattr(message, "content", None)):
                stripped.append(message)
                continue
            clone = copy.copy(message)
            clone.content = self._strip_images_from_content(message.content)
            stripped.append(clone)
            changed = True
        return stripped if changed else messages

    @staticmethod
    def _summary_neighbor_role(message: Message | None) -> str:
        role = getattr(message, "role", "user")
        return "tool" if role == "toolResult" else role

    def _summary_message(self, role: str, content: str) -> Message:
        if role == "assistant":
            return AssistantMessage(
                content=[TextContent(text=content)],
                api="compaction",
                provider="hermes",
                model="summary",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
        return UserMessage(content=content, timestamp=now_ms())

    @staticmethod
    def _prepend_text_to_message(message: Message, text: str) -> Message:
        clone = copy.copy(message)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            clone.content = text + content
        elif isinstance(content, list):
            clone.content = [TextContent(text=text), *content]
        else:
            clone.content = text + str(content or "")
        return clone

    def _assemble_compressed_messages(
        self,
        messages: list[Message],
        head_end: int,
        tail_start: int,
        summary_text: str,
    ) -> list[Message]:
        summary = SUMMARY_PREFIX + summary_text
        last_head_role = self._summary_neighbor_role(messages[head_end - 1] if head_end > 0 else None)
        first_tail_role = self._summary_neighbor_role(messages[tail_start] if tail_start < len(messages) else None)

        merge_summary_into_tail = False
        if last_head_role in {"assistant", "tool"}:
            summary_role = "user"
        else:
            summary_role = "assistant"
        if summary_role == first_tail_role:
            flipped = "assistant" if summary_role == "user" else "user"
            if flipped != last_head_role:
                summary_role = flipped
            else:
                merge_summary_into_tail = True

        result = [*messages[:head_end]]
        if merge_summary_into_tail:
            prefix = summary + "\n\n" + SUMMARY_END_MARKER + "\n\n"
            for index in range(tail_start, len(messages)):
                tail_message = messages[index]
                result.append(self._prepend_text_to_message(tail_message, prefix) if index == tail_start else tail_message)
            return result

        result.append(self._summary_message(summary_role, summary + "\n\n" + SUMMARY_END_MARKER))
        result.extend(messages[tail_start:])
        return result

    @staticmethod
    def _strip_summary_prefix(summary: str) -> str:
        text = (summary or "").strip()
        for prefix in (SUMMARY_PREFIX, LEGACY_SUMMARY_PREFIX):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                break
        if text.endswith(SUMMARY_END_MARKER):
            text = text[: -len(SUMMARY_END_MARKER)].rstrip()
        return text

    @classmethod
    def _is_context_summary_message(cls, message: Message) -> bool:
        text = _message_text(message).lstrip()
        return text.startswith(SUMMARY_PREFIX) or text.startswith(LEGACY_SUMMARY_PREFIX)

    @classmethod
    def _find_latest_context_summary(cls, messages: list[Message], start: int, end: int) -> tuple[int | None, str]:
        for index in range(end - 1, start - 1, -1):
            if cls._is_context_summary_message(messages[index]):
                return index, cls._strip_summary_prefix(_message_text(messages[index]))
        return None, ""

    # --- Pass 2: LLM structured summary ---

    def _can_fallback_to_main_summarizer(self) -> bool:
        return bool(
            self.summary_model
            and self.summary_model != self.model
            and not self._summary_model_fallen_back
        )

    @staticmethod
    def _compact_error_text(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        if len(text) > _AUX_MODEL_ERROR_MAX_CHARS:
            return text[: _AUX_MODEL_ERROR_MAX_CHARS - 3].rstrip() + "..."
        return text

    def _fallback_to_main_for_compression(self, exc: Exception) -> None:
        self._summary_model_fallen_back = True
        self._last_aux_model_failure_error = self._compact_error_text(exc)
        self._last_aux_model_failure_model = self.summary_model
        self.summary_model = ""

    def _run_summary_summarizer(self, prompt: str, summarizer: Optional[Summarizer]) -> str | None:
        if self.summary_model and self._summary_summarizer is not None:
            try:
                summary = self._summary_summarizer(prompt)
            except Exception as exc:
                if self._can_fallback_to_main_summarizer():
                    self._fallback_to_main_for_compression(exc)
                    if summarizer is not None:
                        summary = summarizer(prompt)
                        self._summary_model_fallen_back = False
                        return summary
                raise
            self._summary_model_fallen_back = False
            return summary

        if summarizer is not None:
            summary = summarizer(prompt)
            self._summary_model_fallen_back = False
            return summary
        return None

    def generate_summary(
        self,
        middle: list[Message],
        summarizer: Optional[Summarizer],
        *,
        focus_topic: str | None = None,
    ) -> str | None:
        if self._summary_failure_in_cooldown():
            self._last_summary_error = "summary generation cooldown active"
            return None
        serialized = self._serialize_for_summary(middle)
        template = self._summary_template(self._summary_budget(middle))
        if self._previous_summary:
            prompt = f"""{_SUMMARIZER_PREAMBLE}

You are updating a context compaction summary. A previous compaction produced the summary below. New conversation turns have occurred since then and need to be incorporated.

PREVIOUS SUMMARY:
{_redact_sensitive_text(self._previous_summary)}

NEW TURNS TO INCORPORATE:
{serialized}

Update the summary using this exact structure. PRESERVE all existing information that is still relevant. ADD new completed actions to the numbered list (continue numbering). Move items from "In Progress" to "Completed Actions" when done. Move answered questions to "Resolved Questions". Update "Active State" to reflect current state. Remove information only if it is clearly obsolete. CRITICAL: Update "## Active Task" to reflect the user's most recent unfulfilled input — this includes any question, decision request, or discussion turn that the assistant has not yet answered. Only write "None" if the last exchange was fully resolved.

{template}"""
        else:
            prompt = f"""{_SUMMARIZER_PREAMBLE}

Create a structured checkpoint summary for the conversation after earlier turns are compacted. The summary should preserve enough detail for continuity without re-reading the original turns.

TURNS TO SUMMARIZE:
{serialized}

Use this exact structure:

{template}"""
        if focus_topic:
            safe_focus = _redact_sensitive_text(focus_topic)
            prompt += f"""

FOCUS TOPIC: "{safe_focus}"
This compaction should PRIORITISE preserving all information related to the focus topic above. For content related to "{safe_focus}", include full detail — exact values, file paths, command outputs, error messages, and decisions. For content NOT related to the focus topic, summarise more aggressively (brief one-liners or omit if truly irrelevant). The focus topic sections should receive roughly 60-70% of the summary token budget. Even for the focus topic, NEVER preserve API keys, tokens, passwords, or credentials — use [REDACTED]."""
        summary = self._run_summary_summarizer(prompt, summarizer)
        if summary is not None:
            self._summary_failure_cooldown_until = 0.0
            self._last_summary_error = None
            return _redact_sensitive_text(summary)
        return _redact_sensitive_text(self._static_fallback_summary(middle))

    def _summary_budget(self, middle: list[Message]) -> int:
        return max(2000, estimate_tokens(middle) // 5)

    def _current_date_string(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _temporal_anchoring_rule(self) -> str:
        try:
            today = self._current_date_string()
        except Exception:
            return ""
        if not today:
            return ""
        return (
            f"\nTEMPORAL ANCHORING: The current date is {today}. When an action has already been carried "
            "out, phrase it as a completed, dated, past-tense fact rather than an open instruction. For "
            f'example, rewrite "email John about the proposal" as "Sent the proposal email to John on {today}." '
            "Never leave a finished action worded as if it still needs doing, and never invent a date for "
            "work that has not happened yet.\n"
        )

    def _summary_template(self, summary_budget: int) -> str:
        return f"""{HISTORICAL_TASK_HEADING}
[THE SINGLE MOST IMPORTANT FIELD. Capture the user's most recent unfulfilled input verbatim. If no outstanding task exists, write "None."]

## Goal
[What the user is trying to accomplish overall]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions]

## Completed Actions
[Numbered list of concrete actions taken — include tool used, target, and outcome.]

## Active State
[Current working state, modified files, test status, running processes, and environment details that matter.]

{HISTORICAL_IN_PROGRESS_HEADING}
[Work currently underway — what was being done when compaction fired]

## Blocked
[Any blockers, errors, or issues not yet resolved. Include exact error messages.]

## Key Decisions
[Important technical decisions and why they were made]

## Resolved Questions
[Questions the user asked that were already answered — include the answer so it is not repeated]

{HISTORICAL_PENDING_ASKS_HEADING}
[Questions or requests from the user that have not yet been answered or fulfilled. These are stale reference only. If none, write "None."]

## Relevant Files
[Files read, modified, or created — with brief note on each]

{HISTORICAL_REMAINING_WORK_HEADING}
[What remains to be done — framed as stale context for reference only. The agent must not resume this work unless the latest user message explicitly asks for it.]

## Critical Context
[Any specific values, error messages, configuration details, or data that would be lost without explicit preservation. NEVER include API keys, tokens, passwords, or credentials — write [REDACTED] instead.]

Target ~{summary_budget} tokens. Be CONCRETE — include file paths, command outputs, error messages, line numbers, and specific values. Avoid vague descriptions like "made some changes" — say exactly what changed.
{self._temporal_anchoring_rule()}
Write only the summary body. Do not include any preamble or prefix."""

    def _serialize_for_summary(self, middle: list[Message]) -> str:
        lines = []
        for message in middle:
            role = getattr(message, "role", "?")
            text = _redact_sensitive_text(_message_text(message))[:6000]
            lines.append(f"[{role}] {text}")
        return "\n".join(lines)

    def _static_fallback_summary(self, middle: list[Message], *, reason: str | None = None) -> str:
        roles = [getattr(m, "role", "?") for m in middle]
        reason_text = f"\nReason: {reason}" if reason else ""
        return (
            f"{HISTORICAL_TASK_HEADING}\n"
            "Recovered from a deterministic fallback because summary generation was unavailable. "
            "Continue from the protected recent messages after this summary and use current file/system state "
            "for exact details.\n\n"
            "## Completed Actions\n"
            f"Compacted {len(middle)} earlier messages ({', '.join(roles)}).\n\n"
            "## Critical Context\n"
            f"Summary generation was unavailable, so this is a best-effort deterministic fallback.{reason_text}"
        )

    # --- Orchestrator ---

    def compress(
        self,
        messages: list[Message],
        summarizer: Optional[Summarizer] = None,
        *,
        focus_topic: str | None = None,
        force: bool = False,
    ) -> CompressionResult:
        summarizer = summarizer or self._summarizer
        before = estimate_tokens(messages)
        self._last_summary_error = None
        self._last_summary_dropped_count = 0
        self._last_summary_fallback_used = False
        self._last_compress_aborted = False
        self._last_aux_model_failure_error = None
        self._last_aux_model_failure_model = None
        if force and self._summary_failure_cooldown_until > 0.0:
            self._summary_failure_cooldown_until = 0.0

        pruned = self.prune_old_tool_results(messages)
        head_end = self._protect_head_size(pruned)
        head_end = self._align_boundary_forward(pruned, head_end)
        tail_start = self._find_tail_start(pruned, head_end)

        if tail_start <= head_end:
            after = estimate_tokens(pruned)
            return CompressionResult(messages=pruned, compressed=False, savings_pct=_savings(before, after))

        middle = pruned[head_end:tail_start]
        summary_index, summary_body = self._find_latest_context_summary(pruned, 0, tail_start)
        if summary_index is not None:
            if summary_body and not self._previous_summary:
                self._previous_summary = summary_body
            middle = pruned[max(head_end, summary_index + 1):tail_start]
        try:
            summary_text = self.generate_summary(middle, summarizer, focus_topic=focus_topic)
        except Exception as exc:  # noqa: BLE001 - fallback handoff mirrors Hermes default
            self._last_summary_error = str(exc)
            self._summary_failure_cooldown_until = self._clock() + _SUMMARY_FAILURE_COOLDOWN_SECONDS
            if self.abort_on_summary_failure:
                self._last_compress_aborted = True
                after = estimate_tokens(pruned)
                return CompressionResult(messages=pruned, compressed=False, savings_pct=_savings(before, after))
            summary_text = None
        if summary_text is None:
            if self.abort_on_summary_failure:
                self._last_compress_aborted = True
                after = estimate_tokens(pruned)
                return CompressionResult(messages=pruned, compressed=False, savings_pct=_savings(before, after))
            self._last_summary_dropped_count = len(middle)
            self._last_summary_fallback_used = True
            summary_text = _redact_sensitive_text(
                self._static_fallback_summary(middle, reason=self._last_summary_error)
            )
        result = self._assemble_compressed_messages(pruned, head_end, tail_start, summary_text)
        result = self._sanitize_tool_pairs(result)
        result = self._strip_historical_media(result)

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
        total = len(messages)
        available_tail = max(0, total - head_end - 1)
        min_tail_floor = max(3, min(self.protect_last_n, _MAX_TAIL_MESSAGE_FLOOR))
        compressible_tail_cap = max(3, available_tail - 2)
        min_tail = (
            min(min_tail_floor, compressible_tail_cap, available_tail)
            if available_tail > 1
            else 0
        )
        soft_ceiling = int(budget * 1.5)
        accumulated = 0
        index = total
        for candidate in range(total - 1, head_end - 1, -1):
            tokens = self._tail_message_tokens(messages[candidate])
            if accumulated + tokens > soft_ceiling and (total - candidate) >= min_tail:
                break
            accumulated += tokens
            index = candidate

        if index <= head_end and accumulated <= soft_ceiling and accumulated > 0:
            raw_accumulated = 0
            for candidate in range(total - 1, head_end - 1, -1):
                tokens = self._tail_message_tokens(messages[candidate])
                if raw_accumulated + tokens > budget and (total - candidate) >= min_tail:
                    index = candidate
                    break
                raw_accumulated += tokens
                index = candidate

        fallback_cut = total - min_tail
        index = min(index, fallback_cut)
        if index <= head_end:
            index = max(fallback_cut, head_end + 1)

        index = self._align_boundary_backward(messages, index)
        index = self._ensure_last_user_message_in_tail(messages, index, head_end)
        index = self._ensure_last_assistant_message_in_tail(messages, index, head_end)
        return max(index, head_end + 1)

    @staticmethod
    def _tail_message_tokens(message: Message) -> int:
        return len(_message_text(message)) // CHARS_PER_TOKEN + _MESSAGE_TOKEN_OVERHEAD


def _savings(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return (before - after) / before * 100.0
