"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace

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
        if isinstance(message, AssistantMessage):
            transformed.append(
                _transform_assistant_message(
                    message,
                    model,
                    tool_call_id_map,
                    normalize_tool_call_id,
                )
            )
        elif isinstance(message, ToolResultMessage):
            normalized_id = tool_call_id_map.get(message.tool_call_id)
            transformed.append(
                replace(message, tool_call_id=normalized_id)
                if normalized_id and normalized_id != message.tool_call_id
                else message
            )
        else:
            transformed.append(message)
    return _repair_tool_result_history(transformed)


def _transform_assistant_message(
    message: AssistantMessage,
    model: Model,
    tool_call_id_map: dict[str, str],
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None,
) -> AssistantMessage:
    is_same_model = (
        message.provider == model.provider
        and message.api == model.api
        and message.model == model.id
    )
    content: list[TextContent | ThinkingContent | ImageContent | ToolCall] = []
    for block in message.content:
        transformed = _transform_assistant_block(
            block,
            message,
            model,
            is_same_model,
            tool_call_id_map,
            normalize_tool_call_id,
        )
        if transformed is not None:
            content.append(transformed)
    return replace(message, content=content)


def _transform_assistant_block(
    block: TextContent | ThinkingContent | ImageContent | ToolCall,
    message: AssistantMessage,
    model: Model,
    is_same_model: bool,
    tool_call_id_map: dict[str, str],
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None,
) -> TextContent | ThinkingContent | ImageContent | ToolCall | None:
    if isinstance(block, ThinkingContent):
        return _transform_thinking_block(block, is_same_model)
    if isinstance(block, TextContent):
        return block if is_same_model else TextContent(text=block.text)
    if isinstance(block, ToolCall):
        return _transform_tool_call(
            block,
            message,
            model,
            is_same_model,
            tool_call_id_map,
            normalize_tool_call_id,
        )
    return block


def _transform_thinking_block(
    block: ThinkingContent,
    is_same_model: bool,
) -> TextContent | ThinkingContent | None:
    if block.redacted:
        return block if is_same_model else None
    if is_same_model and block.thinking_signature:
        return block
    if not block.thinking or not block.thinking.strip():
        return None
    return block if is_same_model else TextContent(text=block.thinking)


def _transform_tool_call(
    block: ToolCall,
    message: AssistantMessage,
    model: Model,
    is_same_model: bool,
    tool_call_id_map: dict[str, str],
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None,
) -> ToolCall:
    if is_same_model:
        return block
    transformed = replace(block, thought_signature=None) if block.thought_signature else block
    normalized_id = (
        normalize_tool_call_id(block.id, model, message)
        if normalize_tool_call_id is not None
        else _normalize_tool_call_id(block.id, model)
    )
    if normalized_id == block.id:
        return transformed
    tool_call_id_map[block.id] = normalized_id
    return replace(transformed, id=normalized_id)


def _append_missing_tool_results(
    result: list[Message],
    pending_tool_calls: list[ToolCall],
    existing_tool_result_ids: set[str],
) -> None:
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


def _drop_unanswered_user_messages(result: list[Message]) -> None:
    while result and result[-1].role == "user":
        result.pop()


def _repair_tool_result_history(transformed: list[Message]) -> list[Message]:
    result: list[Message] = []
    pending_tool_calls: list[ToolCall] = []
    existing_tool_result_ids: set[str] = set()

    for message in transformed:
        if isinstance(message, AssistantMessage):
            if pending_tool_calls:
                _append_missing_tool_results(result, pending_tool_calls, existing_tool_result_ids)
                pending_tool_calls = []
                existing_tool_result_ids = set()
            if message.stop_reason in ("error", "aborted"):
                # Failed assistant messages can contain incomplete reasoning or
                # malformed tool calls. They are retained in the session record
                # but omitted from provider replay. If no completed assistant or
                # tool work followed the latest user input, omit that unanswered
                # input as well. Replaying it beside the next prompt makes some
                # providers treat the newer instruction as an injection or as
                # steering for the cancelled task.
                _drop_unanswered_user_messages(result)
                continue
            tool_calls = [block for block in message.content if isinstance(block, ToolCall)]
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()
            result.append(message)
        elif isinstance(message, ToolResultMessage):
            existing_tool_result_ids.add(message.tool_call_id)
            result.append(message)
        else:
            if pending_tool_calls:
                _append_missing_tool_results(result, pending_tool_calls, existing_tool_result_ids)
                pending_tool_calls = []
                existing_tool_result_ids = set()
            result.append(message)

    _append_missing_tool_results(result, pending_tool_calls, existing_tool_result_ids)
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
    if not isinstance(message, AssistantMessage):
        if isinstance(message, ToolResultMessage):
            return _convert_single_tool_result(message)
        content = _sanitize_surrogates(message.content) if isinstance(message.content, str) else _convert_user_content_parts(message.content)
        return {"role": "user", "content": content}
    return _convert_assistant_message(message, model, compat)


