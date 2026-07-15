"""Context usage estimation for the Travis provider runtime."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from collections.abc import Sequence

from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
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
    system_tokens: int = 0
    tool_tokens: int = 0
    message_tokens: int = 0
    confidence: str = "estimated_full_request"


def calculate_prompt_tokens(usage: Usage) -> int:
    """Return provider input pressure without generated output tokens."""

    return (
        max(0, int(usage.input or 0))
        + max(0, int(usage.cache_read or 0))
        + max(0, int(usage.cache_write or 0))
    )


def calculate_total_tokens(usage: Usage) -> int:
    """Return billing/aggregate usage, falling back to explicit components."""

    reported = max(0, int(usage.total_tokens or 0))
    if reported:
        return reported
    return calculate_prompt_tokens(usage) + max(0, int(usage.output or 0))


def calculate_context_tokens(message: AssistantMessage) -> int:
    """Compatibility alias for prompt-pressure accounting."""

    return calculate_prompt_tokens(message.usage)


def estimate_context_tokens(context: Context) -> ContextUsageEstimate:
    usage_info = _last_assistant_usage(context.messages)
    if usage_info is not None:
        usage_message, index = usage_info
        usage_tokens = calculate_context_tokens(usage_message)
        trailing_message_tokens = sum(
            estimate_message_tokens(message) for message in context.messages[index + 1 :]
        )
        added_names = {
            name
            for message in context.messages[index + 1 :]
            if getattr(message, "role", None) == "toolResult"
            for name in (getattr(message, "added_tool_names", None) or [])
        }
        added_tools = [tool for tool in (context.tools or []) if tool.name in added_names]
        added_tool_tokens = estimate_text_tokens(_safe_json(added_tools)) if added_tools else 0
        trailing = trailing_message_tokens + added_tool_tokens
        return ContextUsageEstimate(
            usage_tokens + trailing,
            usage_tokens,
            trailing,
            index,
            tool_tokens=added_tool_tokens,
            # Provider usage is authoritative for the already-sent envelope but
            # cannot be split back into system/tool/message pieces. Keep that
            # indivisible observed prefix in the message bucket so components
            # remain additive, then account for trailing replay separately.
            message_tokens=usage_tokens + trailing_message_tokens,
            confidence="estimated_trailing" if trailing else "provider_real",
        )

    return estimate_full_context_tokens(context)


def estimate_full_context_tokens(context: Context) -> ContextUsageEstimate:
    """Estimate every serialized request component without provider-usage shortcuts."""

    message_tokens = sum(estimate_message_tokens(message) for message in context.messages)
    system_tokens = estimate_text_tokens(context.system_prompt or "")
    tool_tokens = estimate_text_tokens(_safe_json(context.tools)) if context.tools else 0
    total = message_tokens + system_tokens + tool_tokens
    return ContextUsageEstimate(
        total,
        0,
        total,
        None,
        system_tokens=system_tokens,
        tool_tokens=tool_tokens,
        message_tokens=message_tokens,
    )


def clamp_max_tokens_to_context(model, context: Context, requested: int) -> int:
    if model.context_window <= 0:
        return max(1, requested)
    available = model.context_window - estimate_context_tokens(context).tokens - CONTEXT_SAFETY_TOKENS
    return min(requested, max(1, available))


def estimate_message_tokens(message: Message) -> int:
    content = getattr(message, "content", None)
    role = getattr(message, "role", None)
    if content is None:
        if role in {"compactionSummary", "branchSummary"}:
            chars = len(str(getattr(message, "summary", "") or ""))
            details = getattr(message, "details", None)
            if details is not None:
                chars += len(_safe_json(details))
            return math.ceil(chars / CHARS_PER_TOKEN)
        return estimate_text_tokens(_safe_json(message))
    if role == "user":
        if isinstance(content, str):
            return estimate_text_tokens(content)
        chars = sum(_content_block_chars(block) for block in content)
        return math.ceil(chars / CHARS_PER_TOKEN)
    if role == "toolResult":
        chars = len(str(getattr(message, "tool_call_id", "") or ""))
        chars += len(str(getattr(message, "tool_name", "") or ""))
        chars += sum(_content_block_chars(block) for block in content)
        details = getattr(message, "details", None)
        if details is not None:
            chars += len(_safe_json(details))
        return math.ceil(chars / CHARS_PER_TOKEN)
    chars = 0
    for block in content:
        chars += _content_block_chars(block)
    if role == "assistant":
        provider_data = getattr(message, "provider_data", None)
        for field_name in (
            "reasoning_content",
            "reasoning_details",
            "codex_reasoning_items",
            "codex_message_items",
        ):
            value = getattr(message, field_name, None)
            if value is None and isinstance(provider_data, dict):
                value = provider_data.get(field_name)
            if value is not None:
                chars += len(value) if isinstance(value, str) else len(_safe_json(value))
    return math.ceil(chars / CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _content_block_chars(block: object) -> int:
    if isinstance(block, TextContent):
        return len(block.text) + len(block.text_signature or "")
    if isinstance(block, ThinkingContent):
        return len(block.thinking) + len(block.thinking_signature or "")
    if isinstance(block, ToolCall):
        return (
            len(block.id)
            + len(block.name)
            + len(_safe_json(block.arguments))
            + len(block.thought_signature or "")
        )
    if isinstance(block, ImageContent):
        return ESTIMATED_IMAGE_CHARS
    if isinstance(block, dict):
        if block.get("type") in {"image", "image_url", "input_image"}:
            return ESTIMATED_IMAGE_CHARS
        return len(_safe_json(block))
    return len(str(block or ""))


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
    "calculate_prompt_tokens",
    "calculate_total_tokens",
    "clamp_max_tokens_to_context",
    "estimate_context_tokens",
    "estimate_full_context_tokens",
    "estimate_message_tokens",
    "estimate_messages_tokens",
]
