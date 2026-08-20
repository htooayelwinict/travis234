"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from travis.ai.providers._shared import blank_assistant_message as _blank
from travis.ai.types import (
    AssistantMessage,
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
    Tool,
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
)

_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")

from travis.ai.providers.anthropic_stream import _parse_anthropic_messages_sse_chunks
from travis.ai.providers.google_stream import _parse_google_sse_chunks
from travis.ai.providers.mistral_stream import _decode_mistral_stream
from travis.ai.providers.responses_stream import _parse_codex_responses_sse_chunks
from travis.ai.providers.sse_common import _StartEventState, _iter_sse_data, _map_stop_reason
from travis.ai.providers.streaming_json import (
    _parse_streaming_json,
    _parse_streaming_json_preview,
)


@dataclass
class _ChatStreamState:
    text_index: int | None
    text_buf: str
    thinking_index: int | None
    tool_call_blocks_by_index: dict[int, ToolCall]
    tool_call_blocks_by_id: dict[str, ToolCall]
    pending_reasoning_details_by_tool_call_id: dict[str, str]
    tool_arg_bufs: dict[int, str]
    tool_arg_previews: dict[int, dict[str, object]]
    content_index_of: Callable[[TextContent | ThinkingContent | ToolCall], int]
    ensure_start: Callable[[], StartEvent | None]
    usage: Usage
    finish_reason: str | None = None

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
    anthropic_oauth: bool = False,
) -> Iterator:
    """Pure transform: decoded SSE lines -> AssistantMessageEvent stream."""
    if api_mode in {
        "openai_responses",
        "azure_openai_responses",
        "openai_codex_responses",
    }:
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
            tools=tools,
            is_oauth=anthropic_oauth,
        )
        return
    if api_mode in {"google_generative_ai", "google_vertex"}:
        yield from _parse_google_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=data_idle_timeout_seconds,
            clock=clock,
            include_reasoning=include_reasoning,
        )
        return
    if api_mode == "mistral_conversations":
        yield from _decode_mistral_stream(
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
    pending_reasoning_details_by_tool_call_id: dict[str, str] = {}
    tool_arg_bufs: dict[int, str] = {}
    tool_arg_previews: dict[int, dict[str, object]] = {}
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
        yield from end_content_events()
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

    stream_state = _ChatStreamState(
        text_index=text_index,
        text_buf=text_buf,
        thinking_index=thinking_index,
        tool_call_blocks_by_index=tool_call_blocks_by_index,
        tool_call_blocks_by_id=tool_call_blocks_by_id,
        pending_reasoning_details_by_tool_call_id=pending_reasoning_details_by_tool_call_id,
        tool_arg_bufs=tool_arg_bufs,
        tool_arg_previews=tool_arg_previews,
        content_index_of=content_index_of,
        ensure_start=start_state.ensure,
        usage=usage,
    )
    start = start_state.ensure()
    if start:
        yield start
    try:
        payloads = _iter_sse_data(lines, data_idle_timeout_seconds=data_idle_timeout_seconds, clock=clock)
        for payload in payloads:
            yield from _parse_sse_payload(
                payload,
                model,
                message,
                stream_state,
                include_reasoning=include_reasoning,
            )
            usage = stream_state.usage
            if stream_state.finish_reason:
                finish_reason = stream_state.finish_reason
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

def _record_chat_response_metadata(chunk, model: Model, message: AssistantMessage) -> None:
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


def _reasoning_field(delta) -> str | None:
    for field in _REASONING_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value:
            return field
    return None


def _emit_reasoning_delta(
    delta,
    field: str,
    model: Model,
    message: AssistantMessage,
    state: _ChatStreamState,
) -> Iterator[object]:
    reasoning = delta[field]
    thinking_signature = (
        "reasoning_content"
        if model.provider == "opencode-go" and field == "reasoning"
        else field
    )
    start = state.ensure_start()
    if start:
        yield start
    if state.thinking_index is None:
        state.thinking_index = len(message.content)
        message.content.append(
            ThinkingContent(thinking="", thinking_signature=thinking_signature)
        )
        yield ThinkingStartEvent(content_index=state.thinking_index, partial=message)
    block = message.content[state.thinking_index]
    if not isinstance(block, ThinkingContent):
        return
    block.thinking += reasoning
    yield ThinkingDeltaEvent(
        content_index=state.thinking_index,
        delta=reasoning,
        partial=message,
    )


def _emit_text_delta(
    content_piece,
    message: AssistantMessage,
    state: _ChatStreamState,
) -> Iterator[object]:
    start = state.ensure_start()
    if start:
        yield start
    if state.text_index is None:
        state.text_index = len(message.content)
        message.content.append(TextContent(text=""))
        yield TextStartEvent(content_index=state.text_index, partial=message)
    state.text_buf += content_piece
    block = message.content[state.text_index]
    if not isinstance(block, TextContent):
        return
    block.text = state.text_buf
    yield TextDeltaEvent(
        content_index=state.text_index,
        delta=content_piece,
        partial=message,
    )


def _tool_call_fragments(tool_call_delta) -> tuple[int | None, str, str, str]:
    stream_index = tool_call_delta.get("index")
    if not isinstance(stream_index, int):
        stream_index = None
    tool_call_id = tool_call_delta.get("id") or ""
    function = tool_call_delta.get("function") or {}
    if not isinstance(function, dict):
        function = {}
    name = function.get("name") if isinstance(function.get("name"), str) else ""
    arguments = (
        function.get("arguments")
        if isinstance(function.get("arguments"), str)
        else ""
    )
    return stream_index, tool_call_id, name, arguments


def _existing_tool_call(
    state: _ChatStreamState,
    stream_index: int | None,
    tool_call_id: str,
) -> ToolCall | None:
    tool_call = (
        state.tool_call_blocks_by_index.get(stream_index)
        if stream_index is not None
        else None
    )
    if tool_call is None and tool_call_id:
        return state.tool_call_blocks_by_id.get(tool_call_id)
    return tool_call


def _attach_pending_reasoning(
    state: _ChatStreamState,
    tool_call: ToolCall,
    tool_call_id: str,
) -> None:
    pending_detail = state.pending_reasoning_details_by_tool_call_id.pop(
        tool_call_id,
        None,
    )
    if pending_detail:
        tool_call.thought_signature = pending_detail


def _register_tool_call_identity(
    state: _ChatStreamState,
    tool_call: ToolCall,
    stream_index: int | None,
    tool_call_id: str,
) -> None:
    if stream_index is not None:
        state.tool_call_blocks_by_index[stream_index] = tool_call
    if tool_call_id:
        state.tool_call_blocks_by_id[tool_call_id] = tool_call
    if tool_call_id and not tool_call.id:
        tool_call.id = tool_call_id
        _attach_pending_reasoning(state, tool_call, tool_call_id)


def _emit_tool_call_delta(
    tool_call_delta,
    message: AssistantMessage,
    state: _ChatStreamState,
) -> Iterator[object]:
    stream_index, tool_call_id, name_fragment, arg_fragment = _tool_call_fragments(
        tool_call_delta
    )
    tool_call = _existing_tool_call(state, stream_index, tool_call_id)
    start = state.ensure_start()
    if start:
        yield start
    if tool_call is None:
        arguments_preview: dict[str, object] = {}
        tool_call = ToolCall(
            id=tool_call_id,
            name=name_fragment,
            arguments=arguments_preview,
        )
        content_index = len(message.content)
        message.content.append(tool_call)
        state.tool_arg_bufs[content_index] = ""
        state.tool_arg_previews[content_index] = arguments_preview
        _register_tool_call_identity(state, tool_call, stream_index, tool_call.id)
        if tool_call.id:
            _attach_pending_reasoning(state, tool_call, tool_call.id)
        yield ToolcallStartEvent(content_index=content_index, partial=message)
    else:
        content_index = state.content_index_of(tool_call)
        if content_index < 0:
            return
        _register_tool_call_identity(state, tool_call, stream_index, tool_call_id)
    if name_fragment and not tool_call.name:
        tool_call.name = name_fragment
    if arg_fragment:
        state.tool_arg_bufs[content_index] = (
            state.tool_arg_bufs.get(content_index, "") + arg_fragment
        )
        arguments_preview = _parse_streaming_json_preview(
            state.tool_arg_bufs[content_index],
            state.tool_arg_previews.get(content_index),
        )
        state.tool_arg_previews[content_index] = arguments_preview
        tool_call.arguments = arguments_preview
    yield ToolcallDeltaEvent(
        content_index=content_index,
        delta=arg_fragment,
        partial=message,
    )


def _apply_reasoning_details(delta, state: _ChatStreamState) -> None:
    reasoning_details = delta.get("reasoning_details")
    if not isinstance(reasoning_details, list):
        return
    for detail in reasoning_details:
        if not isinstance(detail, dict):
            continue
        detail_id = detail.get("id")
        if (
            detail.get("type") != "reasoning.encrypted"
            or not isinstance(detail_id, str)
            or not detail_id
            or not isinstance(detail.get("data"), str)
            or not detail["data"]
        ):
            continue
        serialized = json.dumps(detail, separators=(",", ":"))
        matching = state.tool_call_blocks_by_id.get(detail_id)
        if matching is not None:
            matching.thought_signature = serialized
        else:
            state.pending_reasoning_details_by_tool_call_id[detail_id] = serialized


def _parse_sse_payload(
    payload: str,
    model: Model,
    message: AssistantMessage,
    state: _ChatStreamState,
    *,
    include_reasoning: bool = True,
) -> Iterator[object]:
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return
    _record_chat_response_metadata(chunk, model, message)
    state.usage = _merge_usage(state.usage, chunk.get("usage"))
    choices = chunk.get("choices") or []
    if not choices:
        return
    choice = choices[0]
    if not chunk.get("usage"):
        state.usage = _merge_usage(state.usage, choice.get("usage"))
    delta = choice.get("delta") or {}

    reasoning_field = _reasoning_field(delta)
    if reasoning_field and include_reasoning:
        yield from _emit_reasoning_delta(
            delta,
            reasoning_field,
            model,
            message,
            state,
        )
    content_piece = delta.get("content")
    if content_piece:
        yield from _emit_text_delta(content_piece, message, state)
    for tool_call_delta in delta.get("tool_calls") or []:
        yield from _emit_tool_call_delta(tool_call_delta, message, state)
    _apply_reasoning_details(delta, state)
    if choice.get("finish_reason"):
        state.finish_reason = choice["finish_reason"]


def _merge_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not raw:
        return usage
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    prompt_details = raw.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    cache_read = int(prompt_details.get("cached_tokens") or raw.get("prompt_cache_hit_tokens") or 0)
    cache_write = int(prompt_details.get("cache_write_tokens") or 0)
    completion_details = raw.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    usage.input = max(0, prompt - cache_read - cache_write) if prompt else usage.input
    usage.output = completion or usage.output
    usage.cache_read = cache_read or usage.cache_read
    usage.cache_write = cache_write or usage.cache_write
    usage.reasoning = int(completion_details.get("reasoning_tokens") or 0) or usage.reasoning
    usage.total_tokens = (
        usage.input + usage.output + usage.cache_read + usage.cache_write
        if prompt or completion
        else usage.total_tokens
    )
    return usage

decode_chat_stream = parse_sse_chunks