def _assistant_text(message: AssistantMessage) -> str:
    return "".join(
        _sanitize_surrogates(block.text)
        for block in message.content
        if isinstance(block, TextContent) and block.text.strip()
    )


def _parse_responses_reasoning_item(signature: str | None) -> dict[str, object] | None:
    if not signature:
        return None
    try:
        value = json.loads(signature)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("type") != "reasoning":
        return None
    if not all(isinstance(key, str) for key in value):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _assistant_reasoning(
    message: AssistantMessage,
) -> tuple[list[dict[str, object]], list[ThinkingContent]]:
    responses_reasoning_items: list[dict[str, object]] = []
    textual_thinking_parts: list[ThinkingContent] = []
    for block in message.content:
        if not isinstance(block, ThinkingContent) or not block.thinking.strip():
            continue
        reasoning_item = _parse_responses_reasoning_item(block.thinking_signature)
        if reasoning_item is not None:
            responses_reasoning_items.append(reasoning_item)
        else:
            textual_thinking_parts.append(block)
    return responses_reasoning_items, textual_thinking_parts


def _parse_reasoning_detail(signature: str) -> object | None:
    try:
        return json.loads(signature)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _assistant_tool_payloads(
    message: AssistantMessage,
) -> tuple[list[dict[str, object]], list[object]]:
    tool_calls: list[dict[str, object]] = []
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
            reasoning_detail = _parse_reasoning_detail(block.thought_signature)
            if reasoning_detail is not None:
                reasoning_details.append(reasoning_detail)
    return tool_calls, reasoning_details


def _apply_textual_reasoning(
    out: dict[str, object],
    textual_thinking_parts: list[ThinkingContent],
    content_text: str,
    model: Model | None,
    compat: OpenAICompat,
) -> None:
    if not textual_thinking_parts:
        return
    if compat.requires_thinking_as_text:
        thinking_text = "\n\n".join(
            _sanitize_surrogates(block.thinking) for block in textual_thinking_parts
        )
        thinking_content = [{"type": "text", "text": thinking_text}]
        if content_text:
            thinking_content.append({"type": "text", "text": content_text})
        out["content"] = thinking_content
        return
    signature = textual_thinking_parts[0].thinking_signature
    if model is not None and model.provider == "opencode-go" and signature == "reasoning":
        signature = "reasoning_content"
    if signature:
        out[signature] = "\n".join(
            _sanitize_surrogates(block.thinking) for block in textual_thinking_parts
        )


def _assistant_has_content(out: dict[str, object]) -> bool:
    content = out.get("content")
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return bool(content)
    return False


def _convert_assistant_message(
    message: AssistantMessage,
    model: Model | None,
    compat: OpenAICompat,
) -> dict[str, object] | None:
    content_text = _assistant_text(message)
    responses_reasoning_items, textual_thinking_parts = _assistant_reasoning(message)
    tool_calls, reasoning_details = _assistant_tool_payloads(message)
    if not content_text and not responses_reasoning_items and not textual_thinking_parts and not tool_calls:
        return None
    out: dict[str, object] = {
        "role": "assistant",
        "content": "" if compat.requires_assistant_after_tool_result else None,
    }
    if responses_reasoning_items:
        out["codex_reasoning_items"] = responses_reasoning_items
    _apply_textual_reasoning(out, textual_thinking_parts, content_text, model, compat)
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
    if not _assistant_has_content(out) and not tool_calls:
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
