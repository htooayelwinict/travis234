"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import time
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
                usage_ref := {"usage": usage},
                state := {
                    "started": start_state.started,
                    "text_index": text_index,
                    "text_buf": text_buf,
                    "thinking_index": thinking_index,
                    "tool_call_blocks_by_index": tool_call_blocks_by_index,
                    "tool_call_blocks_by_id": tool_call_blocks_by_id,
                    "pending_reasoning_details_by_tool_call_id": pending_reasoning_details_by_tool_call_id,
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
    pending_reasoning_details_by_tool_call_id = state.setdefault(
        "pending_reasoning_details_by_tool_call_id",
        {},
    )
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
        name_fragment = fn.get("name") if isinstance(fn.get("name"), str) else ""
        arg_fragment = fn.get("arguments") if isinstance(fn.get("arguments"), str) else ""
        tool_call = tool_call_blocks_by_index.get(stream_index) if stream_index is not None else None
        if tool_call is None and tool_call_id:
            tool_call = tool_call_blocks_by_id.get(tool_call_id)
        if tool_call is None:
            start = ensure_start()
            if start:
                state["started"] = True
                yield start
            arguments_preview: dict = {}
            tool_call = ToolCall(
                id=tool_call_id,
                name=name_fragment,
                arguments=arguments_preview,
            )
            content_index = len(message.content)
            message.content.append(tool_call)
            tool_arg_bufs[content_index] = ""
            tool_arg_previews[content_index] = arguments_preview
            if stream_index is not None:
                tool_call_blocks_by_index[stream_index] = tool_call
            if tool_call.id:
                tool_call_blocks_by_id[tool_call.id] = tool_call
                pending_detail = pending_reasoning_details_by_tool_call_id.pop(tool_call.id, None)
                if pending_detail:
                    tool_call.thought_signature = pending_detail
            yield ToolcallStartEvent(content_index=content_index, partial=message)
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
            pending_detail = pending_reasoning_details_by_tool_call_id.pop(tool_call_id, None)
            if pending_detail:
                tool_call.thought_signature = pending_detail
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

    reasoning_details = delta.get("reasoning_details")
    if isinstance(reasoning_details, list):
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
            matching = tool_call_blocks_by_id.get(detail_id)
            if matching is not None:
                matching.thought_signature = serialized
            else:
                pending_reasoning_details_by_tool_call_id[detail_id] = serialized

    usage_ref["usage"] = usage
    if choice.get("finish_reason"):
        state["finish_reason"] = choice["finish_reason"]
    return


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
