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



def _has_tool_history(messages: list[Message]) -> bool:
    for message in messages:
        if getattr(message, "role", None) == "toolResult":
            return True
        if getattr(message, "role", None) == "assistant":
            for block in getattr(message, "content", []) or []:
                if isinstance(block, ToolCall):
                    return True
    return False


def convert_messages(context: Context, model: Model | None = None) -> "tuple[list[dict], list[dict] | None]":
    messages: list[dict] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": _sanitize_surrogates(context.system_prompt)})
    context_messages = _transform_messages(context.messages, model) if model is not None else context.messages
    index = 0
    while index < len(context_messages):
        message = context_messages[index]
        if message.role == "toolResult":
            tool_messages, next_index = _convert_tool_result_group(
                context_messages,
                index,
                model,
            )
            messages.extend(tool_messages)
            index = next_index
            continue
        converted_message = _convert_message(message, model)
        if converted_message is not None:
            messages.append(converted_message)
        index += 1
    tools = None
    if context.tools:
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in context.tools
        ]
    elif _has_tool_history(context_messages):
        tools = []
    return messages, tools


def _transform_messages(messages: list[Message], model: Model) -> list[Message]:
    tool_call_id_map: dict[str, str] = {}
    image_aware_messages = _downgrade_unsupported_images(messages, model)
    transformed: list[Message] = []

    for message in image_aware_messages:
        if message.role == "user":
            transformed.append(message)
            continue

        if message.role == "toolResult":
            normalized_id = tool_call_id_map.get(message.tool_call_id)
            transformed.append(
                replace(message, tool_call_id=normalized_id)
                if normalized_id and normalized_id != message.tool_call_id
                else message
            )
            continue

        is_same_model = message.provider == model.provider and message.api == model.api and message.model == model.id
        transformed_content: list[TextContent | ThinkingContent | ImageContent | ToolCall] = []
        for block in message.content:
            if isinstance(block, ThinkingContent):
                if block.redacted:
                    if is_same_model:
                        transformed_content.append(block)
                    continue
                if is_same_model and block.thinking_signature:
                    transformed_content.append(block)
                    continue
                if not block.thinking or not block.thinking.strip():
                    continue
                transformed_content.append(block if is_same_model else TextContent(text=block.thinking))
                continue

            if isinstance(block, TextContent):
                transformed_content.append(block if is_same_model else TextContent(text=block.text))
                continue

            if isinstance(block, ToolCall):
                transformed_tool_call = block
                if not is_same_model and block.thought_signature:
                    transformed_tool_call = replace(block, thought_signature=None)
                if not is_same_model:
                    normalized_id = _normalize_tool_call_id(block.id, model)
                    if normalized_id != block.id:
                        tool_call_id_map[block.id] = normalized_id
                        transformed_tool_call = replace(transformed_tool_call, id=normalized_id)
                transformed_content.append(transformed_tool_call)
                continue

            transformed_content.append(block)

        transformed.append(replace(message, content=transformed_content))

    result: list[Message] = []
    pending_tool_calls: list[ToolCall] = []
    existing_tool_result_ids: set[str] = set()

    def insert_synthetic_tool_results() -> None:
        nonlocal pending_tool_calls, existing_tool_result_ids
        if not pending_tool_calls:
            return
        for tool_call in pending_tool_calls:
            if tool_call.id not in existing_tool_result_ids:
                result.append(
                    ToolResultMessage(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        content=[TextContent(text="No result provided")],
                        is_error=True,
                        timestamp=now_ms(),
                    )
                )
        pending_tool_calls = []
        existing_tool_result_ids = set()

    for message in transformed:
        if message.role == "assistant":
            insert_synthetic_tool_results()
            if message.stop_reason in ("error", "aborted"):
                continue
            tool_calls = [block for block in message.content if isinstance(block, ToolCall)]
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()
            result.append(message)
        elif message.role == "toolResult":
            existing_tool_result_ids.add(message.tool_call_id)
            result.append(message)
        elif message.role == "user":
            insert_synthetic_tool_results()
            result.append(message)
        else:
            result.append(message)

    insert_synthetic_tool_results()
    return result


def _downgrade_unsupported_images(messages: list[Message], model: Model) -> list[Message]:
    if "image" in model.input:
        return messages

    downgraded: list[Message] = []
    for message in messages:
        if message.role == "user" and isinstance(message.content, list):
            downgraded.append(
                replace(
                    message,
                    content=_replace_images_with_placeholder(message.content, _NON_VISION_USER_IMAGE_PLACEHOLDER),
                )
            )
        elif message.role == "toolResult":
            downgraded.append(
                replace(
                    message,
                    content=_replace_images_with_placeholder(message.content, _NON_VISION_TOOL_IMAGE_PLACEHOLDER),
                )
            )
        else:
            downgraded.append(message)
    return downgraded


