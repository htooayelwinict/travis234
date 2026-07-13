"""Branch summarization for session tree navigation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from travis.agent.types import AbortSignal, AgentMessage
from travis.ai.stream import complete_simple_sync
from travis.ai.types import (
    AssistantMessage,
    Context,
    Message,
    Model,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    now_ms,
)
from travis.compaction.compressor import estimate_tokens
from travis.coding_agent.session_store import BranchSummaryMessage, CustomMessage, deserialize_message

BRANCH_SUMMARY_PREAMBLE = """The user explored a different conversation branch before returning here.
Summary of that exploration:

"""

BRANCH_SUMMARY_PROMPT = """Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

TOOL_RESULT_MAX_CHARS = 2000


@dataclass
class BranchSummaryResult:
    summary: str | None = None
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    aborted: bool = False
    error: str | None = None

    @property
    def readFiles(self) -> list[str]:
        return self.read_files

    @property
    def modifiedFiles(self) -> list[str]:
        return self.modified_files


@dataclass
class FileOperations:
    read: set[str] = field(default_factory=set)
    written: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)


@dataclass
class BranchPreparation:
    messages: list[AgentMessage]
    file_ops: FileOperations
    total_tokens: int


def prepare_branch_entries(entries: list[dict[str, Any]], token_budget: int = 0) -> BranchPreparation:
    messages: list[AgentMessage] = []
    file_ops = FileOperations()
    total_tokens = 0

    for entry in entries:
        if entry.get("type") == "branch_summary" and not entry.get("fromHook") and isinstance(entry.get("details"), dict):
            details = entry["details"]
            for path in details.get("readFiles", []):
                if isinstance(path, str):
                    file_ops.read.add(path)
            for path in details.get("modifiedFiles", []):
                if isinstance(path, str):
                    file_ops.edited.add(path)

    for entry in reversed(entries):
        message = _get_message_from_entry(entry)
        if message is None:
            continue
        _extract_file_ops_from_message(message, file_ops)
        tokens = estimate_tokens(_convert_to_llm([message]))
        if token_budget > 0 and total_tokens + tokens > token_budget:
            if entry.get("type") in ("compaction", "branch_summary") and total_tokens < token_budget * 0.9:
                messages.insert(0, message)
                total_tokens += tokens
            break
        messages.insert(0, message)
        total_tokens += tokens

    return BranchPreparation(messages=messages, file_ops=file_ops, total_tokens=total_tokens)


def generate_branch_summary(
    entries: list[dict[str, Any]],
    *,
    model: Model,
    signal: AbortSignal | None = None,
    custom_instructions: str | None = None,
    replace_instructions: bool | None = None,
    reserve_tokens: int = 16384,
    stream_fn=None,
    api_key: str | None = None,
    headers: dict[str, str] | None = None,
) -> BranchSummaryResult:
    if signal and signal.aborted:
        return BranchSummaryResult(aborted=True)

    context_window = model.context_window or 128000
    token_budget = context_window - reserve_tokens
    preparation = prepare_branch_entries(entries, token_budget)
    if not preparation.messages:
        return BranchSummaryResult(summary="No content to summarize")

    conversation_text = serialize_conversation(_convert_to_llm(preparation.messages))
    if replace_instructions and custom_instructions:
        instructions = custom_instructions
    elif custom_instructions:
        instructions = f"{BRANCH_SUMMARY_PROMPT}\n\nAdditional focus: {custom_instructions}"
    else:
        instructions = BRANCH_SUMMARY_PROMPT
    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n{instructions}"

    context = Context(
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[UserMessage(content=[TextContent(text=prompt_text)], timestamp=now_ms())],
    )
    options = SimpleStreamOptions(api_key=api_key, headers=headers, signal=signal, max_tokens=2048)
    response = stream_fn(model, context, options).result_sync() if stream_fn else complete_simple_sync(model, context, options)
    if response.stop_reason == "aborted":
        return BranchSummaryResult(aborted=True)
    if response.stop_reason == "error":
        return BranchSummaryResult(error=response.error_message or "Summarization failed")

    summary_text = "\n".join(block.text for block in response.content if isinstance(block, TextContent))
    summary = f"{BRANCH_SUMMARY_PREAMBLE}{summary_text}"
    read_files, modified_files = _compute_file_lists(preparation.file_ops)
    summary += _format_file_operations(read_files, modified_files)
    return BranchSummaryResult(summary=summary or "No summary generated", read_files=read_files, modified_files=modified_files)


