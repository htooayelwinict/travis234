"""Anthropic Messages API event decoding."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator

from travis.ai.providers._shared import blank_assistant_message as _blank
from travis.ai.providers.sse_common import _StartEventState, _iter_sse_data
from travis.ai.providers.streaming_json import _parse_complete_tool_arguments, _parse_streaming_json_preview
from travis.ai.types import (
    DoneEvent,
    ErrorEvent,
    Model,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    Tool,
    ToolResultMessage,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
)

def _map_anthropic_stop_reason(reason: str | None) -> tuple[str, str | None]:
    if reason in (None, "end_turn", "stop_sequence", "pause_turn"):
        return "stop", None
    if reason == "tool_use":
        return "toolUse", None
    if reason in ("max_tokens", "model_context_window_exceeded"):
        return "length", None
    if reason == "refusal":
        return "error", "The model refused to complete the request"
    return "error", f"Provider stop_reason: {reason}"


def _merge_anthropic_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not isinstance(raw, dict):
        return usage
    input_tokens = int(raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_write = int(raw.get("cache_creation_input_tokens") or 0)
    cache_creation = raw.get("cache_creation")
    cache_write_1h = (
        int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
        if isinstance(cache_creation, dict)
        else 0
    )
    usage.input = input_tokens or usage.input
    usage.output = output_tokens or usage.output
    usage.total_tokens = (usage.input or 0) + (usage.output or 0) + cache_read + cache_write
    if hasattr(usage, "cache_read"):
        usage.cache_read = cache_read or getattr(usage, "cache_read")
    if hasattr(usage, "cache_write"):
        usage.cache_write = cache_write or getattr(usage, "cache_write")
    if hasattr(usage, "cache_write_1h"):
        usage.cache_write_1h = cache_write_1h or getattr(usage, "cache_write_1h")
    output_details = raw.get("output_tokens_details")
    if hasattr(usage, "reasoning") and isinstance(output_details, dict):
        usage.reasoning = int(output_details.get("thinking_tokens") or 0) or getattr(usage, "reasoning")
    return usage


def _parse_anthropic_messages_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
    tools: Iterable[Tool] | None = None,
    is_oauth: bool = False,
) -> Iterator:
    message = _blank(model)
    start_state = _StartEventState(message)
    usage = empty_usage()
    block_slots: dict[int, tuple[str, int]] = {}
    tool_arg_bufs: dict[int, str] = {}
    tool_arg_previews: dict[int, dict] = {}
    stop_reason = "stop"
    error_message: str | None = None
    saw_message_start = False
    saw_message_stop = False

    start = start_state.ensure()
    if start:
        yield start

    try:
        payloads = _iter_sse_data(lines, data_idle_timeout_seconds=data_idle_timeout_seconds, clock=clock)
        for payload in payloads:
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "message_start":
                saw_message_start = True
                raw_message = event.get("message")
                if isinstance(raw_message, dict):
                    if isinstance(raw_message.get("id"), str):
                        message.response_id = raw_message["id"]
                    usage = _merge_anthropic_usage(usage, raw_message.get("usage"))
                continue
            if event_type == "content_block_start":
                index = event.get("index")
                content_block = event.get("content_block")
                if not isinstance(index, int) or not isinstance(content_block, dict):
                    continue
                block_type = content_block.get("type")
                start = start_state.ensure()
                if start:
                    yield start
                content_index = len(message.content)
                if block_type == "text":
                    initial_text = content_block.get("text") if isinstance(content_block.get("text"), str) else ""
                    message.content.append(TextContent(text=initial_text))
                    block_slots[index] = ("text", content_index)
                    yield TextStartEvent(content_index=content_index, partial=message)
                elif block_type == "thinking" and include_reasoning:
                    initial_thinking = content_block.get("thinking") if isinstance(content_block.get("thinking"), str) else ""
                    signature = content_block.get("signature") if isinstance(content_block.get("signature"), str) else None
                    message.content.append(ThinkingContent(thinking=initial_thinking, thinking_signature=signature))
                    block_slots[index] = ("thinking", content_index)
                    yield ThinkingStartEvent(content_index=content_index, partial=message)
                elif block_type == "redacted_thinking" and include_reasoning:
                    signature = content_block.get("data") if isinstance(content_block.get("data"), str) else None
                    message.content.append(
                        ThinkingContent(thinking="[Reasoning redacted]", thinking_signature=signature, redacted=True)
                    )
                    block_slots[index] = ("thinking", content_index)
                    yield ThinkingStartEvent(content_index=content_index, partial=message)
                elif block_type == "tool_use":
                    raw_input = content_block.get("input")
                    initial_args = raw_input if isinstance(raw_input, dict) else {}
                    raw_arguments = json.dumps(initial_args) if initial_args else ""
                    provider_name = str(content_block.get("name") or "")
                    tool_name = provider_name
                    if is_oauth:
                        lower_name = provider_name.lower()
                        matching = next(
                            (tool for tool in tools or [] if tool.name.lower() == lower_name),
                            None,
                        )
                        if matching is not None:
                            tool_name = matching.name
                    message.content.append(
                        ToolCall(
                            id=str(content_block.get("id") or ""),
                            name=tool_name,
                            arguments=initial_args,
                        )
                    )
                    block_slots[index] = ("toolCall", content_index)
                    tool_arg_bufs[content_index] = raw_arguments
                    tool_arg_previews[content_index] = initial_args
                    yield ToolcallStartEvent(content_index=content_index, partial=message)
                continue
            if event_type == "content_block_delta":
                index = event.get("index")
                delta = event.get("delta")
                if not isinstance(index, int) or not isinstance(delta, dict):
                    continue
                slot = block_slots.get(index)
                if slot is None:
                    continue
                kind, content_index = slot
                delta_type = delta.get("type")
                if delta_type == "text_delta" and kind == "text" and isinstance(message.content[content_index], TextContent):
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        message.content[content_index].text += text
                        yield TextDeltaEvent(content_index=content_index, delta=text, partial=message)
                elif (
                    delta_type == "thinking_delta"
                    and kind == "thinking"
                    and isinstance(message.content[content_index], ThinkingContent)
                ):
                    thinking = delta.get("thinking")
                    if isinstance(thinking, str) and thinking:
                        message.content[content_index].thinking += thinking
                        yield ThinkingDeltaEvent(content_index=content_index, delta=thinking, partial=message)
                elif (
                    delta_type == "signature_delta"
                    and kind == "thinking"
                    and isinstance(message.content[content_index], ThinkingContent)
                ):
                    signature = delta.get("signature")
                    if isinstance(signature, str):
                        block = message.content[content_index]
                        block.thinking_signature = (block.thinking_signature or "") + signature
                elif (
                    delta_type == "input_json_delta"
                    and kind == "toolCall"
                    and isinstance(message.content[content_index], ToolCall)
                ):
                    partial_json = delta.get("partial_json")
                    if isinstance(partial_json, str):
                        tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + partial_json
                        arguments_preview = _parse_streaming_json_preview(
                            tool_arg_bufs[content_index],
                            tool_arg_previews.get(content_index),
                        )
                        tool_arg_previews[content_index] = arguments_preview
                        message.content[content_index].arguments = arguments_preview
                        yield ToolcallDeltaEvent(content_index=content_index, delta=partial_json, partial=message)
                continue
            if event_type == "content_block_stop":
                index = event.get("index")
                if not isinstance(index, int):
                    continue
                slot = block_slots.pop(index, None)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind == "text" and isinstance(message.content[content_index], TextContent):
                    yield TextEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].text,
                        partial=message,
                    )
                elif kind == "thinking" and isinstance(message.content[content_index], ThinkingContent):
                    yield ThinkingEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].thinking,
                        partial=message,
                    )
                elif kind == "toolCall" and isinstance(message.content[content_index], ToolCall):
                    message.content[content_index].arguments = _parse_complete_tool_arguments(
                        tool_arg_bufs.get(content_index, "")
                    ) or {}
                    yield ToolcallEndEvent(
                        content_index=content_index,
                        tool_call=message.content[content_index],
                        partial=message,
                    )
                continue
            if event_type == "message_delta":
                delta = event.get("delta")
                if isinstance(delta, dict):
                    reason, mapped_error = _map_anthropic_stop_reason(delta.get("stop_reason"))
                    stop_reason = reason
                    error_message = mapped_error
                usage = _merge_anthropic_usage(usage, event.get("usage"))
                continue
            if event_type == "message_stop":
                saw_message_stop = True
                message.usage = usage
                if stop_reason == "error":
                    message.stop_reason = "error"
                    message.error_message = error_message
                    yield ErrorEvent(reason="error", error=message)
                else:
                    message.stop_reason = stop_reason
                    yield DoneEvent(reason=stop_reason, message=message)
                return
            if event_type == "error":
                message.stop_reason = "error"
                error = event.get("error")
                if isinstance(error, dict):
                    message.error_message = str(error.get("message") or error.get("type") or "Anthropic stream error")
                else:
                    message.error_message = "Anthropic stream error"
                yield ErrorEvent(reason="error", error=message)
                return
    except TimeoutError as error:
        message.stop_reason = "error"
        message.error_message = str(error)
        yield ErrorEvent(reason="error", error=message)
        return

    if saw_message_start and not saw_message_stop:
        message.stop_reason = "error"
        message.error_message = "Anthropic stream ended before message_stop"
        yield ErrorEvent(reason="error", error=message)

decode_anthropic_stream = _parse_anthropic_messages_sse_chunks
