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


def _map_bedrock_stop_reason(reason: object) -> tuple[str, str | None]:
    if reason in {"end_turn", "stop_sequence"}:
        return "stop", None
    if reason in {"max_tokens", "model_context_window_exceeded"}:
        return "length", None
    if reason == "tool_use":
        return "toolUse", None
    return "error", str(reason or "Unknown Bedrock stop reason")


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

    def start_content_block(event: dict[str, object]) -> Iterator[object]:
        raw = event["contentBlockStart"]
        if not isinstance(raw, dict):
            return
        provider_index = int(raw.get("contentBlockIndex") or 0)
        start_data = raw.get("start")
        if not isinstance(start_data, dict):
            return
        start = ensure_start()
        if start:
            yield start
        tool_use = start_data.get("toolUse")
        if not isinstance(tool_use, dict):
            return
        content_index = len(message.content)
        block = ToolCall(
            id=str(tool_use.get("toolUseId") or ""),
            name=str(tool_use.get("name") or ""),
            arguments={},
        )
        message.content.append(block)
        slots[provider_index] = content_index
        tool_buffers[content_index] = ""
        yield ToolcallStartEvent(content_index=content_index, partial=message)

    def apply_text_delta(
        provider_index: int,
        content_index: int | None,
        delta: dict[str, object],
    ) -> Iterator[object]:
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

    def apply_reasoning_delta(
        provider_index: int,
        content_index: int | None,
        reasoning: dict[str, object],
    ) -> Iterator[object]:
        if content_index is None:
            start = ensure_start()
            if start:
                yield start
            content_index = len(message.content)
            message.content.append(ThinkingContent(thinking=""))
            slots[provider_index] = content_index
            yield ThinkingStartEvent(content_index=content_index, partial=message)
        block = message.content[content_index]
        if not isinstance(block, ThinkingContent):
            return
        text = reasoning.get("text")
        if isinstance(text, str) and text:
            block.thinking += text
            yield ThinkingDeltaEvent(content_index=content_index, delta=text, partial=message)
        signature = reasoning.get("signature")
        if isinstance(signature, str) and signature:
            block.thinking_signature = signature

    def apply_tool_delta(
        content_index: int | None,
        tool_delta: dict[str, object],
    ) -> Iterator[object]:
        if content_index is None:
            return
        fragment = str(tool_delta.get("input") or "")
        tool_buffers[content_index] = tool_buffers.get(content_index, "") + fragment
        if fragment:
            yield ToolcallDeltaEvent(content_index=content_index, delta=fragment, partial=message)

    def apply_content_delta(event: dict[str, object]) -> Iterator[object]:
        raw = event["contentBlockDelta"]
        if not isinstance(raw, dict):
            return
        provider_index = int(raw.get("contentBlockIndex") or 0)
        delta = raw.get("delta")
        if not isinstance(delta, dict):
            return
        content_index = slots.get(provider_index)
        if "text" in delta:
            yield from apply_text_delta(provider_index, content_index, delta)
            return
        reasoning = delta.get("reasoningContent")
        if isinstance(reasoning, dict):
            yield from apply_reasoning_delta(provider_index, content_index, reasoning)
            return
        tool_delta = delta.get("toolUse")
        if isinstance(tool_delta, dict):
            yield from apply_tool_delta(content_index, tool_delta)

    def finish_content_block(event: dict[str, object]) -> Iterator[object]:
        raw = event["contentBlockStop"]
        if not isinstance(raw, dict):
            return
        provider_index = int(raw.get("contentBlockIndex") or 0)
        content_index = slots.get(provider_index)
        if content_index is None:
            return
        block = message.content[content_index]
        if isinstance(block, TextContent):
            yield TextEndEvent(content_index=content_index, content=block.text, partial=message)
        elif isinstance(block, ThinkingContent):
            yield ThinkingEndEvent(content_index=content_index, content=block.thinking, partial=message)
        elif isinstance(block, ToolCall):
            raw_arguments = tool_buffers.get(content_index, "")
            block.arguments = _parse_complete_tool_arguments(raw_arguments) or {}
            yield ToolcallEndEvent(content_index=content_index, tool_call=block, partial=message)

    def record_message_stop(event: dict[str, object]) -> None:
        nonlocal error_message, stop_reason
        raw = event["messageStop"]
        reason = raw.get("stopReason") if isinstance(raw, dict) else None
        stop_reason, error_message = _map_bedrock_stop_reason(reason)

    def record_usage(event: dict[str, object]) -> None:
        raw = event["metadata"]
        usage = raw.get("usage") if isinstance(raw, dict) else None
        if not isinstance(usage, dict):
            return
        cache_read = int(usage.get("cacheReadInputTokens") or 0)
        cache_write = int(usage.get("cacheWriteInputTokens") or 0)
        input_tokens = int(usage.get("inputTokens") or 0)
        message.usage.input = max(0, input_tokens - cache_read - cache_write)
        message.usage.output = int(usage.get("outputTokens") or 0)
        message.usage.cache_read = cache_read
        message.usage.cache_write = cache_write
        message.usage.total_tokens = int(usage.get("totalTokens") or 0)

    def raise_provider_exception(event: dict[str, object]) -> None:
        for exception_name in (
            "internalServerException",
            "modelStreamErrorException",
            "validationException",
            "throttlingException",
            "serviceUnavailableException",
        ):
            if exception_name not in event:
                continue
            raw = event[exception_name]
            detail = raw.get("message") if isinstance(raw, dict) else raw
            raise RuntimeError(f"{exception_name}: {detail}")

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
                yield from start_content_block(event)
                continue
            if "contentBlockDelta" in event:
                yield from apply_content_delta(event)
                continue
            if "contentBlockStop" in event:
                yield from finish_content_block(event)
                continue
            if "messageStop" in event:
                record_message_stop(event)
                continue
            if "metadata" in event:
                record_usage(event)
                continue
            raise_provider_exception(event)

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
