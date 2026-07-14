from __future__ import annotations

import json
import time
from typing import Callable, Iterable, Iterator

from travis.ai.providers._shared import blank_assistant_message
from travis.ai.providers.sse_common import _StartEventState, _iter_sse_data
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
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    empty_usage,
    now_ms,
)


def _parse_google_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
) -> Iterator:
    message = blank_assistant_message(model)
    start_state = _StartEventState(message)
    current: TextContent | ThinkingContent | None = None
    stop_reason = "stop"
    error_message: str | None = None
    tool_counter = 0

    start = start_state.ensure()
    if start:
        yield start

    def end_current() -> Iterator:
        nonlocal current
        if current is None:
            return
        index = message.content.index(current)
        if isinstance(current, TextContent):
            yield TextEndEvent(content_index=index, content=current.text, partial=message)
        else:
            yield ThinkingEndEvent(content_index=index, content=current.thinking, partial=message)
        current = None

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
            raw_error = chunk.get("error")
            if isinstance(raw_error, dict):
                stop_reason = "error"
                error_message = str(raw_error.get("message") or raw_error)
                break
            response_id = chunk.get("responseId")
            if isinstance(response_id, str) and response_id and not message.response_id:
                message.response_id = response_id
            candidates = chunk.get("candidates")
            candidate = candidates[0] if isinstance(candidates, list) and candidates else None
            if isinstance(candidate, dict):
                content = candidate.get("content")
                parts = content.get("parts") if isinstance(content, dict) else None
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        text = part.get("text")
                        is_thinking = part.get("thought") is True
                        if isinstance(text, str) and (include_reasoning or not is_thinking):
                            desired_type = ThinkingContent if is_thinking else TextContent
                            if current is None or not isinstance(current, desired_type):
                                yield from end_current()
                                start = start_state.ensure()
                                if start:
                                    yield start
                                if is_thinking:
                                    current = ThinkingContent(
                                        thinking="",
                                        thinking_signature=part.get("thoughtSignature")
                                        if isinstance(part.get("thoughtSignature"), str)
                                        else None,
                                    )
                                    message.content.append(current)
                                    yield ThinkingStartEvent(content_index=len(message.content) - 1, partial=message)
                                else:
                                    current = TextContent(text="")
                                    message.content.append(current)
                                    yield TextStartEvent(content_index=len(message.content) - 1, partial=message)
                            index = message.content.index(current)
                            signature = part.get("thoughtSignature")
                            if isinstance(current, ThinkingContent):
                                current.thinking += text
                                if isinstance(signature, str) and signature:
                                    current.thinking_signature = signature
                                yield ThinkingDeltaEvent(content_index=index, delta=text, partial=message)
                            else:
                                current.text += text
                                if isinstance(signature, str) and signature:
                                    current.text_signature = signature
                                yield TextDeltaEvent(content_index=index, delta=text, partial=message)
                        function_call = part.get("functionCall")
                        if isinstance(function_call, dict):
                            yield from end_current()
                            start = start_state.ensure()
                            if start:
                                yield start
                            tool_counter += 1
                            name = str(function_call.get("name") or "")
                            provided_id = function_call.get("id")
                            duplicate_id = bool(
                                provided_id
                                and any(
                                    isinstance(block, ToolCall) and block.id == provided_id
                                    for block in message.content
                                )
                            )
                            call_id = (
                                str(provided_id)
                                if provided_id and not duplicate_id
                                else f"{name}_{now_ms()}_{tool_counter}"
                            )
                            arguments = function_call.get("args")
                            tool_call = ToolCall(
                                id=call_id,
                                name=name,
                                arguments=arguments if isinstance(arguments, dict) else {},
                                thought_signature=part.get("thoughtSignature")
                                if isinstance(part.get("thoughtSignature"), str)
                                else None,
                            )
                            message.content.append(tool_call)
                            index = len(message.content) - 1
                            yield ToolcallStartEvent(content_index=index, partial=message)
                            yield ToolcallDeltaEvent(
                                content_index=index,
                                delta=json.dumps(tool_call.arguments, separators=(",", ":")),
                                partial=message,
                            )
                            yield ToolcallEndEvent(
                                content_index=index,
                                tool_call=tool_call,
                                partial=message,
                            )
                finish_reason = candidate.get("finishReason")
                if finish_reason == "MAX_TOKENS":
                    stop_reason = "length"
                elif finish_reason and finish_reason != "STOP":
                    stop_reason = "error"
                    error_message = f"Provider finish_reason: {finish_reason}"
            raw_usage = chunk.get("usageMetadata")
            if isinstance(raw_usage, dict):
                prompt = int(raw_usage.get("promptTokenCount") or 0)
                cached = int(raw_usage.get("cachedContentTokenCount") or 0)
                candidates_tokens = int(raw_usage.get("candidatesTokenCount") or 0)
                thoughts = int(raw_usage.get("thoughtsTokenCount") or 0)
                usage = empty_usage()
                usage.input = max(0, prompt - cached)
                usage.output = candidates_tokens + thoughts
                usage.cache_read = cached
                usage.reasoning = thoughts
                usage.total_tokens = int(raw_usage.get("totalTokenCount") or 0)
                message.usage = usage

        yield from end_current()
        start = start_state.ensure()
        if start:
            yield start
        if any(isinstance(block, ToolCall) for block in message.content):
            stop_reason = "toolUse"
        message.stop_reason = stop_reason
        if stop_reason == "error":
            message.error_message = error_message or "Google provider stream failed"
            yield ErrorEvent(reason="error", error=message)
        else:
            yield DoneEvent(reason=stop_reason, message=message)
    except Exception as exc:
        message.stop_reason = "error"
        message.error_message = str(exc)
        start = start_state.ensure()
        if start:
            yield start
        yield ErrorEvent(reason="error", error=message)


__all__ = ["_parse_google_sse_chunks"]
