"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Iterator

import httpx

from travis.ai.env_config import ModelConfig, load_model_config
from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers._shared import blank_assistant_message as _blank
from travis.ai.providers.capabilities import build_generation_payload
from travis.ai.providers.catalog import get_provider_profile, resolve_provider_runtime
from travis.ai.providers.message_sanitization import repair_tool_call_arguments
from travis.ai.providers.params import GenerationParams, merge_generation_params
from travis.ai.providers.transports import get_transport
from travis.ai.stream import ApiProvider
from travis.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
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
    Tool,
    ToolCall,
    ToolResultMessage,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)
from travis.ai.validation import ToolValidationError, validate_tool_arguments
PROVIDER_API = "openai-completions"
PARTIAL_STREAM_STUB_ID = "partial-stream-stub"
PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE = "partial_stream_dropped_tool_calls"
MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE = "malformed_streamed_tool_call_arguments"
LEAKED_TOOL_PROTOCOL_TEXT_CODE = "leaked_tool_protocol_text"
_MUTATING_TOOL_REQUIRED_ARGUMENTS = {"write": ("path", "content")}
_VALID_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")
_BILLING_PATTERNS = (
    "key limit exceeded",
    "spending limit",
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "credits exhausted",
    "no usable credits",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "plan does not include",
    "out of funds",
    "run out of funds",
)
_OPENROUTER_POLICY_PATTERNS = (
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
)
_OPENROUTER_PROMPT_GUARDRAIL_PATTERNS = (
    "prompt injection patterns detected",
    "system_prefix_spoofing",
)
_TOOL_CALL_LEAK_PATTERN = re.compile(r"(?:^|[\s>|])to=functions\.[A-Za-z_][\w.]*", re.IGNORECASE)
_TOOL_PROTOCOL_XML_BLOCK_PATTERN = re.compile(
    r"(?is)"
    r"<(?:tool_call|tool_calls|tool_response|function_call|function_calls)\b[^>]*>.*?"
    r"</(?:tool_call|tool_calls|tool_response|function_call|function_calls)>"
    r"|<function\s+name\s*=\s*[\"'][^\"']+[\"'][^>]*>.*?</function>"
)
_TOOL_PROTOCOL_XML_LINE_PATTERN = re.compile(
    r"(?im)^[ \t]*</?(?:parameter|function|tool_call|tool_calls|tool_response|function_call|function_calls)"
    r"(?:[\s=>][^>]*)?>[ \t]*(?:\r?\n|$)"
)
_TOOL_PROTOCOL_XML_PREFIX_PATTERN = re.compile(
    r"(?is)(?:^|[\s>])(?:"
    r"</?(?:tool_call|tool_calls|tool_response|function_call|function_calls|parameter)(?:$|[\s=>_/])"
    r"|<tool(?:$|_)"
    r"|</tool(?:$|[_>\s])"
    r"|<function\s+(?:$|name\s*=)"
    r"|</function(?:$|[\s>])"
    r"|<parameter(?:$|[\s=>])"
    r"|</parameter(?:$|[\s>])"
    r")"
)
_PROVIDER_ERROR_DETAIL_HEAD_CHARS = 450
_PROVIDER_ERROR_DETAIL_TAIL_CHARS = 300
_PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER = "... [truncated provider error body] ..."
_NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
_NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"
_STREAMING_TOOL_ARGUMENT_PREVIEW_MAX_CHARS = 8_192



from travis.ai.providers.sse_common import _StartEventState, _iter_sse_data
from travis.ai.providers.streaming_json import (
    _parse_complete_tool_arguments, _parse_streaming_json, _parse_streaming_json_preview,
    _strip_leaked_tool_xml,
)

def _responses_tool_call_id(call_id: str, item_id: str | None) -> str:
    return f"{call_id}|{item_id}" if item_id else call_id


def _map_responses_status(status: str | None) -> tuple[str, str | None]:
    if status in (None, "completed", "in_progress", "queued"):
        return "stop", None
    if status == "incomplete":
        return "length", None
    if status in ("failed", "cancelled"):
        return "error", f"Provider response status: {status}"
    return "error", f"Provider response status: {status}"


