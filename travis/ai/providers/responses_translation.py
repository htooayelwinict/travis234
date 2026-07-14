"""OpenAI Responses message and tool conversion."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Set
from typing import Any

from travis.ai.providers.message_translation import _sanitize_surrogates, _transform_messages
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
)


def _imul(left: int, right: int) -> int:
    return (left * right) & 0xFFFFFFFF


def _unsigned_base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    value &= 0xFFFFFFFF
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def short_hash(value: str) -> str:
    """Stable 32-bit hash over UTF-16 code units."""

    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        char_code = encoded[index] | (encoded[index + 1] << 8)
        h1 = _imul(h1 ^ char_code, 2_654_435_761)
        h2 = _imul(h2 ^ char_code, 1_597_334_677)
    h1 = _imul(h1 ^ (h1 >> 16), 2_246_822_507) ^ _imul(h2 ^ (h2 >> 13), 3_266_489_909)
    h1 &= 0xFFFFFFFF
    h2 = _imul(h2 ^ (h2 >> 16), 2_246_822_507) ^ _imul(h1 ^ (h1 >> 13), 3_266_489_909)
    return _unsigned_base36(h2) + _unsigned_base36(h1)


def _normalize_id_part(part: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", part)[:64]
    return re.sub(r"_+$", "", normalized)


def _normalize_responses_tool_call_id(
    tool_call_id: str,
    target_model: Model,
    source: AssistantMessage,
    allowed_tool_call_providers: Set[str],
) -> str:
    if target_model.provider not in allowed_tool_call_providers:
        return _normalize_id_part(tool_call_id)
    if "|" not in tool_call_id:
        return _normalize_id_part(tool_call_id)
    call_id, item_id = tool_call_id.split("|", 1)
    normalized_call_id = _normalize_id_part(call_id)
    is_foreign = source.provider != target_model.provider or source.api != target_model.api
    normalized_item_id = (
        _normalize_id_part(f"fc_{short_hash(item_id)}")
        if is_foreign
        else _normalize_id_part(item_id)
    )
    if not normalized_item_id.startswith("fc_"):
        normalized_item_id = _normalize_id_part(f"fc_{normalized_item_id}")
    return f"{normalized_call_id}|{normalized_item_id}"


def _parse_text_signature(signature: str | None) -> tuple[str | None, str | None]:
    if not signature:
        return None, None
    if signature.startswith("{"):
        try:
            parsed = json.loads(signature)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("v") == 1 and isinstance(parsed.get("id"), str):
            phase = parsed.get("phase")
            return parsed["id"], phase if phase in {"commentary", "final_answer"} else None
    return signature, None


def split_deferred_tools(
    context: Context,
    enabled: bool,
    normalize_name: Callable[[str], str] = lambda name: name,
) -> tuple[list[Tool], dict[str, Tool]]:
    unique_tools = {normalize_name(tool.name): tool for tool in context.tools or []}
    if not enabled:
        return list(unique_tools.values()), {}

    deferred_names: set[str] = set()
    used_names: set[str] = set()
    for message in context.messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolCall):
                    used_names.add(normalize_name(block.name))
        elif isinstance(message, ToolResultMessage):
            for name in message.added_tool_names or []:
                normalized_name = normalize_name(name)
                if normalized_name not in used_names:
                    deferred_names.add(normalized_name)

    immediate: list[Tool] = []
    deferred: dict[str, Tool] = {}
    for name, tool in unique_tools.items():
        if name in deferred_names:
            deferred[name] = tool
        else:
            immediate.append(tool)
    return immediate, deferred


def convert_responses_tools(
    tools: Iterable[Tool],
    *,
    strict: bool | None = False,
    defer_loading: bool = False,
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        item: dict[str, Any] = {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": strict,
        }
        if defer_loading:
            item["defer_loading"] = True
        converted.append(item)
    return converted


def convert_responses_messages(
    model: Model,
    context: Context,
    allowed_tool_call_providers: Set[str],
    *,
    include_system_prompt: bool = True,
    deferred_tools: Mapping[str, Tool] | None = None,
) -> list[dict[str, Any]]:
    output_messages: list[dict[str, Any]] = []
    loaded_tool_names: set[str] = set()

    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, target, source: _normalize_responses_tool_call_id(
            tool_call_id,
            target,
            source,
            allowed_tool_call_providers,
        ),
    )

    if include_system_prompt and context.system_prompt:
        supports_developer = (model.compat or {}).get("supportsDeveloperRole") is not False
        role = "developer" if model.reasoning and supports_developer else "system"
        output_messages.append({"role": role, "content": _sanitize_surrogates(context.system_prompt)})

    message_index = 0
    for message in transformed:
        if message.role == "user":
            if isinstance(message.content, str):
                output_messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": _sanitize_surrogates(message.content)}],
                    }
                )
            else:
                content: list[dict[str, Any]] = []
                for item in message.content:
                    if isinstance(item, TextContent):
                        content.append({"type": "input_text", "text": _sanitize_surrogates(item.text)})
                    elif isinstance(item, ImageContent):
                        content.append(
                            {
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": f"data:{item.mime_type};base64,{item.data}",
                            }
                        )
                if not content:
                    continue
                output_messages.append({"role": "user", "content": content})
        elif isinstance(message, AssistantMessage):
            items: list[dict[str, Any]] = []
            is_different_model = (
                message.model != model.id
                and message.provider == model.provider
                and message.api == model.api
            )
            text_block_index = 0
            for block in message.content:
                if isinstance(block, ThinkingContent):
                    if block.thinking_signature:
                        reasoning_item = json.loads(block.thinking_signature)
                        if isinstance(reasoning_item, dict):
                            items.append(reasoning_item)
                elif isinstance(block, TextContent):
                    signature_id, phase = _parse_text_signature(block.text_signature)
                    fallback = (
                        f"msg_travis_{message_index}"
                        if text_block_index == 0
                        else f"msg_travis_{message_index}_{text_block_index}"
                    )
                    text_block_index += 1
                    message_id = signature_id or fallback
                    if len(message_id) > 64:
                        message_id = f"msg_{short_hash(message_id)}"
                    item = {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": _sanitize_surrogates(block.text),
                                "annotations": [],
                            }
                        ],
                        "status": "completed",
                        "id": message_id,
                    }
                    if phase:
                        item["phase"] = phase
                    items.append(item)
                elif isinstance(block, ToolCall):
                    call_id, separator, item_id = block.id.partition("|")
                    if is_different_model and item_id.startswith("fc_"):
                        item_id = ""
                    function_call: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": block.name,
                        "arguments": json.dumps(block.arguments, separators=(",", ":")),
                    }
                    if separator and item_id:
                        function_call["id"] = item_id
                    items.append(function_call)
            if not items:
                continue
            output_messages.extend(items)
        elif isinstance(message, ToolResultMessage):
            text_result = "\n".join(
                block.text for block in message.content if isinstance(block, TextContent)
            )
            images = [block for block in message.content if isinstance(block, ImageContent)]
            call_id = message.tool_call_id.split("|", 1)[0]
            if images and "image" in model.input:
                result_content: str | list[dict[str, Any]] = []
                if text_result:
                    result_content.append({"type": "input_text", "text": _sanitize_surrogates(text_result)})
                for image in images:
                    result_content.append(
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{image.mime_type};base64,{image.data}",
                        }
                    )
            else:
                result_content = _sanitize_surrogates(
                    text_result if text_result else "(see attached image)" if images else "(no tool output)"
                )
            output_messages.append(
                {"type": "function_call_output", "call_id": call_id, "output": result_content}
            )

            newly_loaded: list[Tool] = []
            for name in message.added_tool_names or []:
                tool = deferred_tools.get(name) if deferred_tools else None
                if tool is None or name in loaded_tool_names:
                    continue
                loaded_tool_names.add(name)
                newly_loaded.append(tool)
            if newly_loaded:
                names = [tool.name for tool in newly_loaded]
                search_key = f"{message.tool_call_id}:{','.join(names)}"
                search_call_id = f"travis_tool_load_{short_hash(search_key)}"
                output_messages.append(
                    {
                        "type": "tool_search_call",
                        "call_id": search_call_id,
                        "execution": "client",
                        "status": "completed",
                        "arguments": {"query": " ".join(names), "limit": len(names)},
                    }
                )
                output_messages.append(
                    {
                        "type": "tool_search_output",
                        "call_id": search_call_id,
                        "execution": "client",
                        "status": "completed",
                        "tools": convert_responses_tools(newly_loaded, defer_loading=True),
                    }
                )
        message_index += 1

    return output_messages


def encode_text_signature(message_id: str, phase: str | None = None) -> str:
    payload: dict[str, Any] = {"v": 1, "id": message_id}
    if phase in {"commentary", "final_answer"}:
        payload["phase"] = phase
    return json.dumps(payload, separators=(",", ":"))