def _replace_images_with_placeholder(
    content: list[TextContent | ImageContent], placeholder: str
) -> list[TextContent]:
    result: list[TextContent] = []
    previous_was_placeholder = False
    for block in content:
        if isinstance(block, ImageContent):
            if not previous_was_placeholder:
                result.append(TextContent(text=placeholder))
            previous_was_placeholder = True
            continue
        result.append(block)
        previous_was_placeholder = block.text == placeholder
    return result


def _normalize_tool_call_id(tool_call_id: str, model: Model) -> str:
    if "|" in tool_call_id:
        call_id = tool_call_id.split("|", 1)[0]
        return re.sub(r"[^a-zA-Z0-9_-]", "_", call_id)[:40]
    if model.provider == "openai":
        return tool_call_id[:40]
    return tool_call_id


def _repair_tool_call_name_fragment(name: str) -> str:
    if not name:
        return ""
    separator_indexes = [index for sep in ('"', "'", "<", ">") if (index := name.find(sep)) >= 0]
    if not separator_indexes:
        return name
    first = min(separator_indexes)
    return name[:first] if first > 0 else ""


def _coerce_tool_call_arguments_for_replay(arguments: Any, tool_name: str = "?") -> tuple[dict[str, Any], bool]:
    if arguments is None:
        return {}, False
    if isinstance(arguments, dict):
        return arguments, False
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}, False
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            repaired = repair_tool_call_arguments(arguments, tool_name)
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                return {}, True
            return (parsed, True) if isinstance(parsed, dict) else ({}, True)
        return (parsed, False) if isinstance(parsed, dict) else ({}, True)
    return {}, True


def _convert_message(
    message: Message,
    model: Model | None = None,
) -> dict | None:
    if message.role == "user":
        content = _sanitize_surrogates(message.content) if isinstance(message.content, str) else _convert_user_content_parts(message.content)
        return {"role": "user", "content": content}
    if message.role == "toolResult":
        return _convert_single_tool_result(message)
    # assistant
    text_parts = [_sanitize_surrogates(b.text) for b in message.content if isinstance(b, TextContent)]
    thinking_parts = [b for b in message.content if isinstance(b, ThinkingContent) and b.thinking.strip()]
    responses_reasoning_items: list[dict[str, Any]] = []
    textual_thinking_parts: list[ThinkingContent] = []
    for block in thinking_parts:
        signature = block.thinking_signature
        if signature:
            try:
                reasoning_item = json.loads(signature)
            except (json.JSONDecodeError, TypeError, ValueError):
                reasoning_item = None
            if isinstance(reasoning_item, dict) and reasoning_item.get("type") == "reasoning":
                responses_reasoning_items.append(reasoning_item)
                continue
        textual_thinking_parts.append(block)
    tool_calls = []
    for block in message.content:
        if not isinstance(block, ToolCall):
            continue
        name = _repair_tool_call_name_fragment(block.name)
        arguments, _repaired_corruption = _coerce_tool_call_arguments_for_replay(block.arguments, name)
        tool_calls.append(
            {
                "id": block.id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments),
                },
            },
        )
    content_text = "".join(text_parts)
    if not content_text and not thinking_parts and not tool_calls:
        return None
    out: dict = {"role": "assistant", "content": content_text}
    if responses_reasoning_items:
        out["codex_reasoning_items"] = responses_reasoning_items
    if textual_thinking_parts:
        signature = textual_thinking_parts[0].thinking_signature
        if model is not None and model.provider == "opencode-go" and signature == "reasoning":
            signature = "reasoning_content"
        if signature:
            out[signature] = "\n".join(
                _sanitize_surrogates(block.thinking) for block in textual_thinking_parts
            )
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _convert_single_tool_result(
    message: ToolResultMessage,
) -> dict:
    content = _text_of(message.content)
    if not content and any(isinstance(block, ImageContent) for block in message.content):
        content = "(see attached image)"
    return {
        "role": "tool",
        "tool_call_id": message.tool_call_id,
        "name": message.tool_name,
        "content": content,
    }


def _convert_tool_result_group(
    messages: list[Message],
    start_index: int,
    model: Model | None,
) -> tuple[list[dict], int]:
    converted: list[dict] = []
    image_parts: list[dict] = []
    index = start_index
    while index < len(messages) and messages[index].role == "toolResult":
        message = messages[index]
        converted_message = _convert_single_tool_result(message)
        converted.append(converted_message)
        if model is not None and "image" in model.input:
            for block in message.content:
                if isinstance(block, ImageContent):
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                    })
        index += 1
    if image_parts:
        converted.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                *image_parts,
            ],
        })
    return converted, index


def _convert_user_content_parts(content: list[TextContent | ImageContent]) -> list[dict]:
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": _sanitize_surrogates(block.text)})
        elif isinstance(block, ImageContent):
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
            })
    return parts


def _text_of(content) -> str:
    if isinstance(content, str):
        return _sanitize_surrogates(content)
    return "".join(_sanitize_surrogates(b.text) for b in content if isinstance(b, TextContent))


def _sanitize_surrogates(text: str) -> str:
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF)

translate_messages = convert_messages
