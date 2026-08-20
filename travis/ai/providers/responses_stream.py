"""OpenAI Responses API event decoding."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from travis.ai.providers._shared import blank_assistant_message as _blank
from travis.ai.providers.sse_common import _StartEventState, _iter_sse_data
from travis.ai.providers.responses_translation import encode_text_signature
from travis.ai.providers.streaming_json import (
    _parse_complete_tool_arguments,
    _parse_streaming_json,
    _parse_streaming_json_preview,
)
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
    ToolResultMessage,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
)

def _responses_tool_call_id(call_id: str, item_id: str | None) -> str:
    return f"{call_id}|{item_id}" if item_id else call_id


def _map_responses_status(
    status: str | None,
    incomplete_reason: str | None = None,
) -> tuple[str, str | None]:
    if status in (None, "completed", "in_progress", "queued"):
        return "stop", None
    if status == "incomplete":
        if incomplete_reason in {"max_output_tokens", "max_tokens"}:
            return "length", None
        if incomplete_reason:
            return "error", f"Response incomplete: {incomplete_reason}"
        return "error", "Response incomplete without a provider reason"
    if status in ("failed", "cancelled"):
        return "error", f"Provider response status: {status}"
    return "error", f"Provider response status: {status}"


def _merge_responses_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not isinstance(raw, dict):
        return usage
    details = raw.get("input_tokens_details")
    cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    cache_write = int(details.get("cache_write_tokens") or 0) if isinstance(details, dict) else 0
    output_details = raw.get("output_tokens_details")
    reasoning = int(output_details.get("reasoning_tokens") or 0) if isinstance(output_details, dict) else 0
    input_tokens = int(raw.get("input_tokens") or 0)
    usage.input = max(0, input_tokens - cached - cache_write) or usage.input
    usage.output = int(raw.get("output_tokens") or 0) or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    if hasattr(usage, "cache_read"):
        usage.cache_read = cached or getattr(usage, "cache_read")
    if hasattr(usage, "cache_write"):
        usage.cache_write = cache_write or getattr(usage, "cache_write")
    if hasattr(usage, "reasoning"):
        usage.reasoning = reasoning or getattr(usage, "reasoning")
    return usage


def _parse_codex_responses_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
) -> Iterator:
    message = _blank(model)
    start_state = _StartEventState(message)
    usage = empty_usage()
    output_slots: dict[int, tuple[str, int]] = {}
    tool_arg_bufs: dict[int, str] = {}
    tool_arg_previews: dict[int, dict] = {}
    reasoning_blocks_by_id: dict[str, ThinkingContent] = {}
    completed = False

    start = start_state.ensure()
    if start:
        yield start

    def backfill_reasoning_signatures(response_output: Any) -> None:
        if not isinstance(response_output, list):
            return
        for item in response_output:
            if not isinstance(item, dict) or item.get("type") != "reasoning":
                continue
            item_id = item.get("id")
            encrypted_content = item.get("encrypted_content")
            if not isinstance(item_id, str) or not encrypted_content:
                continue
            block = reasoning_blocks_by_id.get(item_id)
            if block is None or not block.thinking_signature:
                continue
            try:
                stored_item = json.loads(block.thinking_signature)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(stored_item, dict) or stored_item.get("encrypted_content"):
                continue
            stored_item["encrypted_content"] = encrypted_content
            block.thinking_signature = json.dumps(stored_item, separators=(",", ":"))

    def create_slot(output_index: int, item: dict[str, Any]) -> Iterator:
        item_type = item.get("type")
        if item_type == "reasoning":
            if not include_reasoning:
                return
            start = start_state.ensure()
            if start:
                yield start
            content_index = len(message.content)
            message.content.append(ThinkingContent(thinking="", thinking_signature=None))
            output_slots[output_index] = ("thinking", content_index)
            yield ThinkingStartEvent(content_index=content_index, partial=message)
            return
        if item_type == "message":
            start = start_state.ensure()
            if start:
                yield start
            content_index = len(message.content)
            message.content.append(TextContent(text=""))
            output_slots[output_index] = ("text", content_index)
            yield TextStartEvent(content_index=content_index, partial=message)
            return
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            item_id = str(item.get("id") or "") or None
            name = str(item.get("name") or "")
            raw_arguments = item.get("arguments") if isinstance(item.get("arguments"), str) else ""
            start = start_state.ensure()
            if start:
                yield start
            content_index = len(message.content)
            arguments_preview = _parse_streaming_json_preview(raw_arguments)
            block = ToolCall(
                id=_responses_tool_call_id(call_id, item_id),
                name=name,
                arguments=arguments_preview,
            )
            message.content.append(block)
            output_slots[output_index] = ("toolCall", content_index)
            tool_arg_bufs[content_index] = raw_arguments
            tool_arg_previews[content_index] = arguments_preview
            yield ToolcallStartEvent(content_index=content_index, partial=message)

    def get_slot(output_index: int, item: dict[str, Any] | None = None) -> tuple[str, int] | None:
        slot = output_slots.get(output_index)
        if slot is None and item is not None:
            for event in create_slot(output_index, item):
                pending_events.append(event)
            slot = output_slots.get(output_index)
        return slot

    pending_events: list[Any] = []

    def record_response_created(event: dict[str, object]) -> None:
        response = event.get("response")
        if isinstance(response, dict) and isinstance(response.get("id"), str):
            message.response_id = response["id"]

    def add_output_item(event: dict[str, object]) -> Iterator[object]:
        item = event.get("item")
        output_index = event.get("output_index")
        if isinstance(item, dict) and isinstance(output_index, int):
            yield from create_slot(output_index, item)

    def apply_text_delta(event: dict[str, object]) -> Iterator[object]:
        output_index = event.get("output_index")
        if not isinstance(output_index, int):
            return
        slot = output_slots.get(output_index)
        if slot is None:
            return
        kind, content_index = slot
        block = message.content[content_index]
        if kind != "text" or not isinstance(block, TextContent):
            return
        delta = event.get("delta")
        if not isinstance(delta, str) or not delta:
            return
        block.text += delta
        yield TextDeltaEvent(content_index=content_index, delta=delta, partial=message)

    def apply_thinking_delta(event: dict[str, object]) -> Iterator[object]:
        if not include_reasoning:
            return
        output_index = event.get("output_index")
        if not isinstance(output_index, int):
            return
        slot = output_slots.get(output_index)
        if slot is None:
            return
        kind, content_index = slot
        block = message.content[content_index]
        if kind != "thinking" or not isinstance(block, ThinkingContent):
            return
        delta = event.get("delta")
        if not isinstance(delta, str) or not delta:
            return
        block.thinking += delta
        yield ThinkingDeltaEvent(content_index=content_index, delta=delta, partial=message)

    def append_reasoning_separator(event: dict[str, object]) -> Iterator[object]:
        if not include_reasoning:
            return
        output_index = event.get("output_index")
        if not isinstance(output_index, int):
            return
        slot = output_slots.get(output_index)
        if slot is None:
            return
        kind, content_index = slot
        block = message.content[content_index]
        if kind != "thinking" or not isinstance(block, ThinkingContent):
            return
        block.thinking += "\n\n"
        yield ThinkingDeltaEvent(content_index=content_index, delta="\n\n", partial=message)

    def apply_tool_delta(event: dict[str, object]) -> Iterator[object]:
        output_index = event.get("output_index")
        if not isinstance(output_index, int):
            return
        slot = output_slots.get(output_index)
        if slot is None:
            return
        kind, content_index = slot
        block = message.content[content_index]
        if kind != "toolCall" or not isinstance(block, ToolCall):
            return
        delta = event.get("delta")
        if not isinstance(delta, str):
            return
        tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + delta
        arguments_preview = _parse_streaming_json_preview(
            tool_arg_bufs[content_index],
            tool_arg_previews.get(content_index),
        )
        tool_arg_previews[content_index] = arguments_preview
        block.arguments = arguments_preview
        yield ToolcallDeltaEvent(content_index=content_index, delta=delta, partial=message)

    def finish_tool_arguments(event: dict[str, object]) -> Iterator[object]:
        output_index = event.get("output_index")
        if not isinstance(output_index, int):
            return
        slot = output_slots.get(output_index)
        if slot is None:
            return
        kind, content_index = slot
        block = message.content[content_index]
        if kind != "toolCall" or not isinstance(block, ToolCall):
            return
        arguments = event.get("arguments")
        if not isinstance(arguments, str):
            return
        previous = tool_arg_bufs.get(content_index, "")
        tool_arg_bufs[content_index] = arguments
        block.arguments = _parse_streaming_json(arguments)
        tool_arg_previews.pop(content_index, None)
        if arguments.startswith(previous):
            delta = arguments[len(previous):]
            if delta:
                yield ToolcallDeltaEvent(content_index=content_index, delta=delta, partial=message)

    def finish_text_item(
        item: dict[str, object],
        content_index: int,
        block: TextContent,
    ) -> TextEndEvent:
        parts = item.get("content")
        if isinstance(parts, list):
            text = "".join(
                str(part.get("text") or part.get("refusal") or "")
                for part in parts
                if isinstance(part, dict)
            )
            if text:
                block.text = text
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            phase = item.get("phase") if item.get("phase") in {"commentary", "final_answer"} else None
            block.text_signature = encode_text_signature(item_id, phase)
        return TextEndEvent(
            content_index=content_index,
            content=block.text,
            partial=message,
        )

    def finish_thinking_item(
        item: dict[str, object],
        content_index: int,
        block: ThinkingContent,
    ) -> ThinkingEndEvent:
        summary = item.get("summary")
        if isinstance(summary, list):
            text = "\n\n".join(
                str(part.get("text") or "")
                for part in summary
                if isinstance(part, dict) and part.get("text")
            )
            if text:
                block.thinking = text
        block.thinking_signature = json.dumps(item)
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            reasoning_blocks_by_id[item_id] = block
        return ThinkingEndEvent(
            content_index=content_index,
            content=block.thinking,
            partial=message,
        )

    def finish_tool_item(
        item: dict[str, object],
        content_index: int,
        block: ToolCall,
    ) -> ToolcallEndEvent:
        raw_arguments = item.get("arguments")
        if isinstance(raw_arguments, str):
            tool_arg_bufs[content_index] = raw_arguments
        block.arguments = _parse_complete_tool_arguments(
            tool_arg_bufs.get(content_index, "")
        ) or {}
        return ToolcallEndEvent(
            content_index=content_index,
            tool_call=block,
            partial=message,
        )

    def finish_output_item(event: dict[str, object]) -> Iterator[object]:
        item = event.get("item")
        output_index = event.get("output_index")
        if not isinstance(item, dict) or not isinstance(output_index, int):
            return
        pending_events.clear()
        slot = get_slot(output_index, item)
        while pending_events:
            yield pending_events.pop(0)
        if slot is None:
            return
        kind, content_index = slot
        block = message.content[content_index]
        if kind == "text" and isinstance(block, TextContent):
            yield finish_text_item(item, content_index, block)
        elif kind == "thinking" and isinstance(block, ThinkingContent):
            yield finish_thinking_item(item, content_index, block)
        elif kind == "toolCall" and isinstance(block, ToolCall):
            yield finish_tool_item(item, content_index, block)
        output_slots.pop(output_index, None)

    def finish_response(event: dict[str, object]) -> DoneEvent | ErrorEvent | None:
        nonlocal completed, usage
        response = event.get("response")
        if not isinstance(response, dict):
            return None
        completed = True
        backfill_reasoning_signatures(response.get("output"))
        if isinstance(response.get("id"), str):
            message.response_id = response["id"]
        usage = _merge_responses_usage(usage, response.get("usage"))
        incomplete_details = response.get("incomplete_details")
        incomplete_reason = (
            incomplete_details.get("reason")
            if isinstance(incomplete_details, dict)
            and isinstance(incomplete_details.get("reason"), str)
            else None
        )
        reason, error_message = _map_responses_status(
            response.get("status"),
            incomplete_reason,
        )
        if reason == "stop" and any(isinstance(block, ToolCall) for block in message.content):
            reason = "toolUse"
        message.usage = usage
        message.stop_reason = reason
        if reason == "error":
            message.error_message = error_message
            return ErrorEvent(reason="error", error=message)
        return DoneEvent(reason=reason, message=message)

    def failed_response(event: dict[str, object]) -> ErrorEvent:
        message.stop_reason = "error"
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else None
        if isinstance(error, dict):
            message.error_message = str(error.get("message") or error.get("code") or "Provider response failed")
        else:
            message.error_message = "Provider response failed"
        return ErrorEvent(reason="error", error=message)

    def generic_error(event: dict[str, object]) -> ErrorEvent:
        message.stop_reason = "error"
        code = event.get("code")
        detail = event.get("message")
        message.error_message = f"Error Code {code}: {detail}" if code or detail else "Unknown error"
        return ErrorEvent(reason="error", error=message)

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
            if event_type == "response.created":
                record_response_created(event)
                continue
            if event_type == "response.output_item.added":
                yield from add_output_item(event)
                continue
            if event_type in ("response.output_text.delta", "response.refusal.delta"):
                yield from apply_text_delta(event)
                continue
            if event_type in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                yield from apply_thinking_delta(event)
                continue
            if event_type == "response.reasoning_summary_part.done":
                yield from append_reasoning_separator(event)
                continue
            if event_type == "response.function_call_arguments.delta":
                yield from apply_tool_delta(event)
                continue
            if event_type == "response.function_call_arguments.done":
                yield from finish_tool_arguments(event)
                continue
            if event_type == "response.output_item.done":
                yield from finish_output_item(event)
                continue
            if event_type in ("response.completed", "response.incomplete"):
                terminal_event = finish_response(event)
                if terminal_event is not None:
                    yield terminal_event
                    return
            if event_type == "response.failed":
                yield failed_response(event)
                return
            if event_type == "error":
                yield generic_error(event)
                return
    except TimeoutError as error:
        message.stop_reason = "error"
        message.error_message = str(error)
        yield ErrorEvent(reason="error", error=message)
        return

    if not completed:
        message.stop_reason = "error"
        message.error_message = "Responses stream ended before a terminal response event"
        yield ErrorEvent(reason="error", error=message)

decode_responses_stream = _parse_codex_responses_sse_chunks
