"""Mistral Conversations streaming decoder."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from travis.ai.providers._shared import blank_assistant_message
from travis.ai.providers.sse_common import _iter_sse_data
from travis.ai.providers.streaming_json import _parse_streaming_json
from travis.ai.types import (
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
)


def _cached_prompt_tokens(raw_usage: dict[str, Any], prompt_tokens: int) -> int:
    candidates = (
        ((raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
        ((raw_usage.get("promptTokensDetails") or {}).get("cachedTokens")),
        ((raw_usage.get("prompt_token_details") or {}).get("cached_tokens")),
        ((raw_usage.get("promptTokenDetails") or {}).get("cachedTokens")),
        raw_usage.get("num_cached_tokens"),
        raw_usage.get("numCachedTokens"),
    )
    cached = next((value for value in candidates if isinstance(value, (int, float))), 0)
    return min(prompt_tokens, max(0, int(cached)))


def _mistral_stop_reason(reason: str | None) -> str:
    if reason in {None, "stop"}:
        return "stop"
    if reason in {"length", "model_length"}:
        return "length"
    if reason == "tool_calls":
        return "toolUse"
    if reason == "error":
        return "error"
    return "stop"


def _decode_mistral_stream(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
) -> Iterator:
    message = blank_assistant_message(model)
    current: TextContent | ThinkingContent | None = None
    tool_blocks: dict[object, tuple[int, str]] = {}
    stop_reason = "stop"

    yield StartEvent(partial=message)

    def finish_current() -> Iterator:
        nonlocal current
        if current is not None:
            index = message.content.index(current)
            event = (
                TextEndEvent(content_index=index, content=current.text, partial=message)
                if isinstance(current, TextContent)
                else ThinkingEndEvent(content_index=index, content=current.thinking, partial=message)
            )
            current = None
            yield event

    try:
        for payload in _iter_sse_data(
            lines,
            data_idle_timeout_seconds=data_idle_timeout_seconds,
            clock=clock,
        ):
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(chunk, dict):
                continue
            if not message.response_id and isinstance(chunk.get("id"), str):
                message.response_id = chunk["id"]
            raw_usage = chunk.get("usage")
            if isinstance(raw_usage, dict):
                prompt = int(raw_usage.get("prompt_tokens", raw_usage.get("promptTokens", 0)) or 0)
                cached = _cached_prompt_tokens(raw_usage, prompt)
                message.usage.input = max(0, prompt - cached)
                message.usage.output = int(
                    raw_usage.get("completion_tokens", raw_usage.get("completionTokens", 0)) or 0
                )
                message.usage.cache_read = cached
                message.usage.cache_write = 0
                message.usage.total_tokens = int(
                    raw_usage.get("total_tokens", raw_usage.get("totalTokens", 0))
                    or message.usage.input + message.usage.output + cached
                )

            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else None
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason", choice.get("finishReason"))
            if isinstance(finish_reason, str):
                stop_reason = _mistral_stop_reason(finish_reason)
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            content_items = [content] if isinstance(content, str) else content if isinstance(content, list) else []
            for item in content_items:
                if isinstance(item, str):
                    text = item
                    kind = "text"
                elif isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text") or "")
                    kind = "text"
                elif isinstance(item, dict) and item.get("type") == "thinking":
                    if not include_reasoning:
                        continue
                    thinking = item.get("thinking")
                    text = "".join(
                        str(part.get("text") or "")
                        for part in thinking
                        if isinstance(part, dict) and part.get("type") == "text"
                    ) if isinstance(thinking, list) else ""
                    kind = "thinking"
                else:
                    continue
                if not text:
                    continue
                desired = TextContent if kind == "text" else ThinkingContent
                if current is None or not isinstance(current, desired):
                    yield from finish_current()
                    if kind == "text":
                        current = TextContent(text="")
                        message.content.append(current)
                        yield TextStartEvent(content_index=len(message.content) - 1, partial=message)
                    else:
                        current = ThinkingContent(thinking="")
                        message.content.append(current)
                        yield ThinkingStartEvent(content_index=len(message.content) - 1, partial=message)
                index = message.content.index(current)
                if isinstance(current, TextContent):
                    current.text += text
                    yield TextDeltaEvent(content_index=index, delta=text, partial=message)
                else:
                    current.thinking += text
                    yield ThinkingDeltaEvent(content_index=index, delta=text, partial=message)

            tool_calls = delta.get("tool_calls", delta.get("toolCalls"))
            for position, raw_call in enumerate(tool_calls if isinstance(tool_calls, list) else []):
                if not isinstance(raw_call, dict):
                    continue
                stream_index = raw_call.get("index")
                call_id = str(raw_call.get("id") or "")
                key: object = stream_index if isinstance(stream_index, int) else call_id or position
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    function = {}
                entry = tool_blocks.get(key)
                if entry is None:
                    yield from finish_current()
                    block = ToolCall(
                        id=call_id,
                        name=str(function.get("name") or ""),
                        arguments={},
                    )
                    message.content.append(block)
                    content_index = len(message.content) - 1
                    tool_blocks[key] = (content_index, "")
                    yield ToolcallStartEvent(content_index=content_index, partial=message)
                else:
                    content_index, _buffer = entry
                    block = message.content[content_index]
                    if not isinstance(block, ToolCall):
                        continue
                    if call_id and not block.id:
                        block.id = call_id
                    name = function.get("name")
                    if isinstance(name, str) and name and not block.name:
                        block.name = name
                content_index, buffer = tool_blocks[key]
                block = message.content[content_index]
                arguments = function.get("arguments")
                fragment = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, separators=(",", ":"))
                buffer += fragment
                tool_blocks[key] = (content_index, buffer)
                if isinstance(block, ToolCall):
                    block.arguments = _parse_streaming_json(buffer)
                yield ToolcallDeltaEvent(content_index=content_index, delta=fragment, partial=message)

        yield from finish_current()
        for content_index, buffer in tool_blocks.values():
            block = message.content[content_index]
            if not isinstance(block, ToolCall):
                continue
            block.arguments = _parse_streaming_json(buffer)
            yield ToolcallEndEvent(content_index=content_index, tool_call=block, partial=message)
        if any(isinstance(block, ToolCall) for block in message.content) and stop_reason == "stop":
            stop_reason = "toolUse"
        message.stop_reason = stop_reason
        if stop_reason == "error":
            message.error_message = "Mistral provider stream failed"
            yield ErrorEvent(reason="error", error=message)
        else:
            yield DoneEvent(reason=stop_reason, message=message)
    except Exception as exc:
        message.stop_reason = "error"
        message.error_message = str(exc)
        yield ErrorEvent(reason="error", error=message)


__all__ = ["_decode_mistral_stream"]
