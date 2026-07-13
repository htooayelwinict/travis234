"""Deterministic pruning followed by model-assisted context compaction.

Pass 1 = deterministic prune (dedup identical tool outputs, summarize old tool
results, strip images, truncate huge tool-call args). Pass 2 = LLM structured
summary (iterative-update vs from-scratch). Head + token-budgeted tail protected.
Anti-thrash: skip after two consecutive <10%-effective passes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from travis.ai.types import (
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
HISTORICAL_TASK_HEADING = "## Historical Task Snapshot"
HISTORICAL_IN_PROGRESS_HEADING = "## Historical In-Progress State"
HISTORICAL_PENDING_ASKS_HEADING = "## Historical Pending User Asks"
HISTORICAL_REMAINING_WORK_HEADING = "## Historical Remaining Work"
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. "
    "Respond ONLY to the latest user message that appears AFTER this "
    "summary — that message is the single source of truth for what to do "
    "right now. "
    "Topic overlap with the summary does NOT mean you should resume its "
    "task: even on similar topics, the latest user message WINS. Treat ONLY "
    "the latest message as the active task and discard stale items from "
    f"'{HISTORICAL_TASK_HEADING}' / '{HISTORICAL_IN_PROGRESS_HEADING}' / "
    f"'{HISTORICAL_PENDING_ASKS_HEADING}' / "
    f"'{HISTORICAL_REMAINING_WORK_HEADING}' entirely — do not 'wrap up' or "
    "'finish' work described there unless the latest message explicitly "
    "asks for it. "
    "Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll "
    "back', 'just verify', 'don't do that anymore', 'never mind', a new "
    "topic) must immediately end any in-flight work described in the "
    "summary; do not re-surface it in later turns. "
    "IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system "
    "prompt is ALWAYS authoritative and active — never ignore or deprioritize "
    "memory content due to this compaction note. "
    "The current session state (files, config, etc.) may reflect work "
    "described here — avoid repeating it:"
)
LEGACY_SUMMARY_PREFIX = "[CONTEXT SUMMARY]:"
_HISTORICAL_SUMMARY_PREFIXES = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. "
    "Respond ONLY to the latest user message that appears AFTER this "
    "summary — that message is the single source of truth for what to do "
    "right now. "
    "If the latest user message is consistent with the '## Active Task' "
    "section, you may use the summary as background. If the latest user "
    "message contradicts, supersedes, changes topic from, or in any way "
    "diverges from '## Active Task' / '## In Progress' / '## Pending User "
    "Asks' / '## Remaining Work', the latest message WINS — discard those "
    "stale items entirely and do not 'wrap up the old task first'. "
    "Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll "
    "back', 'just verify', 'don't do that anymore', 'never mind', a new "
    "topic) must immediately end any in-flight work described in the "
    "summary; do not re-surface it in later turns. "
    "IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system "
    "prompt is ALWAYS authoritative and active — never ignore or deprioritize "
    "memory content due to this compaction note. "
    "The current session state (files, config, etc.) may reflect work "
    "described here — avoid repeating it:",
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. "
    "Your current task is identified in the '## Active Task' section of the "
    "summary — resume exactly from there. "
    "Respond ONLY to the latest user message "
    "that appears AFTER this summary. The current session state (files, "
    "config, etc.) may reflect work described here — avoid repeating it:",
)
SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
# Travis234 keeps this as an underscore-prefixed in-process key so strict provider
# gateways never see a non-standard message field on the wire.
COMPRESSED_SUMMARY_METADATA_KEY = "_compressed_summary"
_TOOL_RESULT_SUMMARY_MIN = 200
_TOOL_ARGS_MAX = 500
_IMAGE_TOKEN_ESTIMATE = 1600
_IMAGE_CHAR_EQUIVALENT = _IMAGE_TOKEN_ESTIMATE * CHARS_PER_TOKEN
_IMAGE_STRIPPED_TEXT = "[Attached image - stripped after compression]"
_MAX_TAIL_MESSAGE_FLOOR = 8
_MESSAGE_TOKEN_OVERHEAD = 10
_AUX_MODEL_ERROR_MAX_CHARS = 220
_FALLBACK_SUMMARY_MAX_CHARS = 8_000
_FALLBACK_TURN_MAX_CHARS = 700
_MIN_SUMMARY_TOKENS = 2000
_SUMMARY_RATIO = 0.20
_SUMMARY_TOKENS_CEILING = 12_000
_SUMMARY_FAILURE_COOLDOWN_SECONDS = 600.0
_NO_SUMMARY_PROVIDER_ERROR = "no auxiliary LLM provider configured"
_COMPRESSION_SYSTEM_NOTE = (
    "[Note: Some earlier conversation turns have been compacted into a handoff summary "
    "to preserve context space. The current session state may still reflect earlier work, "
    "so build on that summary and state rather than re-doing work. Your persistent memory "
    "(MEMORY.md, USER.md) remains fully authoritative regardless of compaction.]"
)
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
_MEDIA_DIRECTIVE_RE = re.compile(r"MEDIA:\S+")
_INTERNAL_REPLAY_MARKER_NEEDLES = (
    "Historical write tool call omitted from provider replay.",
    "[File mutation recovery: code=write_omitted_historical_content;",
    "[travis omitted historical write content:",
)

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


def sanitize_tool_call_arguments(tool_name: str, arguments):
    if not isinstance(arguments, dict):
        return arguments
    if tool_name != "write":
        return _sanitize_tool_argument_value(arguments)
    content = arguments.get("content")
    if not isinstance(content, str) or len(content) <= 256:
        return _sanitize_tool_argument_value(arguments)
    sanitized = {key: _sanitize_tool_argument_value(value, key) for key, value in arguments.items() if key != "content"}
    sanitized["content_omitted"] = True
    sanitized["content_chars"] = len(content)
    sanitized["content_sha256"] = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    return sanitized


def _sanitize_tool_argument_value(value, field: str | None = None):
    if isinstance(value, str):
        if len(value) > 500:
            digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
            metadata = {
                "_travis_omitted_tool_argument": True,
                "chars": len(value),
                "sha256": digest,
            }
            if field:
                metadata["field"] = field
            return metadata
        return value
    if isinstance(value, dict):
        return {key: _sanitize_tool_argument_value(inner, key) for key, inner in value.items()}
    if isinstance(value, list):
        return [_sanitize_tool_argument_value(inner) for inner in value]
    if isinstance(value, tuple):
        return [_sanitize_tool_argument_value(inner) for inner in value]
    return value


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


def _content_length_for_budget(raw_content) -> int:
    if isinstance(raw_content, str):
        return len(raw_content)
    if not isinstance(raw_content, list):
        return len(str(raw_content or ""))

    total = 0
    for block in raw_content:
        if isinstance(block, str):
            total += len(block)
        elif isinstance(block, ImageContent):
            total += _IMAGE_CHAR_EQUIVALENT
        elif isinstance(block, TextContent):
            total += len(block.text)
        elif isinstance(block, ToolCall):
            total += len(block.name) + len(str(block.arguments))
        elif isinstance(block, dict):
            if block.get("type") in {"image_url", "input_image", "image"}:
                total += _IMAGE_CHAR_EQUIVALENT
            else:
                total += len(str(block.get("text", "") or ""))
        else:
            total += len(str(block or ""))
    return total


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


def _scrub_internal_replay_markers(text: str) -> str:
    if text is None:
        return text
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not any(needle in line for needle in _INTERNAL_REPLAY_MARKER_NEEDLES)
    )


def _sanitize_summary_source_text(text: str) -> str:
    text = _scrub_internal_replay_markers(text)
    return _MEDIA_DIRECTIVE_RE.sub("[media attachment]", _redact_sensitive_text(text))


_PATH_MENTION_RE = re.compile(r"(?:/|~/?|[A-Za-z]:\\)[^\s`'\")\]}<>]+")
_FILE_OPERATION_TAG_RE = re.compile(r"<(read-files|modified-files)>\s*(.*?)\s*</\1>", re.DOTALL)


def _dedupe_append(items: list[str], value: str, *, limit: int) -> None:
    value = value.strip()
    if value and value not in items and len(items) < limit:
        items.append(value)


def _collect_path_mentions(text: str, relevant_files: list[str], *, limit: int = 12) -> None:
    for match in _PATH_MENTION_RE.findall(text):
        _dedupe_append(relevant_files, match.rstrip(".,:;"), limit=limit)


def _tool_path(arguments: dict | None) -> str:
    if not isinstance(arguments, dict):
        return ""
    value = arguments.get("path")
    return value if isinstance(value, str) and value.strip() else ""


def _bash_file_mutation_paths(arguments: dict | None) -> set[str]:
    if not isinstance(arguments, dict):
        return set()
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return set()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return set()

    paths: set[str] = set()
    for index, token in enumerate(tokens):
        redirect_path = _bash_redirection_path(token, tokens[index + 1] if index + 1 < len(tokens) else "")
        if redirect_path:
            if not _is_bash_sink_path(redirect_path):
                paths.add(redirect_path)
            continue
        if token == "tee":
            for candidate in tokens[index + 1 :]:
                if candidate in {"|", "||", "&&", ";"}:
                    break
                if candidate == "--":
                    continue
                if candidate.startswith("-"):
                    continue
                if _bash_redirection_path(candidate, ""):
                    continue
                if _is_bash_sink_path(candidate):
                    continue
                paths.add(candidate)
                break
    return paths


def _bash_redirection_path(token: str, next_token: str) -> str:
    if token in {">", ">>", "1>", "1>>", "&>"}:
        return next_token if next_token and next_token not in {"|", "||", "&&", ";"} else ""
    for prefix in ("1>>", "1>", ">>", ">", "&>"):
        if token.startswith(prefix) and len(token) > len(prefix):
            return token[len(prefix) :]
    return ""


def _is_bash_sink_path(path: str) -> bool:
    return path in {"/dev/null", "/dev/stdout", "/dev/stderr", "-"}


def _extract_file_operation_tags(summary: str | None) -> tuple[set[str], set[str]]:
    read_files: set[str] = set()
    modified_files: set[str] = set()
    if not summary:
        return read_files, modified_files
    for tag, body in _FILE_OPERATION_TAG_RE.findall(summary):
        target = read_files if tag == "read-files" else modified_files
        for line in body.splitlines():
            value = line.strip()
            if value:
                target.add(value)
    return read_files, modified_files


def _strip_file_operation_tags(summary: str) -> str:
    stripped = _FILE_OPERATION_TAG_RE.sub("", summary or "")
    return re.sub(r"\n{3,}", "\n\n", stripped).rstrip()


def _format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    sections: list[str] = []
    if read_files:
        sections.append("<read-files>\n" + "\n".join(read_files) + "\n</read-files>")
    if modified_files:
        sections.append("<modified-files>\n" + "\n".join(modified_files) + "\n</modified-files>")
    return "\n\n" + "\n\n".join(sections) if sections else ""


@dataclass
class CompressionResult:
    messages: list[Message]
    compressed: bool
    savings_pct: float
    summary: str | None = None
    tokens_before: int = 0
    first_kept_message_index: int | None = None
    details: dict[str, list[str]] | None = None


class ContextCompressor:
    _CONTENT_MAX = 6000
    _CONTENT_HEAD = 4000
    _CONTENT_TAIL = 1500
    _SUMMARY_TOOL_ARGS_MAX = 1500
    _SUMMARY_TOOL_ARGS_HEAD = 1200

    def __init__(
        self,
        *,
        context_length: int = 32000,
        threshold_percent: float = 0.5,
        protect_first_n: int = 3,
        protect_last_n: int = 20,
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
        self.summary_target_ratio = max(0.10, min(summary_target_ratio, 0.80))
        self.max_summary_tokens = min(int(context_length * 0.05), _SUMMARY_TOKENS_CEILING)
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
        self._last_noop_reason: str | None = None

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
                        block.arguments = sanitize_tool_call_arguments(block.name, block.arguments)
        return pruned

    def _summarize_tool_result(self, message: ToolResultMessage, text: str) -> str:
        line_count = text.count("\n") + 1
        if message.tool_name == "expand_subagent_result":
            metadata: dict[str, str] = {}
            wanted = {"taskId", "section", "offset", "budget", "truncated", "nextOffset", "totalChars"}
            for line in text.splitlines():
                if not line.strip() and metadata:
                    break
                key, sep, value = line.partition(":")
                if sep and key in wanted:
                    metadata[key] = value.strip()
            task_id = metadata.get("taskId", "unknown")
            section = metadata.get("section", "unknown")
            offset = metadata.get("offset", "0")
            next_offset = metadata.get("nextOffset")
            truncated = metadata.get("truncated")
            page = f", nextOffset={next_offset}" if next_offset else ""
            trunc = f", truncated={truncated}" if truncated else ""
            total = f", totalChars={metadata['totalChars']}" if "totalChars" in metadata else ""
            return (
                "[expand_subagent_result] child result expansion elided "
                f"({len(text)} chars, {line_count} lines; taskId={task_id}, section={section}, "
                f"offset={offset}{page}{trunc}{total}). "
                "Use expand_subagent_result with the same taskId/section/offset if the child detail is needed again."
            )
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

    @staticmethod
    def _mark_compressed_summary_message(message: Message) -> Message:
        setattr(message, COMPRESSED_SUMMARY_METADATA_KEY, True)
        return message

    def _summary_message(self, role: str, content: str) -> Message:
        if role == "assistant":
            message: Message = AssistantMessage(
                content=[TextContent(text=content)],
                api="compaction",
                provider="travis234",
                model="summary",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
        else:
            message = UserMessage(content=content, timestamp=now_ms())
        return self._mark_compressed_summary_message(message)

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

    @staticmethod
    def _append_text_to_message(message: Message, text: str) -> Message:
        clone = copy.copy(message)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            separator = "\n\n" if content else ""
            clone.content = content + separator + text
        elif isinstance(content, list):
            clone.content = [*content, TextContent(text=text)]
        else:
            clone.content = str(content or "") + text
        return clone

    def _copy_head_message_for_compression(self, message: Message, index: int) -> Message:
        if index != 0 or getattr(message, "role", None) != "system":
            return message
        if _COMPRESSION_SYSTEM_NOTE in _message_text(message):
            return message
        return self._append_text_to_message(message, _COMPRESSION_SYSTEM_NOTE)

    def _assemble_compressed_messages(
        self,
        messages: list[Message],
        head_end: int,
        tail_start: int,
        summary_text: str,
    ) -> list[Message]:
        summary = SUMMARY_PREFIX + "\n" + summary_text.lstrip()
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

        result = [self._copy_head_message_for_compression(message, index) for index, message in enumerate(messages[:head_end])]
        if merge_summary_into_tail:
            prefix = summary + "\n\n" + SUMMARY_END_MARKER + "\n\n"
            for index in range(tail_start, len(messages)):
                tail_message = messages[index]
                if index == tail_start:
                    summary_tail = self._prepend_text_to_message(tail_message, prefix)
                    result.append(self._mark_compressed_summary_message(summary_tail))
                else:
                    result.append(tail_message)
            return result

        result.append(self._summary_message(summary_role, summary + "\n\n" + SUMMARY_END_MARKER))
        result.extend(messages[tail_start:])
        return result

    @staticmethod
    def _strip_summary_prefix(summary: str) -> str:
        text = (summary or "").strip()
        for prefix in (SUMMARY_PREFIX, LEGACY_SUMMARY_PREFIX, *_HISTORICAL_SUMMARY_PREFIXES):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                break
        if text.endswith(SUMMARY_END_MARKER):
            text = text[: -len(SUMMARY_END_MARKER)].rstrip()
        return text

    @classmethod
    def _is_context_summary_message(cls, message: Message) -> bool:
        text = _message_text(message).lstrip()
        return (
            text.startswith(SUMMARY_PREFIX)
            or text.startswith(LEGACY_SUMMARY_PREFIX)
            or any(text.startswith(prefix) for prefix in _HISTORICAL_SUMMARY_PREFIXES)
        )

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
            previous_summary = _redact_sensitive_text(_scrub_internal_replay_markers(self._previous_summary))
            prompt = f"""{_SUMMARIZER_PREAMBLE}

