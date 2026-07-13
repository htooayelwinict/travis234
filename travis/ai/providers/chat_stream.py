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



from travis.ai.providers.anthropic_stream import _parse_anthropic_messages_sse_chunks
from travis.ai.providers.message_translation import _repair_tool_call_name_fragment
from travis.ai.providers.responses_stream import _parse_codex_responses_sse_chunks
from travis.ai.providers.sse_common import _StartEventState, _iter_sse_data, _map_stop_reason
from travis.ai.providers.streaming_json import (
    _malformed_finished_mutating_tool_call_names,
    _malformed_finished_tool_call_names_against_active_schema, _parse_streaming_json,
    _parse_streaming_json_preview,
)

def parse_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
    api_mode: str = "chat_completions",
    tools: Iterable[Tool] | None = None,
    wait_for_usage_after_finish: bool = False,
) -> Iterator:
    """Pure transform: decoded SSE lines -> AssistantMessageEvent stream."""
    if api_mode == "codex_responses":
        yield from _parse_codex_responses_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=data_idle_timeout_seconds,
            clock=clock,
            include_reasoning=include_reasoning,
        )
        return
    if api_mode == "anthropic_messages":
        yield from _parse_anthropic_messages_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=data_idle_timeout_seconds,
            clock=clock,
            include_reasoning=include_reasoning,
        )
        return

    message = _blank(model)
    start_state = _StartEventState(message)
    text_index: int | None = None
    text_buf = ""
    thinking_index: int | None = None
    tool_call_blocks_by_index: dict[int, ToolCall] = {}
    tool_call_blocks_by_id: dict[str, ToolCall] = {}
    pending_tool_call_parts: dict[tuple[str, int | str], dict[str, str]] = {}
    tool_arg_bufs: dict[int, str] = {}
    tool_arg_previews: dict[int, dict] = {}
    finish_reason = "stop"
    has_finish_reason = False
    usage = empty_usage()

    def content_index_of(block: TextContent | ThinkingContent | ToolCall) -> int:
        for index, candidate in enumerate(message.content):
            if candidate is block:
                return index
        return -1

    def end_content_events() -> Iterator:
        for content_index, block in enumerate(message.content):
            if isinstance(block, TextContent):
                yield TextEndEvent(content_index=content_index, content=block.text, partial=message)
            elif isinstance(block, ThinkingContent):
                yield ThinkingEndEvent(content_index=content_index, content=block.thinking, partial=message)
            elif isinstance(block, ToolCall):
                block.arguments = _parse_streaming_json(tool_arg_bufs.get(content_index, ""))
                yield ToolcallEndEvent(content_index=content_index, tool_call=block, partial=message)

        if not start_state.started:
            yield StartEvent(partial=message)
        message.usage = usage

    def final_events() -> Iterator:
        malformed_mutating_tool_names: list[str] = []
        if has_finish_reason and finish_reason == "tool_calls":
            malformed_mutating_tool_names = (
                _malformed_finished_tool_call_names_against_active_schema(message.content, tool_arg_bufs, tools)
                if tools is not None
                else _malformed_finished_mutating_tool_call_names(message.content, tool_arg_bufs)
            )
        streamed_tool_names = [
            block.name or "?"
            for block in message.content
            if isinstance(block, ToolCall)
        ]
        yield from end_content_events()
        if malformed_mutating_tool_names:
            message.stop_reason = "length"
            diagnostics = list(message.diagnostics or [])
            diagnostics.append(
                {
                    "code": MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE,
                    "tool_names": malformed_mutating_tool_names,
                    "finish_reason": finish_reason if has_finish_reason else None,
                }
            )
            message.diagnostics = diagnostics
            yield DoneEvent(reason="length", message=message)
            return
        if streamed_tool_names and not has_finish_reason:
            message.stop_reason = "length"
            diagnostics = list(message.diagnostics or [])
            diagnostics.append(
                {
                    "code": "partial_stream_tool_calls",
                    "tool_names": streamed_tool_names,
                    "finish_reason": None,
                }
            )
            message.diagnostics = diagnostics
            yield DoneEvent(reason="length", message=message)
            return
        if not has_finish_reason:
            message.stop_reason = "error"
            message.error_message = "Stream ended without finish_reason"
            yield ErrorEvent(reason="error", error=message)
            return
        reason, error_message = _map_stop_reason(finish_reason)
        if reason == "toolUse" and not any(isinstance(block, ToolCall) for block in message.content):
            reason = "stop"
            error_message = None
        message.stop_reason = reason
        if reason == "error":
            message.error_message = error_message
            yield ErrorEvent(reason="error", error=message)
            return
        yield DoneEvent(reason=reason, message=message)

    try:
        payloads = _iter_sse_data(lines, data_idle_timeout_seconds=data_idle_timeout_seconds, clock=clock)
        for payload in payloads:
            yield from _parse_sse_payload(
                payload,
                model,
                message,
                usage_ref := {"usage": usage},
                state := {
                    "started": start_state.started,
                    "text_index": text_index,
                    "text_buf": text_buf,
                    "thinking_index": thinking_index,
                    "tool_call_blocks_by_index": tool_call_blocks_by_index,
                    "tool_call_blocks_by_id": tool_call_blocks_by_id,
                    "pending_tool_call_parts": pending_tool_call_parts,
                    "tool_arg_bufs": tool_arg_bufs,
                    "tool_arg_previews": tool_arg_previews,
                    "content_index_of": content_index_of,
                    "ensure_start": start_state.ensure,
                },
                include_reasoning=include_reasoning,
            )
            usage = usage_ref["usage"]
            start_state.started = state["started"]
            text_index = state["text_index"]
            text_buf = state["text_buf"]
            thinking_index = state["thinking_index"]
            if state.get("finish_reason"):
                finish_reason = state["finish_reason"]
                has_finish_reason = True
                if not wait_for_usage_after_finish:
                    yield from final_events()
                    return
    except TimeoutError as error:
        yield from end_content_events()
        message.stop_reason = "error"
        message.error_message = str(error)
        yield ErrorEvent(reason="error", error=message)
        return

    yield from final_events()

