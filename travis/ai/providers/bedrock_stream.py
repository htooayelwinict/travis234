from __future__ import annotations

from typing import Iterable, Iterator

from travis.ai.providers._shared import blank_assistant_message
from travis.ai.providers.streaming_json import _parse_complete_tool_arguments
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


def _parse_bedrock_events(events: Iterable[dict], model: Model) -> Iterator:
    message = blank_assistant_message(model)
    started = False
    slots: dict[int, int] = {}
    tool_buffers: dict[int, str] = {}
    stop_reason = "stop"
    error_message: str | None = None

    def ensure_start() -> StartEvent | None:
        nonlocal started
        if started:
            return None
        started = True
        return StartEvent(partial=message)

    try:
        for event in events:
            if not isinstance(event, dict):
                continue
            if "messageStart" in event:
                start = ensure_start()
                if start:
                    yield start
                continue
            if "contentBlockStart" in event:
                raw = event["contentBlockStart"]
                if not isinstance(raw, dict):
                    continue
                provider_index = int(raw.get("contentBlockIndex") or 0)
                start_data = raw.get("start")
                if not isinstance(start_data, dict):
                    continue
                start = ensure_start()
                if start:
                    yield start
                content_index = len(message.content)
                tool_use = start_data.get("toolUse")
                if isinstance(tool_use, dict):
                    block = ToolCall(
                        id=str(tool_use.get("toolUseId") or ""),
                        name=str(tool_use.get("name") or ""),
                        arguments={},
                    )
                    message.content.append(block)
                    slots[provider_index] = content_index
                    tool_buffers[content_index] = ""
                    yield ToolcallStartEvent(content_index=content_index, partial=message)
                continue
            if "contentBlockDelta" in event:
                raw = event["contentBlockDelta"]
                if not isinstance(raw, dict):
                    continue
                provider_index = int(raw.get("contentBlockIndex") or 0)
                delta = raw.get("delta")
                if not isinstance(delta, dict):
                    continue
                content_index = slots.get(provider_index)
                if "text" in delta:
                    text = str(delta.get("text") or "")
                    if content_index is None:
                        start = ensure_start()
                        if start:
                            yield start
                        content_index = len(message.content)
                        message.content.append(TextContent(text=""))
                        slots[provider_index] = content_index
                        yield TextStartEvent(content_index=content_index, partial=message)
                    block = message.content[content_index]
                    if isinstance(block, TextContent) and text:
                        block.text += text
                        yield TextDeltaEvent(content_index=content_index, delta=text, partial=message)
                    continue
                reasoning = delta.get("reasoningContent")
                if isinstance(reasoning, dict):
                    text = reasoning.get("text")
                    if content_index is None:
                        start = ensure_start()
                        if start:
                            yield start
                        content_index = len(message.content)
                        message.content.append(ThinkingContent(thinking=""))
                        slots[provider_index] = content_index
                        yield ThinkingStartEvent(content_index=content_index, partial=message)
                    block = message.content[content_index]
                    if isinstance(block, ThinkingContent):
                        if isinstance(text, str) and text:
                            block.thinking += text
                            yield ThinkingDeltaEvent(content_index=content_index, delta=text, partial=message)
                        signature = reasoning.get("signature")
                        if isinstance(signature, str) and signature:
                            block.thinking_signature = signature
                    continue
                tool_delta = delta.get("toolUse")
                if isinstance(tool_delta, dict) and content_index is not None:
                    fragment = str(tool_delta.get("input") or "")
                    tool_buffers[content_index] = tool_buffers.get(content_index, "") + fragment
                    if fragment:
                        yield ToolcallDeltaEvent(content_index=content_index, delta=fragment, partial=message)
                continue
            if "contentBlockStop" in event:
                raw = event["contentBlockStop"]
                if not isinstance(raw, dict):
                    continue
                provider_index = int(raw.get("contentBlockIndex") or 0)
                content_index = slots.get(provider_index)
                if content_index is None:
                    continue
                block = message.content[content_index]
                if isinstance(block, TextContent):
                    yield TextEndEvent(content_index=content_index, content=block.text, partial=message)
                elif isinstance(block, ThinkingContent):
                    yield ThinkingEndEvent(content_index=content_index, content=block.thinking, partial=message)
                elif isinstance(block, ToolCall):
                    raw_arguments = tool_buffers.get(content_index, "")
                    block.arguments = _parse_complete_tool_arguments(raw_arguments) or {}
                    yield ToolcallEndEvent(content_index=content_index, tool_call=block, partial=message)
                continue
            if "messageStop" in event:
                raw = event["messageStop"]
                reason = raw.get("stopReason") if isinstance(raw, dict) else None
                if reason in {"end_turn", "stop_sequence"}:
                    stop_reason = "stop"
                elif reason in {"max_tokens", "model_context_window_exceeded"}:
                    stop_reason = "length"
                elif reason == "tool_use":
                    stop_reason = "toolUse"
                else:
                    stop_reason = "error"
                    error_message = str(reason or "Unknown Bedrock stop reason")
                continue
            if "metadata" in event:
                raw = event["metadata"]
                usage = raw.get("usage") if isinstance(raw, dict) else None
                if isinstance(usage, dict):
                    cache_read = int(usage.get("cacheReadInputTokens") or 0)
                    cache_write = int(usage.get("cacheWriteInputTokens") or 0)
                    input_tokens = int(usage.get("inputTokens") or 0)
                    message.usage.input = max(0, input_tokens - cache_read - cache_write)
                    message.usage.output = int(usage.get("outputTokens") or 0)
                    message.usage.cache_read = cache_read
                    message.usage.cache_write = cache_write
                    message.usage.total_tokens = int(usage.get("totalTokens") or 0)
                continue
            for exception_name in (
                "internalServerException",
                "modelStreamErrorException",
                "validationException",
                "throttlingException",
                "serviceUnavailableException",
            ):
                if exception_name in event:
                    raw = event[exception_name]
                    detail = raw.get("message") if isinstance(raw, dict) else raw
                    raise RuntimeError(f"{exception_name}: {detail}")

        start = ensure_start()
        if start:
            yield start
        message.stop_reason = stop_reason
        if stop_reason == "error":
            message.error_message = error_message or "Bedrock provider stream failed"
            yield ErrorEvent(reason="error", error=message)
        else:
            yield DoneEvent(reason=stop_reason, message=message)
    except Exception as exc:
        start = ensure_start()
        if start:
            yield start
        message.stop_reason = "error"
        message.error_message = str(exc)
        yield ErrorEvent(reason="error", error=message)


__all__ = ["_parse_bedrock_events"]
