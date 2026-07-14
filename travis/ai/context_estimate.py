"""Context usage estimation for the Travis provider runtime."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
)

CHARS_PER_TOKEN = 4
ESTIMATED_IMAGE_CHARS = 4_800
CONTEXT_SAFETY_TOKENS = 4_096


@dataclass(frozen=True)
class ContextUsageEstimate:
    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: int | None


def calculate_context_tokens(message: AssistantMessage) -> int:
    usage = message.usage
    return usage.total_tokens or usage.input + usage.output + usage.cache_read + usage.cache_write


def estimate_context_tokens(context: Context) -> ContextUsageEstimate:
    usage_info = _last_assistant_usage(context.messages)
    if usage_info is not None:
        usage_message, index = usage_info
        usage_tokens = calculate_context_tokens(usage_message)
        trailing = sum(estimate_message_tokens(message) for message in context.messages[index + 1 :])
        added_names = {
            name
            for message in context.messages[index + 1 :]
            if getattr(message, "role", None) == "toolResult"
            for name in (getattr(message, "added_tool_names", None) or [])
        }
        added_tools = [tool for tool in (context.tools or []) if tool.name in added_names]
        added_tool_tokens = estimate_text_tokens(_safe_json(added_tools)) if added_tools else 0
        trailing += added_tool_tokens
        return ContextUsageEstimate(usage_tokens + trailing, usage_tokens, trailing, index)

    message_tokens = sum(estimate_message_tokens(message) for message in context.messages)
    prefix_tokens = estimate_text_tokens(context.system_prompt or "")
    if context.tools:
        prefix_tokens += estimate_text_tokens(_safe_json(context.tools))
    total = message_tokens + prefix_tokens
    return ContextUsageEstimate(total, 0, total, None)


def clamp_max_tokens_to_context(model, context: Context, requested: int) -> int:
    if model.context_window <= 0:
        return max(1, requested)
    available = model.context_window - estimate_context_tokens(context).tokens - CONTEXT_SAFETY_TOKENS
    return min(requested, max(1, available))


def estimate_message_tokens(message: Message) -> int:
    content = message.content
    if getattr(message, "role", None) in {"user", "toolResult"}:
        if isinstance(content, str):
            return estimate_text_tokens(content)
        chars = sum(len(block.text) if isinstance(block, TextContent) else ESTIMATED_IMAGE_CHARS for block in content)
        return math.ceil(chars / CHARS_PER_TOKEN)
    chars = 0
    for block in content:
        if isinstance(block, TextContent):
            chars += len(block.text)
        elif isinstance(block, ThinkingContent):
            chars += len(block.thinking)
        elif isinstance(block, ToolCall):
            chars += len(block.name) + len(_safe_json(block.arguments))
        elif isinstance(block, ImageContent):
            chars += ESTIMATED_IMAGE_CHARS
    return math.ceil(chars / CHARS_PER_TOKEN)


def estimate_text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _last_assistant_usage(messages: list[Message]) -> tuple[AssistantMessage, int] | None:
    latest_prefix_timestamp = float("-inf")
    result: tuple[AssistantMessage, int] | None = None
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            if (
                message.timestamp >= latest_prefix_timestamp
                and message.stop_reason not in {"aborted", "error"}
                and calculate_context_tokens(message) > 0
            ):
                result = message, index
        latest_prefix_timestamp = max(latest_prefix_timestamp, message.timestamp)
    return result


def _safe_json(value: object) -> str:
    try:
        return json.dumps(value, default=lambda item: item.__dict__, separators=(",", ":"))
    except (TypeError, ValueError):
        return "[unserializable]"


__all__ = [
    "CONTEXT_SAFETY_TOKENS",
    "ContextUsageEstimate",
    "clamp_max_tokens_to_context",
    "estimate_context_tokens",
    "estimate_message_tokens",
]