def _parse_sse_payload(
    payload: str,
    model: Model,
    message: AssistantMessage,
    usage_ref: dict,
    state: dict,
    *,
    include_reasoning: bool = True,
) -> Iterator:
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return
    usage = usage_ref["usage"]
    tool_arg_bufs = state["tool_arg_bufs"]
    tool_arg_previews = state["tool_arg_previews"]
    tool_call_blocks_by_index = state["tool_call_blocks_by_index"]
    tool_call_blocks_by_id = state["tool_call_blocks_by_id"]
    pending_tool_call_parts = state.setdefault("pending_tool_call_parts", {})
    content_index_of = state["content_index_of"]
    ensure_start = state["ensure_start"]
    text_index = state["text_index"]
    text_buf = state["text_buf"]
    thinking_index = state["thinking_index"]

    if not message.response_id and isinstance(chunk.get("id"), str) and chunk["id"]:
        message.response_id = chunk["id"]
    chunk_model = chunk.get("model")
    if (
        not message.response_model
        and isinstance(chunk_model, str)
        and chunk_model
        and chunk_model != model.id
    ):
        message.response_model = chunk_model
    usage = _merge_usage(usage, chunk.get("usage"))
    choices = chunk.get("choices") or []
    if not choices:
        usage_ref["usage"] = usage
        return
    choice = choices[0]
    if not chunk.get("usage"):
        usage = _merge_usage(usage, choice.get("usage"))
    delta = choice.get("delta") or {}

    found_reasoning_field = None
    for field in _REASONING_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value:
            found_reasoning_field = field
            break

    if found_reasoning_field and include_reasoning:
        reasoning = delta[found_reasoning_field]
        thinking_signature = (
            "reasoning_content"
            if model.provider == "opencode-go" and found_reasoning_field == "reasoning"
            else found_reasoning_field
        )
        start = ensure_start()
        if start:
            state["started"] = True
            yield start
        if thinking_index is None:
            thinking_index = len(message.content)
            state["thinking_index"] = thinking_index
            message.content.append(ThinkingContent(thinking="", thinking_signature=thinking_signature))
            yield ThinkingStartEvent(content_index=thinking_index, partial=message)
        message.content[thinking_index].thinking += reasoning
        yield ThinkingDeltaEvent(content_index=thinking_index, delta=reasoning, partial=message)

    content_piece = delta.get("content")
    if content_piece:
        start = ensure_start()
        if start:
            state["started"] = True
            yield start
        if text_index is None:
            text_index = len(message.content)
            state["text_index"] = text_index
            message.content.append(TextContent(text=""))
            yield TextStartEvent(content_index=text_index, partial=message)
        text_buf += content_piece
        state["text_buf"] = text_buf
        message.content[text_index].text = text_buf
        yield TextDeltaEvent(content_index=text_index, delta=content_piece, partial=message)

    for tc in delta.get("tool_calls") or []:
        stream_index = tc.get("index")
        if not isinstance(stream_index, int):
            stream_index = None
        tool_call_id = tc.get("id") or ""
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        name_fragment = _repair_tool_call_name_fragment(fn.get("name")) if isinstance(fn.get("name"), str) else ""
        arg_fragment = fn.get("arguments") if isinstance(fn.get("arguments"), str) else ""
        tool_call = tool_call_blocks_by_index.get(stream_index) if stream_index is not None else None
        if tool_call is None and tool_call_id:
            tool_call = tool_call_blocks_by_id.get(tool_call_id)
        if tool_call is None:
            pending_key = (
                ("index", stream_index)
                if stream_index is not None
                else (("id", tool_call_id) if tool_call_id else None)
            )
            if pending_key is None:
                continue
            pending = pending_tool_call_parts.setdefault(
                pending_key,
                {"id": "", "name": "", "arguments": ""},
            )
            if tool_call_id:
                pending["id"] = tool_call_id
            if name_fragment:
                pending["name"] = name_fragment
            if arg_fragment:
                pending["arguments"] += arg_fragment
            if not pending["id"] or not pending["name"]:
                continue

            start = ensure_start()
            if start:
                state["started"] = True
                yield start
            arguments_preview = _parse_streaming_json_preview(pending["arguments"])
            tool_call = ToolCall(
                id=pending["id"],
                name=pending["name"],
                arguments=arguments_preview,
            )
            content_index = len(message.content)
            message.content.append(tool_call)
            tool_arg_bufs[content_index] = pending["arguments"]
            tool_arg_previews[content_index] = arguments_preview
            if stream_index is not None:
                tool_call_blocks_by_index[stream_index] = tool_call
            if tool_call.id:
                tool_call_blocks_by_id[tool_call.id] = tool_call
            pending_tool_call_parts.pop(pending_key, None)
            yield ToolcallStartEvent(content_index=content_index, partial=message)
            if arg_fragment:
                yield ToolcallDeltaEvent(content_index=content_index, delta=arg_fragment, partial=message)
            continue
        else:
            start = ensure_start()
            if start:
                state["started"] = True
                yield start
            content_index = content_index_of(tool_call)
            if content_index < 0:
                continue
            if stream_index is not None:
                tool_call_blocks_by_index[stream_index] = tool_call
            if tool_call_id:
                tool_call_blocks_by_id[tool_call_id] = tool_call
        if tool_call_id and not tool_call.id:
            tool_call.id = tool_call_id
            tool_call_blocks_by_id[tool_call_id] = tool_call
        if name_fragment and not tool_call.name:
            tool_call.name = name_fragment
        if arg_fragment:
            tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + arg_fragment
            arguments_preview = _parse_streaming_json_preview(
                tool_arg_bufs[content_index],
                tool_arg_previews.get(content_index),
            )
            tool_arg_previews[content_index] = arguments_preview
            tool_call.arguments = arguments_preview
        yield ToolcallDeltaEvent(content_index=content_index, delta=arg_fragment, partial=message)

    usage_ref["usage"] = usage
    if choice.get("finish_reason"):
        state["finish_reason"] = choice["finish_reason"]
    return


def _merge_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not raw:
        return usage
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    usage.input = prompt or usage.input
    usage.output = completion or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    return usage

decode_chat_stream = parse_sse_chunks