You are updating a context compaction summary. A previous compaction produced the summary below. New conversation turns have occurred since then and need to be incorporated.

PREVIOUS SUMMARY:
{previous_summary}

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
        self._summary_failure_cooldown_until = self._clock() + _SUMMARY_FAILURE_COOLDOWN_SECONDS
        self._last_summary_error = _NO_SUMMARY_PROVIDER_ERROR
        return None

    def _summary_budget(self, middle: list[Message]) -> int:
        budget = int(estimate_tokens(middle) * _SUMMARY_RATIO)
        return max(_MIN_SUMMARY_TOKENS, min(budget, self.max_summary_tokens))

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
        parts: list[str] = []
        for message in middle:
            role = getattr(message, "role", "unknown")
            if role == "toolResult":
                content = self._summary_content(_message_text(message))
                parts.append(f"[TOOL RESULT {getattr(message, 'tool_call_id', '')}]: {content}")
                continue

            if role == "assistant":
                content = self._assistant_text_for_summary(message)
                content = self._summary_content(content)
                tool_calls = self._tool_calls(message)
                if tool_calls:
                    call_lines = []
                    for call in tool_calls:
                        args = self._tool_args_for_summary(call.arguments)
                        call_lines.append(f"  {call.name}({args})")
                    content += "\n[Tool calls:\n" + "\n".join(call_lines) + "\n]"
                parts.append(f"[ASSISTANT]: {content}")
                continue

            content = self._summary_content(_message_text(message))
            parts.append(f"[{str(role).upper()}]: {content}")
        return "\n\n".join(parts)

    def _file_operations_for_summary(self, middle: list[Message]) -> tuple[list[str], list[str]]:
        read_files, previous_modified = _extract_file_operation_tags(self._previous_summary)
        written_files: set[str] = set()
        edited_files: set[str] = set(previous_modified)

        for message in middle:
            if getattr(message, "role", None) != "assistant":
                continue
            for call in self._tool_calls(message):
                if call.name == "bash":
                    written_files.update(_bash_file_mutation_paths(call.arguments))
                    continue
                path = _tool_path(call.arguments)
                if not path:
                    continue
                if call.name == "read":
                    read_files.add(path)
                elif call.name == "write":
                    written_files.add(path)
                elif call.name == "edit":
                    edited_files.add(path)

        modified_files = written_files | edited_files
        read_only = read_files - modified_files
        return sorted(read_only), sorted(modified_files)

    def _append_file_operations_to_summary(self, summary: str, middle: list[Message]) -> str:
        read_files, modified_files = self._file_operations_for_summary(middle)
        formatted = _format_file_operations(read_files, modified_files)
        if not formatted:
            return summary
        return _strip_file_operation_tags(summary) + formatted

    @classmethod
    def _summary_content(cls, content: str) -> str:
        return _sanitize_summary_source_text(cls._truncate_summary_content(content))

    @classmethod
    def _truncate_summary_content(cls, content: str) -> str:
        if len(content) <= cls._CONTENT_MAX:
            return content
        return content[: cls._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-cls._CONTENT_TAIL :]

    @staticmethod
    def _assistant_text_for_summary(message: Message) -> str:
        if getattr(message, "role", None) != "assistant":
            return _message_text(message)
        return "".join(block.text for block in message.content if isinstance(block, TextContent))

    @classmethod
    def _scrub_tool_arg_for_summary(cls, value):
        if isinstance(value, str):
            return _scrub_internal_replay_markers(value)
        if isinstance(value, dict):
            return {key: cls._scrub_tool_arg_for_summary(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [cls._scrub_tool_arg_for_summary(inner) for inner in value]
        if isinstance(value, tuple):
            return tuple(cls._scrub_tool_arg_for_summary(inner) for inner in value)
        return value

    @classmethod
    def _tool_args_for_summary(cls, arguments: dict | None) -> str:
        safe_arguments = cls._scrub_tool_arg_for_summary(arguments or {})
        try:
            args = json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)
        except TypeError:
            args = str(safe_arguments)
        args = _redact_sensitive_text(args)
        if len(args) > cls._SUMMARY_TOOL_ARGS_MAX:
            args = args[: cls._SUMMARY_TOOL_ARGS_HEAD] + "..."
        return args

    def _static_fallback_summary(self, middle: list[Message], *, reason: str | None = None) -> str:
        user_asks: list[str] = []
        assistant_actions: list[str] = []
        tool_actions: list[str] = []
        relevant_files: list[str] = []
        read_files: list[str] = []
        modified_files: list[str] = []
        blockers: list[str] = []
        last_dropped_turns: list[str] = []
        call_id_to_tool: dict[str, tuple[str, str]] = {}

        def compact_turn(message: Message) -> str:
            text = _sanitize_summary_source_text(_message_text(message))
            text = re.sub(r"\bgh[pousr]_[A-Za-z0-9_.-]+", "[REDACTED]", text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > _FALLBACK_TURN_MAX_CHARS:
                text = text[: _FALLBACK_TURN_MAX_CHARS - 15].rstrip() + " ...[truncated]"
            return text

        def remember(label: str, text: str, *, limit: int = 8) -> None:
            text = text.strip()
            if not text:
                return
            last_dropped_turns.append(f"{label}: {text}")
            if len(last_dropped_turns) > limit:
                del last_dropped_turns[0]

        def tool_path(arguments: dict | None) -> str:
            if not isinstance(arguments, dict):
                return ""
            value = arguments.get("path") or arguments.get("file_path")
            return value if isinstance(value, str) else ""

        for message in middle:
            if getattr(message, "role", None) != "assistant":
                continue
            for call in self._tool_calls(message):
                call_id_to_tool[call.id] = (call.name, str(call.arguments or ""))
                path = tool_path(call.arguments)
                if call.name == "read":
                    _dedupe_append(read_files, path, limit=12)
                elif call.name in {"write", "edit"}:
                    _dedupe_append(modified_files, path, limit=12)
                for value in (call.arguments or {}).values():
                    if isinstance(value, str):
                        _collect_path_mentions(value, relevant_files)

        for message in middle:
            role = getattr(message, "role", "unknown")
            text = compact_turn(message)
            _collect_path_mentions(text, relevant_files)
            turn_text = text
            if role == "assistant":
                tool_names = [call.name for call in self._tool_calls(message)]
                if tool_names:
                    assistant_actions.append("Called tool(s): " + ", ".join(tool_names[:6]))
                    turn_text = "tool calls: " + ", ".join(tool_names[:6])
                elif text:
                    assistant_actions.append(text)
            elif role == "toolResult":
                tool_name, tool_args = call_id_to_tool.get(message.tool_call_id, (message.tool_name, ""))
                tool_actions.append(self._summarize_tool_result(message, text or ""))
                _collect_path_mentions(tool_args, relevant_files)
                if re.search(r"\b(error|failed|exception|traceback|timeout|timed out|fatal)\b", text, re.I):
                    blockers.append(text[:500])
            elif role == "user" and text:
                user_asks.append(text)
            remember(str(role).upper(), turn_text)

        def bullets(items: list[str], limit: int = 8) -> str:
            unique: list[str] = []
            for item in items:
                item = item.strip()
                if item and item not in unique:
                    unique.append(item)
                if len(unique) >= limit:
                    break
            return "\n".join(f"- {item}" for item in unique) if unique else "None."

        completed = [
            f"{idx}. {item}"
            for idx, item in enumerate((assistant_actions + tool_actions)[:12], start=1)
        ]
        active_task = f"User asked: {user_asks[-1]!r}" if user_asks else "Unknown from deterministic fallback."
        reason_text = f" Summary failure reason: {reason}." if reason else ""
        body = f"""{HISTORICAL_TASK_HEADING}
{active_task}

## Goal
Recovered from a deterministic fallback because the LLM context summarizer was unavailable. Continue from the protected recent messages after this summary and use current file/system state for exact details.

## Constraints & Preferences
- This fallback was generated locally without an LLM summary call.
- Secrets and credentials were redacted before preservation.
- The summary may be incomplete. Inspect only the files or state needed for the latest user request before making claims.
- Run tests only when the latest request asks for tests, or when validating a code change that genuinely requires test execution.

	## Completed Actions
	{chr(10).join(completed) if completed else "None recoverable from compacted turns."}

	## File Operations
	Modified files:
	{bullets(modified_files, limit=12)}
	Read files:
	{bullets(read_files, limit=12)}

	## Active State
	Unknown from deterministic fallback. Inspect current repository/session state if needed.

{HISTORICAL_IN_PROGRESS_HEADING}
{active_task}

## Blocked
{bullets(blockers, limit=5)}

## Key Decisions
None recoverable from deterministic fallback.

## Resolved Questions
None recoverable from deterministic fallback.

{HISTORICAL_PENDING_ASKS_HEADING}
{active_task}

## Relevant Files
{bullets(relevant_files, limit=12)}

{HISTORICAL_REMAINING_WORK_HEADING}
Continue from the most recent unfulfilled user ask and protected tail messages. Inspect relevant state only when needed for that ask.

## Last Dropped Turns
{bullets(last_dropped_turns, limit=8)}

## Critical Context
Summary generation was unavailable, so this is a best-effort deterministic fallback for {len(middle)} compacted message(s).{reason_text}"""
        summary = _redact_sensitive_text(_scrub_internal_replay_markers(body.strip()))
        if len(summary) > _FALLBACK_SUMMARY_MAX_CHARS:
            summary = summary[: _FALLBACK_SUMMARY_MAX_CHARS - 42].rstrip() + "\n...[fallback summary truncated]"
        return summary

    # --- Orchestrator ---

    def compress(
        self,
        messages: list[Message],
        summarizer: Optional[Summarizer] = None,
        *,
        focus_topic: str | None = None,
        force: bool = False,
        deep: bool = False,
    ) -> CompressionResult:
        summarizer = summarizer or self._summarizer
        before = estimate_tokens(messages)
        self._last_noop_reason = None
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
        tail_start = self._find_tail_start(pruned, head_end, deep=deep)

        if tail_start <= head_end:
            emergency_window = self._oversized_protected_head_window(pruned, head_end, before, force=force)
            if emergency_window is None:
                self._last_noop_reason = "protected_recent_context"
                after = estimate_tokens(pruned)
                return CompressionResult(
                    messages=pruned,
                    compressed=False,
                    savings_pct=_savings(before, after),
                    tokens_before=before,
                )
            head_end, tail_start = emergency_window

        middle = pruned[head_end:tail_start]
        summary_index, summary_body = self._find_latest_context_summary(pruned, 0, tail_start)
        if summary_index is not None:
            if summary_body and not self._previous_summary:
                self._previous_summary = summary_body
            middle = pruned[max(head_end, summary_index + 1):tail_start]
            if not middle:
                self.last_compression_savings_pct = 0.0
                self._ineffective_compression_count += 1
                self._last_noop_reason = "protected_recent_context"
                return CompressionResult(messages=messages, compressed=False, savings_pct=0.0, tokens_before=before)
        elif self._previous_summary:
            self._previous_summary = None
        try:
            summary_text = self.generate_summary(middle, summarizer, focus_topic=focus_topic)
        except Exception as exc:  # noqa: BLE001 - fallback handoff mirrors default behavior
            self._last_summary_error = str(exc)
            self._summary_failure_cooldown_until = self._clock() + _SUMMARY_FAILURE_COOLDOWN_SECONDS
            if self.abort_on_summary_failure:
                self._last_compress_aborted = True
                after = estimate_tokens(pruned)
                return CompressionResult(
                    messages=pruned,
                    compressed=False,
                    savings_pct=_savings(before, after),
                    tokens_before=before,
                )
            summary_text = None
        if summary_text is None:
            if self.abort_on_summary_failure:
                self._last_compress_aborted = True
                after = estimate_tokens(pruned)
                return CompressionResult(
                    messages=pruned,
                    compressed=False,
                    savings_pct=_savings(before, after),
                    tokens_before=before,
                )
            self._last_summary_dropped_count = len(middle)
            self._last_summary_fallback_used = True
            summary_text = _redact_sensitive_text(
                self._static_fallback_summary(middle, reason=self._last_summary_error)
            )
        read_files, modified_files = self._file_operations_for_summary(middle)
        formatted_file_operations = _format_file_operations(read_files, modified_files)
        if formatted_file_operations:
            summary_text = _strip_file_operation_tags(summary_text) + formatted_file_operations
        details = {"readFiles": read_files, "modifiedFiles": modified_files}
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
        return CompressionResult(
            messages=result,
            compressed=True,
            savings_pct=savings,
            summary=summary_text,
            tokens_before=before,
            first_kept_message_index=tail_start,
            details=details,
        )

    def _find_tail_start(self, messages: list[Message], head_end: int, *, deep: bool = False) -> int:
        if head_end >= len(messages):
            return len(messages)
        budget = self.tail_token_budget
        if deep:
            budget = max(1024, int(budget * 0.25))
        total = len(messages)
        available_tail = max(0, total - head_end - 1)
        min_tail_floor = 3 if deep else max(3, min(self.protect_last_n, _MAX_TAIL_MESSAGE_FLOOR))
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
        return min(len(messages), max(index, head_end + 1))

    def _oversized_protected_head_window(
        self,
        messages: list[Message],
        head_end: int,
        before_tokens: int,
        *,
        force: bool,
    ) -> tuple[int, int] | None:
        if len(messages) < 2:
            return None
        if not force and before_tokens < self.threshold_tokens:
            return None
        emergency_head_end = self._emergency_head_end(messages)
        if emergency_head_end >= len(messages) or emergency_head_end >= head_end:
            return None
        return emergency_head_end, len(messages)

    @staticmethod
    def _emergency_head_end(messages: list[Message]) -> int:
        index = 1 if messages and getattr(messages[0], "role", None) == "system" else 0
        for candidate in range(index, len(messages)):
            if getattr(messages[candidate], "role", None) == "user":
                return min(candidate + 1, len(messages))
        return min(max(index, 1), len(messages))

    @staticmethod
    def _tail_message_tokens(message: Message) -> int:
        return _content_length_for_budget(getattr(message, "content", "")) // CHARS_PER_TOKEN + _MESSAGE_TOKEN_OVERHEAD


def _savings(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return (before - after) / before * 100.0