def serialize_conversation(messages: list[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        role = getattr(message, "role", None)
        if role == "user":
            content = _message_text_content(message.content)
            if content:
                parts.append(f"[User]: {content}")
        elif role == "assistant":
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[str] = []
            for block in message.content:
                if isinstance(block, TextContent):
                    text_parts.append(block.text)
                elif isinstance(block, ThinkingContent):
                    thinking_parts.append(block.thinking)
                elif isinstance(block, ToolCall):
                    args = ", ".join(f"{key}={json.dumps(value)}" for key, value in block.arguments.items())
                    tool_calls.append(f"{block.name}({args})")
            if thinking_parts:
                parts.append("[Assistant thinking]: " + "\n".join(thinking_parts))
            if text_parts:
                parts.append("[Assistant]: " + "\n".join(text_parts))
            if tool_calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")
        elif role == "toolResult":
            content = _message_text_content(message.content)
            if content:
                parts.append(f"[Tool result]: {_truncate_for_summary(content, TOOL_RESULT_MAX_CHARS)}")
    return "\n\n".join(parts)


def _get_message_from_entry(entry: dict[str, Any]) -> AgentMessage | None:
    entry_type = entry.get("type")
    if entry_type == "message":
        message = deserialize_message(entry["message"])
        if isinstance(message, ToolResultMessage):
            return None
        return message
    if entry_type == "custom_message":
        return CustomMessage(
            custom_type=entry.get("customType", ""),
            content=_deserialize_custom_content(entry.get("content")),
            display=bool(entry.get("display", True)),
            details=entry.get("details"),
            timestamp=_timestamp_to_ms(entry.get("timestamp")),
        )
    if entry_type == "branch_summary" and entry.get("summary"):
        return BranchSummaryMessage(
            summary=entry["summary"],
            from_id=entry.get("fromId", "root"),
            timestamp=_timestamp_to_ms(entry.get("timestamp")),
        )
    if entry_type == "compaction" and entry.get("summary"):
        return SimpleNamespace(
            role="compactionSummary",
            summary=entry["summary"],
            tokensBefore=entry.get("tokensBefore", 0),
            timestamp=_timestamp_to_ms(entry.get("timestamp")),
        )
    return None


def _convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    converted: list[Message] = []
    for message in messages:
        role = getattr(message, "role", None)
        if role == "bashExecution":
            if getattr(message, "excludeFromContext", False):
                continue
            converted.append(
                UserMessage(
                    content=[TextContent(text=_bash_execution_to_text(message))],
                    timestamp=getattr(message, "timestamp", now_ms()),
                )
            )
        elif role == "custom":
            content = getattr(message, "content", "")
            converted.append(
                UserMessage(
                    content=[TextContent(text=content)] if isinstance(content, str) else content,
                    timestamp=getattr(message, "timestamp", now_ms()),
                )
            )
        elif role == "branchSummary":
            converted.append(
                UserMessage(
                    content=[
                        TextContent(
                            text=(
                                "The following is a summary of a branch that this conversation came back from:\n\n"
                                f"<summary>\n{getattr(message, 'summary', '')}</summary>"
                            )
                        )
                    ],
                    timestamp=getattr(message, "timestamp", now_ms()),
                )
            )
        elif role == "compactionSummary":
            converted.append(
                UserMessage(
                    content=[
                        TextContent(
                            text=(
                                "The conversation history before this point was compacted into the following summary:\n\n"
                                f"<summary>\n{getattr(message, 'summary', '')}\n</summary>"
                            )
                        )
                    ],
                    timestamp=getattr(message, "timestamp", now_ms()),
                )
            )
        elif role in ("user", "assistant", "toolResult"):
            converted.append(message)
    return converted


def _bash_execution_to_text(message) -> str:
    text = f"Ran `{getattr(message, 'command', '')}`\n"
    output = getattr(message, "output", "")
    if output:
        text += f"```\n{output}\n```"
    else:
        text += "(no output)"
    if getattr(message, "cancelled", False):
        text += "\n\n(command cancelled)"
    else:
        exit_code = getattr(message, "exitCode", None)
        if exit_code not in (None, 0):
            text += f"\n\nCommand exited with code {exit_code}"
    if getattr(message, "truncated", False) and getattr(message, "fullOutputPath", None):
        text += f"\n\n[Output truncated. Full output: {message.fullOutputPath}]"
    return text


def _extract_file_ops_from_message(message: AgentMessage, file_ops: FileOperations) -> None:
    if not isinstance(message, AssistantMessage):
        return
    for block in message.content:
        if not isinstance(block, ToolCall):
            continue
        path = block.arguments.get("path")
        if not isinstance(path, str):
            continue
        if block.name == "read":
            file_ops.read.add(path)
        elif block.name == "write":
            file_ops.written.add(path)
        elif block.name == "edit":
            file_ops.edited.add(path)


def _compute_file_lists(file_ops: FileOperations) -> tuple[list[str], list[str]]:
    modified = file_ops.edited | file_ops.written
    read_only = sorted(path for path in file_ops.read if path not in modified)
    return read_only, sorted(modified)


def _format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    sections: list[str] = []
    if read_files:
        sections.append("<read-files>\n" + "\n".join(read_files) + "\n</read-files>")
    if modified_files:
        sections.append("<modified-files>\n" + "\n".join(modified_files) + "\n</modified-files>")
    return "" if not sections else "\n\n" + "\n\n".join(sections)


def _message_text_content(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))


def _deserialize_custom_content(content) -> str | list[TextContent]:
    if isinstance(content, str):
        return content
    return [TextContent(text=block.get("text", "")) for block in content or [] if block.get("type") == "text"]


def _truncate_for_summary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated_chars = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated_chars} more characters truncated]"


def _timestamp_to_ms(value: str | None) -> int:
    if not value:
        return now_ms()
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return now_ms()
