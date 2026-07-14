"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from collections.abc import Callable
from typing import Any

from travis.ai.providers.openai_compat import OpenAICompat, resolve_openai_compat
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    now_ms,
)
_NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
_NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"
def _has_tool_history(messages: list[Message]) -> bool:
    for message in messages:
        if getattr(message, "role", None) == "toolResult":
            return True
        if getattr(message, "role", None) == "assistant":
            for block in getattr(message, "content", []) or []:
                if isinstance(block, ToolCall):
                    return True
    return False


def convert_messages(
    context: Context,
    model: Model | None = None,
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None = None,
) -> "tuple[list[dict], list[dict] | None]":
    messages: list[dict] = []
    compat = resolve_openai_compat(model) if model is not None else OpenAICompat()
    if context.system_prompt:
        role = "developer" if model is not None and model.reasoning and compat.supports_developer_role else "system"
        messages.append({"role": role, "content": _sanitize_surrogates(context.system_prompt)})
    context_messages = (
        _transform_messages(context.messages, model, normalize_tool_call_id)
        if model is not None
        else context.messages
    )
    index = 0
    last_role: str | None = None
    while index < len(context_messages):
        message = context_messages[index]
        if compat.requires_assistant_after_tool_result and last_role == "toolResult" and message.role == "user":
            messages.append({"role": "assistant", "content": "I have processed the tool results."})
        if message.role == "toolResult":
            tool_messages, next_index = _convert_tool_result_group(
                context_messages,
                index,
                model,
                compat,
            )
            messages.extend(tool_messages)
            last_role = "user" if tool_messages and tool_messages[-1].get("role") == "user" else "toolResult"
            index = next_index
            continue
        converted_message = _convert_message(message, model, compat)
        if converted_message is not None:
            messages.append(converted_message)
            last_role = message.role
        index += 1
    tools = None
    if context.tools:
        tools = []
        for tool in context.tools:
            function = {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            if compat.supports_strict_mode:
                function["strict"] = False
            tools.append({"type": "function", "function": function})
    elif _has_tool_history(context_messages):
        tools = []
    return messages, tools


def _transform_messages(
    messages: list[Message],
    model: Model,
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None = None,
) -> list[Message]:
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
                    normalized_id = (
                        normalize_tool_call_id(block.id, model, message)
                        if normalize_tool_call_id is not None
                        else _normalize_tool_call_id(block.id, model)
                    )
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
                # Failed assistant messages can contain incomplete reasoning or
                # malformed tool calls. They are retained in the session record
                # but omitted from provider replay. If no completed assistant or
                # tool work followed the latest user input, omit that unanswered
                # input as well. Replaying it beside the next prompt makes some
                # providers treat the newer instruction as an injection or as
                # steering for the cancelled task.
                while result and result[-1].role == "user":
                    result.pop()
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


def _convert_message(
    message: Message,
    model: Model | None = None,
    compat: OpenAICompat | None = None,
) -> dict | None:
    compat = compat or (resolve_openai_compat(model) if model is not None else OpenAICompat())
    if message.role == "user":
        content = _sanitize_surrogates(message.content) if isinstance(message.content, str) else _convert_user_content_parts(message.content)
        return {"role": "user", "content": content}
    if message.role == "toolResult":
        return _convert_single_tool_result(message)
    # assistant
    text_parts = [
        _sanitize_surrogates(block.text)
        for block in message.content
        if isinstance(block, TextContent) and block.text.strip()
    ]
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
    reasoning_details: list[object] = []
    for block in message.content:
        if not isinstance(block, ToolCall):
            continue
        tool_calls.append(
            {
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": json.dumps(block.arguments, separators=(",", ":")),
                },
            },
        )
        if block.thought_signature:
            try:
                reasoning_detail = json.loads(block.thought_signature)
            except (json.JSONDecodeError, TypeError, ValueError):
                reasoning_detail = None
            if reasoning_detail is not None:
                reasoning_details.append(reasoning_detail)
    content_text = "".join(text_parts)
    if not content_text and not thinking_parts and not tool_calls:
        return None
    out: dict = {
        "role": "assistant",
        "content": "" if compat.requires_assistant_after_tool_result else None,
    }
    if responses_reasoning_items:
        out["codex_reasoning_items"] = responses_reasoning_items
    if textual_thinking_parts and compat.requires_thinking_as_text:
        thinking_text = "\n\n".join(_sanitize_surrogates(block.thinking) for block in textual_thinking_parts)
        thinking_content = [{"type": "text", "text": thinking_text}]
        if content_text:
            thinking_content.append({"type": "text", "text": content_text})
        out["content"] = thinking_content
    elif textual_thinking_parts:
        signature = textual_thinking_parts[0].thinking_signature
        if model is not None and model.provider == "opencode-go" and signature == "reasoning":
            signature = "reasoning_content"
        if signature:
            out[signature] = "\n".join(
                _sanitize_surrogates(block.thinking) for block in textual_thinking_parts
            )
    if tool_calls:
        out["tool_calls"] = tool_calls
    if reasoning_details:
        out["reasoning_details"] = reasoning_details
    if content_text and not compat.requires_thinking_as_text:
        out["content"] = content_text
    if (
        compat.requires_reasoning_content_on_assistant_messages
        and model is not None
        and model.reasoning
        and "reasoning_content" not in out
    ):
        out["reasoning_content"] = ""
    content = out.get("content")
    has_content = (
        bool(content)
        if isinstance(content, str)
        else bool(content)
        if isinstance(content, list)
        else False
    )
    if not has_content and not tool_calls:
        return None
    return out


def _convert_single_tool_result(
    message: ToolResultMessage,
    compat: OpenAICompat | None = None,
) -> dict:
    content = _text_of(message.content)
    if not content:
        content = (
            "(see attached image)"
            if any(isinstance(block, ImageContent) for block in message.content)
            else "(no tool output)"
        )
    converted = {
        "role": "tool",
        "tool_call_id": message.tool_call_id,
        "content": content,
    }
    if compat is not None and compat.requires_tool_result_name and message.tool_name:
        converted["name"] = message.tool_name
    return converted


def _convert_tool_result_group(
    messages: list[Message],
    start_index: int,
    model: Model | None,
    compat: OpenAICompat,
) -> tuple[list[dict], int]:
    converted: list[dict] = []
    image_parts: list[dict] = []
    index = start_index
    while index < len(messages) and messages[index].role == "toolResult":
        message = messages[index]
        converted_message = _convert_single_tool_result(message, compat)
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
        if compat.requires_assistant_after_tool_result:
            converted.append({"role": "assistant", "content": "I have processed the tool results."})
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