def _merge_responses_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not isinstance(raw, dict):
        return usage
    details = raw.get("input_tokens_details")
    cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    output_details = raw.get("output_tokens_details")
    reasoning = int(output_details.get("reasoning_tokens") or 0) if isinstance(output_details, dict) else 0
    input_tokens = int(raw.get("input_tokens") or 0)
    usage.input = max(0, input_tokens - cached) or usage.input
    usage.output = int(raw.get("output_tokens") or 0) or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    if hasattr(usage, "cache_read"):
        usage.cache_read = cached or getattr(usage, "cache_read")
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
    completed = False

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
                response = event.get("response")
                if isinstance(response, dict) and isinstance(response.get("id"), str):
                    message.response_id = response["id"]
                continue
            if event_type == "response.output_item.added":
                item = event.get("item")
                if isinstance(item, dict) and isinstance(event.get("output_index"), int):
                    yield from create_slot(event["output_index"], item)
                continue
            if event_type in ("response.output_text.delta", "response.refusal.delta"):
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "text" or not isinstance(message.content[content_index], TextContent):
                    continue
                delta = event.get("delta")
                if not isinstance(delta, str) or not delta:
                    continue
                block = message.content[content_index]
                block.text += delta
                yield TextDeltaEvent(content_index=content_index, delta=delta, partial=message)
                continue
            if event_type in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                if not include_reasoning:
                    continue
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "thinking" or not isinstance(message.content[content_index], ThinkingContent):
                    continue
                delta = event.get("delta")
                if not isinstance(delta, str) or not delta:
                    continue
                block = message.content[content_index]
                block.thinking += delta
                yield ThinkingDeltaEvent(content_index=content_index, delta=delta, partial=message)
                continue
            if event_type == "response.function_call_arguments.delta":
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "toolCall" or not isinstance(message.content[content_index], ToolCall):
                    continue
                delta = event.get("delta")
                if not isinstance(delta, str):
                    continue
                tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + delta
                arguments_preview = _parse_streaming_json_preview(
                    tool_arg_bufs[content_index],
                    tool_arg_previews.get(content_index),
                )
                tool_arg_previews[content_index] = arguments_preview
                message.content[content_index].arguments = arguments_preview
                yield ToolcallDeltaEvent(content_index=content_index, delta=delta, partial=message)
                continue
            if event_type == "response.function_call_arguments.done":
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "toolCall" or not isinstance(message.content[content_index], ToolCall):
                    continue
                arguments = event.get("arguments")
                if isinstance(arguments, str):
                    tool_arg_bufs[content_index] = arguments
                    message.content[content_index].arguments = _parse_streaming_json(arguments)
                    tool_arg_previews.pop(content_index, None)
                continue
            if event_type == "response.output_item.done":
                item = event.get("item")
                output_index = event.get("output_index")
                if not isinstance(item, dict) or not isinstance(output_index, int):
                    continue
                pending_events.clear()
                slot = get_slot(output_index, item)
                while pending_events:
                    yield pending_events.pop(0)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind == "text" and isinstance(message.content[content_index], TextContent):
                    parts = item.get("content")
                    if isinstance(parts, list):
                        text = "".join(
                            str(part.get("text") or part.get("refusal") or "")
                            for part in parts
                            if isinstance(part, dict)
                        )
                        if text:
                            message.content[content_index].text = _strip_leaked_tool_xml(text)
                    yield TextEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].text,
                        partial=message,
                    )
                elif kind == "thinking" and isinstance(message.content[content_index], ThinkingContent):
                    summary = item.get("summary")
                    if isinstance(summary, list):
                        text = "\n\n".join(
                            str(part.get("text") or "")
                            for part in summary
                            if isinstance(part, dict) and part.get("text")
                        )
                        if text:
                            message.content[content_index].thinking = text
                    message.content[content_index].thinking_signature = json.dumps(item)
                    yield ThinkingEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].thinking,
                        partial=message,
                    )
                elif kind == "toolCall" and isinstance(message.content[content_index], ToolCall):
                    raw_arguments = item.get("arguments")
                    if isinstance(raw_arguments, str):
                        tool_arg_bufs[content_index] = raw_arguments
                    message.content[content_index].arguments = _parse_complete_tool_arguments(
                        tool_arg_bufs.get(content_index, "")
                    ) or {}
                    yield ToolcallEndEvent(
                        content_index=content_index,
                        tool_call=message.content[content_index],
                        partial=message,
                    )
                output_slots.pop(output_index, None)
                continue
            if event_type in ("response.completed", "response.incomplete"):
                response = event.get("response")
                if isinstance(response, dict):
                    completed = True
                    if isinstance(response.get("id"), str):
                        message.response_id = response["id"]
                    usage = _merge_responses_usage(usage, response.get("usage"))
                    reason, error_message = _map_responses_status(response.get("status"))
                    if reason == "stop" and any(isinstance(block, ToolCall) for block in message.content):
                        reason = "toolUse"
                    message.usage = usage
                    message.stop_reason = reason
                    if reason == "error":
                        message.error_message = error_message
                        yield ErrorEvent(reason="error", error=message)
                    else:
                        yield DoneEvent(reason=reason, message=message)
                    return
            if event_type == "response.failed":
                message.stop_reason = "error"
                response = event.get("response")
                error = response.get("error") if isinstance(response, dict) else None
                if isinstance(error, dict):
                    message.error_message = str(error.get("message") or error.get("code") or "Provider response failed")
                else:
                    message.error_message = "Provider response failed"
                yield ErrorEvent(reason="error", error=message)
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
