"""provider transports for travis."""

from __future__ import annotations

import copy
import base64
import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from travis.ai.providers.base import (
    OMIT_TEMPERATURE,
    NormalizedResponse,
    NormalizedToolCall,
    NormalizedUsage,
    ProviderProfile,
)
from travis.ai.providers.responses_translation import (
    convert_responses_messages,
    convert_responses_tools,
    split_deferred_tools,
)
from travis.ai.providers.transport_families.chat_completions import (
    ChatCompletionsTransport,
    _clamp_openai_prompt_cache_key,
)
from travis.ai.providers.transport_families.mistral import MistralConversationsTransport
from travis.ai.providers.transport_families.unsupported import UnsupportedTransport
from travis.ai.types import AssistantMessage, Context, ImageContent, TextContent, ThinkingContent, ToolCall, ToolResultMessage


_CLAUDE_CODE_TOOL_NAMES = {
    name.lower(): name
    for name in (
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Grep",
        "Glob",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "KillShell",
        "NotebookEdit",
        "Skill",
        "Task",
        "TaskOutput",
        "TodoWrite",
        "WebFetch",
        "WebSearch",
    )
}


def _claude_code_tool_name(name: str) -> str:
    return _CLAUDE_CODE_TOOL_NAMES.get(name.lower(), name)


def _anthropic_default_supports_tool_references(model: Any) -> bool:
    if model.provider != "anthropic" or "haiku" in model.id:
        return False
    match = re.match(r"^claude-(?:opus|sonnet|fable)-(\d+)(?:-(\d+))?(?:-|$)", model.id)
    if match is None:
        return False
    major = int(match.group(1))
    minor_text = match.group(2)
    minor = int(minor_text) if minor_text and len(minor_text) < 8 else 0
    return major > 4 or (major == 4 and minor >= 5)




def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "\n".join(parts)


def _tool_function(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    return function if isinstance(function, dict) else tool


def _tool_arguments(arguments: Any, tool_name: str = "?") -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments, strict=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _split_responses_tool_call_id(tool_call_id: str) -> tuple[str, str | None]:
    if "|" not in tool_call_id:
        return tool_call_id, None
    call_id, item_id = tool_call_id.split("|", 1)
    return call_id, item_id or None


def _data_url_to_anthropic_image(part: dict[str, Any]) -> dict[str, Any] | None:
    image_url = part.get("image_url")
    if not isinstance(image_url, dict):
        return None
    url = image_url.get("url")
    if not isinstance(url, str):
        return None
    match = re.match(r"^data:([^;]+);base64,(.*)$", url, flags=re.DOTALL)
    if not match:
        return None
    media_type, data = match.groups()
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _openai_content_to_anthropic(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part.strip():
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                blocks.append({"type": "text", "text": text})
            continue
        if part.get("type") == "image_url":
            image = _data_url_to_anthropic_image(part)
            if image is not None:
                blocks.append(image)
    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text") or "")
    return blocks


def _openai_content_to_responses(content: Any, *, output: bool = False) -> list[dict[str, Any]]:
    text_type = "output_text" if output else "input_text"
    image_type = "input_image"
    if isinstance(content, str):
        return [{"type": text_type, "text": content, **({"annotations": []} if output else {})}]
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": text_type, "text": part, **({"annotations": []} if output else {})})
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            blocks.append({"type": text_type, "text": part["text"], **({"annotations": []} if output else {})})
        elif not output and part.get("type") == "image_url":
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                blocks.append({"type": image_type, "detail": "auto", "image_url": image_url["url"]})
    return blocks


def _google_requires_tool_call_id(model_id: str) -> bool:
    return model_id.startswith(("claude-", "gpt-oss-"))


def _google_valid_thought_signature(signature: str | None) -> bool:
    return bool(
        signature
        and len(signature) % 4 == 0
        and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", signature)
    )


def _google_supports_multimodal_function_response(model_id: str) -> bool:
    match = re.match(r"^gemini(?:-live)?-(\d+)", model_id.lower())
    return int(match.group(1)) >= 3 if match else True


def _google_contents(context: Context, model: Any) -> list[dict[str, Any]]:
    from travis.ai.providers.message_translation import _sanitize_surrogates, _transform_messages

    contents: list[dict[str, Any]] = []
    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _model, _source: (
            re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64]
            if _google_requires_tool_call_id(model.id)
            else tool_call_id
        ),
    )
    for message in transformed:
        if message.role == "user":
            if isinstance(message.content, str):
                parts = [{"text": _sanitize_surrogates(message.content)}]
            else:
                parts = []
                for block in message.content:
                    if isinstance(block, TextContent):
                        parts.append({"text": _sanitize_surrogates(block.text)})
                    elif isinstance(block, ImageContent):
                        parts.append({"inlineData": {"mimeType": block.mime_type, "data": block.data}})
            if parts:
                contents.append({"role": "user", "parts": parts})
            continue
        if isinstance(message, AssistantMessage):
            parts: list[dict[str, Any]] = []
            same_model = (
                message.provider == model.provider
                and message.model == model.id
            )
            for block in message.content:
                if isinstance(block, TextContent) and block.text.strip():
                    part: dict[str, Any] = {"text": _sanitize_surrogates(block.text)}
                    if same_model and _google_valid_thought_signature(block.text_signature):
                        part["thoughtSignature"] = block.text_signature
                    parts.append(part)
                elif isinstance(block, ThinkingContent) and block.thinking.strip():
                    part = {"text": _sanitize_surrogates(block.thinking)}
                    if same_model:
                        part["thought"] = True
                        if _google_valid_thought_signature(block.thinking_signature):
                            part["thoughtSignature"] = block.thinking_signature
                    parts.append(part)
                elif isinstance(block, ToolCall):
                    call: dict[str, Any] = {"name": block.name, "args": block.arguments or {}}
                    if _google_requires_tool_call_id(model.id):
                        call["id"] = block.id
                    part = {"functionCall": call}
                    if same_model and _google_valid_thought_signature(block.thought_signature):
                        part["thoughtSignature"] = block.thought_signature
                    parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        if isinstance(message, ToolResultMessage):
            text = "\n".join(
                block.text for block in message.content if isinstance(block, TextContent)
            )
            images = (
                [block for block in message.content if isinstance(block, ImageContent)]
                if "image" in model.input
                else []
            )
            response_value = _sanitize_surrogates(
                text if text else "(see attached image)" if images else ""
            )
            response = {"error" if message.is_error else "output": response_value}
            image_parts = [
                {"inlineData": {"mimeType": image.mime_type, "data": image.data}}
                for image in images
            ]
            supports_multimodal = _google_supports_multimodal_function_response(model.id)
            function_response: dict[str, Any] = {
                "name": message.tool_name,
                "response": response,
            }
            if images and supports_multimodal:
                function_response["parts"] = image_parts
            if _google_requires_tool_call_id(model.id):
                function_response["id"] = message.tool_call_id
            part = {"functionResponse": function_response}
            if (
                contents
                and contents[-1].get("role") == "user"
                and any("functionResponse" in item for item in contents[-1].get("parts", []))
            ):
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
            if images and not supports_multimodal:
                contents.append(
                    {"role": "user", "parts": [{"text": "Tool result image:"}, *image_parts]}
                )
    return contents


def _google_tools(context: Context) -> list[dict[str, Any]] | None:
    if not context.tools:
        return None
    return [
        {
            "functionDeclarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parametersJsonSchema": tool.parameters,
                }
                for tool in context.tools
            ]
        }
    ]


def _bedrock_supports_cache(model: Any) -> bool:
    candidates = {
        value.lower().replace("_", "-").replace(".", "-").replace(":", "-")
        for value in (model.id, model.name)
        if value
    }
    if not any("claude" in value for value in candidates):
        return os.environ.get("AWS_BEDROCK_FORCE_CACHE") == "1"
    return any(
        "fable-5" in value
        or "sonnet-5" in value
        or "-4-" in value
        or "claude-3-7-sonnet" in value
        or "claude-3-5-haiku" in value
        for value in candidates
    )


def _bedrock_image(block: ImageContent) -> dict[str, Any]:
    image_format = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }.get(block.mime_type)
    if image_format is None:
        raise ValueError(f"Unknown image type: {block.mime_type}")
    return {
        "image": {
            "format": image_format,
            "source": {"bytes": base64.b64decode(block.data, validate=True)},
        }
    }


def _bedrock_messages(context: Context, model: Any, cache_retention: str) -> list[dict[str, Any]]:
    from travis.ai.providers.message_translation import _sanitize_surrogates, _transform_messages

    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _model, _source: re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64],
    )
    messages: list[dict[str, Any]] = []
    index = 0
    while index < len(transformed):
        message = transformed[index]
        if message.role == "user":
            blocks: list[dict[str, Any]] = []
            if isinstance(message.content, str):
                text = _sanitize_surrogates(message.content)
                blocks.append({"text": text if text.strip() else "<empty>"})
            else:
                for block in message.content:
                    if isinstance(block, TextContent) and block.text.strip():
                        blocks.append({"text": _sanitize_surrogates(block.text)})
                    elif isinstance(block, ImageContent):
                        blocks.append(_bedrock_image(block))
            messages.append({"role": "user", "content": blocks or [{"text": "<empty>"}]})
            index += 1
            continue
        if isinstance(message, AssistantMessage):
            blocks = []
            is_claude = "claude" in model.id.lower() or "claude" in model.name.lower()
            for block in message.content:
                if isinstance(block, TextContent) and block.text.strip():
                    blocks.append({"text": _sanitize_surrogates(block.text)})
                elif isinstance(block, ToolCall):
                    blocks.append(
                        {"toolUse": {"toolUseId": block.id[:64], "name": block.name, "input": block.arguments}}
                    )
                elif isinstance(block, ThinkingContent) and block.thinking.strip():
                    thinking = _sanitize_surrogates(block.thinking)
                    if is_claude and not block.thinking_signature:
                        blocks.append({"text": thinking})
                    else:
                        reasoning_text: dict[str, Any] = {"text": thinking}
                        if is_claude:
                            reasoning_text["signature"] = block.thinking_signature
                        blocks.append({"reasoningContent": {"reasoningText": reasoning_text}})
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            index += 1
            continue
        if isinstance(message, ToolResultMessage):
            results: list[dict[str, Any]] = []
            while index < len(transformed) and isinstance(transformed[index], ToolResultMessage):
                result = transformed[index]
                result_content: list[dict[str, Any]] = []
                for block in result.content:
                    if isinstance(block, TextContent) and block.text.strip():
                        result_content.append({"text": _sanitize_surrogates(block.text)})
                    elif isinstance(block, ImageContent):
                        result_content.append(_bedrock_image(block))
                results.append(
                    {
                        "toolResult": {
                            "toolUseId": result.tool_call_id[:64],
                            "content": result_content or [{"text": "<empty>"}],
                            "status": "error" if result.is_error else "success",
                        }
                    }
                )
                index += 1
            messages.append({"role": "user", "content": results})
            continue
        index += 1
    if cache_retention != "none" and _bedrock_supports_cache(model) and messages:
        last = messages[-1]
        if last.get("role") == "user":
            cache_point: dict[str, Any] = {"type": "default"}
            if cache_retention == "long":
                cache_point["ttl"] = "1h"
            last["content"].append({"cachePoint": cache_point})
    return messages


def _anthropic_native_messages(
    context: Context,
    model: Any,
    cache_control: dict[str, Any] | None,
    *,
    allow_empty_signature: bool = False,
    deferred_tool_names: set[str] | None = None,
    normalize_tool_name=lambda name: name,
) -> list[dict[str, Any]]:
    from travis.ai.providers.message_translation import _transform_messages

    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _model, _source: re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64],
    )
    messages: list[dict[str, Any]] = []
    loaded_tool_names: set[str] = set()
    deferred_tool_names = deferred_tool_names or set()
    index = 0
    while index < len(transformed):
        message = transformed[index]
        if message.role == "user":
            if isinstance(message.content, str):
                if message.content.strip():
                    messages.append({"role": "user", "content": message.content})
            else:
                blocks: list[dict[str, Any]] = []
                for block in message.content:
                    if isinstance(block, TextContent) and block.text.strip():
                        blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ImageContent):
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": block.mime_type,
                                    "data": block.data,
                                },
                            }
                        )
                if blocks:
                    messages.append({"role": "user", "content": blocks})
            index += 1
            continue
        if isinstance(message, AssistantMessage):
            blocks = []
            for block in message.content:
                if isinstance(block, TextContent) and block.text.strip():
                    blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ThinkingContent):
                    if block.redacted and block.thinking_signature:
                        blocks.append({"type": "redacted_thinking", "data": block.thinking_signature})
                    elif block.thinking.strip():
                        if block.thinking_signature:
                            blocks.append(
                                {
                                    "type": "thinking",
                                    "thinking": block.thinking,
                                    "signature": block.thinking_signature,
                                }
                            )
                        elif allow_empty_signature:
                            blocks.append({"type": "thinking", "thinking": block.thinking, "signature": ""})
                        else:
                            blocks.append({"type": "text", "text": block.thinking})
                elif isinstance(block, ToolCall):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": normalize_tool_name(block.name),
                            "input": block.arguments or {},
                        }
                    )
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            index += 1
            continue
        if isinstance(message, ToolResultMessage):
            results: list[dict[str, Any]] = []
            sibling_content: list[dict[str, Any]] = []
            while index < len(transformed) and isinstance(transformed[index], ToolResultMessage):
                result = transformed[index]
                content: list[dict[str, Any]] = []
                for block in result.content:
                    if isinstance(block, TextContent) and block.text.strip():
                        content.append({"type": "text", "text": block.text})
                    elif isinstance(block, ImageContent):
                        content.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": block.mime_type,
                                    "data": block.data,
                                },
                            }
                        )
                has_images = any(isinstance(block, ImageContent) for block in result.content)
                if has_images and not any(part.get("type") == "text" for part in content):
                    content.insert(0, {"type": "text", "text": "(see attached image)"})
                converted_content: str | list[dict[str, Any]]
                if not has_images:
                    converted_content = "\n".join(
                        block.text for block in result.content if isinstance(block, TextContent)
                    )
                else:
                    converted_content = content
                references: list[dict[str, Any]] = []
                for name in result.added_tool_names or []:
                    normalized_name = normalize_tool_name(name)
                    if normalized_name not in deferred_tool_names or normalized_name in loaded_tool_names:
                        continue
                    loaded_tool_names.add(normalized_name)
                    references.append(
                        {"type": "tool_reference", "tool_name": normalize_tool_name(name)}
                    )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": references if references else converted_content,
                        "is_error": result.is_error,
                    }
                )
                if references:
                    if isinstance(converted_content, str):
                        sibling_content.append({"type": "text", "text": converted_content})
                    else:
                        sibling_content.extend(converted_content)
                index += 1
            messages.append({"role": "user", "content": [*results, *sibling_content]})
            continue
        index += 1
    if cache_control and messages and messages[-1].get("role") == "user":
        last = messages[-1]
        if isinstance(last.get("content"), str):
            last["content"] = [
                {"type": "text", "text": last["content"], "cache_control": cache_control}
            ]
        elif isinstance(last.get("content"), list) and last["content"]:
            final_block = last["content"][-1]
            if isinstance(final_block, dict) and final_block.get("type") in {
                "text",
                "image",
                "tool_result",
            }:
                final_block["cache_control"] = cache_control
    return messages


def _anthropic_native_tools(
    tools: list[Any],
    cache_control: dict[str, Any] | None,
    *,
    eager_input_streaming: bool,
    normalize_tool_name=lambda name: name,
    defer_loading: bool = False,
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        schema = tool.parameters if isinstance(tool.parameters, dict) else {}
        converted_tool: dict[str, Any] = {
            "name": normalize_tool_name(tool.name),
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        }
        if eager_input_streaming:
            converted_tool["eager_input_streaming"] = True
        if defer_loading:
            converted_tool["defer_loading"] = True
        if cache_control and index == len(tools) - 1:
            converted_tool["cache_control"] = cache_control
        converted.append(converted_tool)
    return converted




def _anthropic_allows_disabled_thinking(target_model: Any) -> bool:
    mapping = getattr(target_model, "thinking_level_map", None)
    return not (
        isinstance(mapping, dict)
        and "off" in mapping
        and mapping["off"] is None
    )


def _apply_anthropic_wire_compatibility(
    body: dict[str, Any],
    *,
    compat: dict[str, Any],
    thinking_enabled: bool,
) -> None:
    if compat.get("supportsTemperature") is False:
        body.pop("temperature", None)
    if compat.get("supportsTopP") is False:
        body.pop("top_p", None)
    elif thinking_enabled:
        top_p = body.get("top_p")
        if isinstance(top_p, (int, float)) and not 0.95 <= float(top_p) <= 1.0:
            body.pop("top_p", None)

    if not thinking_enabled:
        return
    tool_choice = body.get("tool_choice")
    choice_type = tool_choice.get("type") if isinstance(tool_choice, dict) else tool_choice
    if choice_type in {"any", "tool", "required"}:
        body["tool_choice"] = {"type": "auto"}




class GoogleGenerativeAITransport:
    api = "google-generative-ai"
    api_mode = "google_generative_ai"
    endpoint_path = ""

    @staticmethod
    def build_url(
        base_url: str,
        model: str,
        _options: object | None = None,
        _api_key: str | None = None,
    ) -> str:
        return f"{base_url.rstrip('/')}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse"

    @staticmethod
    def _thinking_config(model: str, reasoning_config: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(reasoning_config, dict):
            return None
        enabled = reasoning_config.get("enabled", True) is not False
        effort = str(reasoning_config.get("effort") or "medium").strip().lower()
        is_gemma4 = bool(re.search(r"gemma-?4", model.lower()))
        is_gemini3_pro = bool(re.search(r"gemini-3(?:\.\d+)?-pro", model.lower()))
        is_gemini3_flash = bool(re.search(r"gemini-3(?:\.\d+)?-flash", model.lower())) or model.lower() in {
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
        }
        if not enabled or effort in {"none", "off"}:
            if is_gemini3_pro:
                return {"thinkingLevel": "LOW"}
            if is_gemini3_flash or is_gemma4:
                return {"thinkingLevel": "MINIMAL"}
            return {"thinkingBudget": 0}
        effort = effort if effort in {"minimal", "low", "medium", "high"} else "medium"
        if is_gemini3_pro:
            level = "LOW" if effort in {"minimal", "low"} else "HIGH"
            return {"includeThoughts": True, "thinkingLevel": level}
        if is_gemini3_flash:
            return {"includeThoughts": True, "thinkingLevel": effort.upper()}
        if is_gemma4:
            level = "MINIMAL" if effort in {"minimal", "low"} else "HIGH"
            return {"includeThoughts": True, "thinkingLevel": level}
        if "2.5-pro" in model:
            budget = {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}[effort]
        elif "2.5-flash-lite" in model:
            budget = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576}[effort]
        elif "2.5-flash" in model:
            budget = {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}[effort]
        else:
            budget = -1
        return {"includeThoughts": True, "thinkingBudget": budget}

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        omit_max_tokens: bool = False,
        tool_choice: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        context: Context,
        target_model: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
        thinking = self._thinking_config(model, reasoning_config)
        if target_model.reasoning and thinking:
            generation_config["thinkingConfig"] = thinking
        body: dict[str, Any] = {"contents": _google_contents(context, target_model)}
        if generation_config:
            body["generationConfig"] = generation_config
        if context.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": context.system_prompt}]}
        google_tools = _google_tools(context)
        if google_tools:
            body["tools"] = google_tools
            if tool_choice:
                mode = tool_choice.upper() if tool_choice in {"auto", "none", "any"} else "AUTO"
                body["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
        if request_overrides:
            body.update(request_overrides)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class GoogleVertexTransport(GoogleGenerativeAITransport):
    api = "google-vertex"
    api_mode = "google_vertex"

    @staticmethod
    def build_url(
        base_url: str,
        model: str,
        options: object | None = None,
        api_key: str | None = None,
    ) -> str:
        if api_key:
            model_path = f"publishers/google/models/{quote(model, safe='')}"
            return f"https://aiplatform.googleapis.com/v1/{model_path}:streamGenerateContent?alt=sse"
        project = str(
            getattr(options, "project", None)
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
            or ""
        ).strip()
        location = str(getattr(options, "location", None) or os.environ.get("GOOGLE_CLOUD_LOCATION") or "").strip()
        if not project:
            raise ValueError(
                "Vertex AI requires a project ID. Set GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT or pass project in options."
            )
        if not location:
            raise ValueError("Vertex AI requires a location. Set GOOGLE_CLOUD_LOCATION or pass location in options.")
        model_path = f"publishers/google/models/{quote(model, safe='')}"
        if location in {"us", "eu"}:
            root = f"https://aiplatform.{location}.rep.googleapis.com/v1"
        else:
            root = f"https://{location}-aiplatform.googleapis.com/v1"
        return (
            f"{root}/projects/{quote(project, safe='')}/locations/{quote(location, safe='')}/"
            f"{model_path}:streamGenerateContent?alt=sse"
        )


class BedrockConverseStreamTransport:
    api = "bedrock-converse-stream"
    api_mode = "bedrock_converse_stream"
    endpoint_path = ""
    binary_stream = True

    @staticmethod
    def build_url(
        base_url: str,
        model: str,
        _options: object | None = None,
        _api_key: str | None = None,
    ) -> str:
        return f"{base_url.rstrip('/')}/model/{quote(model, safe='')}/converse-stream"

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        cache_retention: str | None = None,
        context: Context,
        target_model: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        retention = cache_retention or "short"
        body: dict[str, Any] = {
            "messages": _bedrock_messages(context, target_model, retention),
        }
        if context.system_prompt:
            system: list[dict[str, Any]] = [{"text": context.system_prompt}]
            if retention != "none" and _bedrock_supports_cache(target_model):
                cache_point: dict[str, Any] = {"type": "default"}
                if retention == "long":
                    cache_point["ttl"] = "1h"
                system.append({"cachePoint": cache_point})
            body["system"] = system
        inference: dict[str, Any] = {}
        if max_tokens is not None:
            inference["maxTokens"] = max_tokens
        if temperature is not None:
            inference["temperature"] = temperature
        if inference:
            body["inferenceConfig"] = inference
        if context.tools:
            body["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": {"json": tool.parameters},
                        }
                    }
                    for tool in context.tools
                ]
            }
        if target_model.reasoning and isinstance(reasoning_config, dict):
            enabled = reasoning_config.get("enabled", True) is not False
            effort = str(reasoning_config.get("effort") or "").strip().lower()
            if enabled and effort not in {"", "none", "off"} and "claude" in target_model.id.lower():
                normalized = target_model.id.lower().replace("_", "-").replace(".", "-")
                adaptive = any(
                    value in normalized
                    for value in (
                        "opus-4-6",
                        "opus-4-7",
                        "opus-4-8",
                        "opus-5",
                        "sonnet-4-6",
                        "sonnet-5",
                        "fable-5",
                    )
                )
                if adaptive:
                    mapped_effort = "low" if effort in {"minimal", "low"} else effort
                    if mapped_effort not in {"low", "medium", "high", "xhigh", "max"}:
                        mapped_effort = "high"
                    body["additionalModelRequestFields"] = {
                        "thinking": {"type": "adaptive", "display": "summarized"},
                        "output_config": {"effort": mapped_effort},
                    }
                else:
                    budget = {
                        "minimal": 1024,
                        "low": 2048,
                        "medium": 8192,
                        "high": 16384,
                        "xhigh": 16384,
                        "max": 16384,
                    }.get(effort, 8192)
                    body["additionalModelRequestFields"] = {
                        "thinking": {"type": "enabled", "budget_tokens": budget, "display": "summarized"},
                        "anthropic_beta": ["interleaved-thinking-2025-05-14"],
                    }
        if request_overrides:
            body.update(request_overrides)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class AnthropicMessagesTransport:
    api = "anthropic-messages"
    api_mode = "anthropic_messages"
    endpoint_path = "/v1/messages"

    def convert_messages(self, messages: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if not isinstance(message, dict):
                index += 1
                continue
            role = message.get("role")
            if role == "system":
                index += 1
                continue
            if role == "user":
                content = _openai_content_to_anthropic(message.get("content"))
                if content:
                    converted.append({"role": "user", "content": content})
                index += 1
                continue
            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                text = _content_to_text(message.get("content"))
                if text.strip():
                    blocks.append({"type": "text", "text": text})
                for tool_call in message.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    function = _tool_function(tool_call)
                    name = str(function.get("name") or "")
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tool_call.get("id") or ""),
                            "name": name,
                            "input": _tool_arguments(function.get("arguments"), name),
                        }
                    )
                if blocks:
                    converted.append({"role": "assistant", "content": blocks})
                index += 1
                continue
            if role == "tool":
                tool_results: list[dict[str, Any]] = []
                while index < len(messages):
                    tool_message = messages[index]
                    if not isinstance(tool_message, dict) or tool_message.get("role") != "tool":
                        break
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": str(tool_message.get("tool_call_id") or ""),
                            "content": _content_to_text(tool_message.get("content")),
                            "is_error": bool(tool_message.get("is_error", False)),
                        }
                    )
                    index += 1
                if tool_results:
                    converted.append({"role": "user", "content": tool_results})
                continue
            index += 1
        return converted

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            function = _tool_function(tool)
            name = str(function.get("name") or "")
            if not name:
                continue
            converted.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "input_schema": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
                }
            )
        return converted

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        omit_max_tokens: bool = False,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        cache_retention: str | None = None,
        session_id: str | None = None,
        tool_choice: Any | None = None,
        metadata: dict[str, Any] | None = None,
        context: Context | None = None,
        target_model: Any = None,
        model_compat: dict[str, Any] | None = None,
        api_key: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        compat = model_compat or {}
        is_oauth = isinstance(api_key, str) and "sk-ant-oat" in api_key
        normalize_tool_name = _claude_code_tool_name if is_oauth else (lambda name: name)
        retention = cache_retention or "short"
        supports_long = compat.get("supportsLongCacheRetention") is not False
        cache_control = None
        if retention != "none":
            cache_control = {"type": "ephemeral"}
            if retention == "long" and supports_long:
                cache_control["ttl"] = "1h"
        native_context = context
        native_model = target_model
        immediate_tools: list[Any] = []
        deferred_tools: list[Any] = []
        if native_context is not None and native_model is not None:
            supports_references = compat.get("supportsToolReferences")
            if supports_references is None:
                supports_references = _anthropic_default_supports_tool_references(native_model)
            immediate_tools, deferred_by_name = split_deferred_tools(
                native_context,
                bool(supports_references),
                normalize_tool_name,
            )
            deferred_tools = list(deferred_by_name.values())
            if not immediate_tools and deferred_tools:
                immediate_tools = deferred_tools
                deferred_tools = []
            deferred_tool_names = {
                normalize_tool_name(tool.name) for tool in deferred_tools
            }
        else:
            deferred_tool_names = set()
        converted_messages = (
            _anthropic_native_messages(
                native_context,
                native_model,
                cache_control,
                allow_empty_signature=compat.get("allowEmptySignature") is True,
                deferred_tool_names=deferred_tool_names,
                normalize_tool_name=normalize_tool_name,
            )
            if native_context is not None and native_model is not None
            else self.convert_messages(messages)
        )
        if is_oauth:
            for message in converted_messages:
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                        block["name"] = _claude_code_tool_name(block["name"])
        if omit_max_tokens:
            native_ceiling = int(getattr(native_model, "max_tokens", 0) or 0) or profile.get_max_tokens(model) or 4096
            if native_context is not None and native_model is not None:
                from travis.ai.context_estimate import clamp_max_tokens_to_context

                native_ceiling = clamp_max_tokens_to_context(native_model, native_context, native_ceiling)
            resolved_max_tokens = native_ceiling
        else:
            resolved_max_tokens = max_tokens if max_tokens is not None else profile.get_max_tokens(model) or 4096
        body: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": resolved_max_tokens,
            "stream": stream,
        }
        system_text = native_context.system_prompt if native_context is not None else None
        system_blocks = (
            [{"type": "text", "text": system_text, **({"cache_control": cache_control} if cache_control else {})}]
            if system_text
            else [
                {"type": "text", "text": content, **({"cache_control": cache_control} if cache_control else {})}
                for message in messages
                if isinstance(message, dict)
                and message.get("role") in {"system", "developer"}
                and (content := _content_to_text(message.get("content")).strip())
            ]
        )
        if is_oauth:
            system_blocks.insert(
                0,
                {
                    "type": "text",
                    "text": "You are Claude Code, Anthropic's official CLI for Claude.",
                    **({"cache_control": cache_control} if cache_control else {}),
                },
            )
        if system_blocks:
            body["system"] = system_blocks
        thinking_enabled = (
            isinstance(reasoning_config, dict)
            and reasoning_config.get("enabled", True) is not False
            and str(reasoning_config.get("effort") or "").strip().lower() not in {"", "none", "off"}
        )
        if (
            temperature is not None
            and not thinking_enabled
            and compat.get("supportsTemperature") is not False
            and profile.fixed_temperature is not OMIT_TEMPERATURE
        ):
            body["temperature"] = profile.fixed_temperature if profile.fixed_temperature is not None else temperature
        supports_eager_input = compat.get("supportsEagerToolInputStreaming") is not False
        if native_context is not None and (immediate_tools or deferred_tools):
            body["tools"] = [
                *_anthropic_native_tools(
                    immediate_tools,
                    cache_control if compat.get("supportsCacheControlOnTools") is not False else None,
                    eager_input_streaming=supports_eager_input,
                    normalize_tool_name=normalize_tool_name,
                ),
                *_anthropic_native_tools(
                    deferred_tools,
                    None,
                    eager_input_streaming=supports_eager_input,
                    normalize_tool_name=normalize_tool_name,
                    defer_loading=True,
                ),
            ]
        elif tools:
            body["tools"] = self.convert_tools(tools)
        if native_model is not None and native_model.reasoning and isinstance(reasoning_config, dict):
            if not thinking_enabled:
                if _anthropic_allows_disabled_thinking(native_model):
                    body["thinking"] = {"type": "disabled"}
            elif compat.get("forceAdaptiveThinking") is True:
                effort = str(reasoning_config.get("effort") or "medium").strip().lower()
                mapped = "low" if effort in {"minimal", "low"} else effort
                if mapped not in {"low", "medium", "high", "xhigh", "max"}:
                    mapped = "high"
                body["thinking"] = {"type": "adaptive", "display": "summarized"}
                body["output_config"] = {"effort": mapped}
            else:
                if int(body["max_tokens"]) < 2048:
                    raise ValueError(
                        "Anthropic manual thinking requires max_tokens >= 2048 "
                        "to preserve the 1024-token minimum thinking budget and response reserve."
                    )
                effort = str(reasoning_config.get("effort") or "medium").strip().lower()
                budget = {
                    "minimal": 1024,
                    "low": 2048,
                    "medium": 8192,
                    "high": 16384,
                    "xhigh": 16384,
                    "max": 16384,
                }.get(effort, 1024)
                budget = min(budget, max(0, int(body["max_tokens"]) - 1024))
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                    "display": "summarized",
                }
        beta_features: list[str] = []
        if native_context is not None and native_context.tools and not supports_eager_input:
            beta_features.append("fine-grained-tool-streaming-2025-05-14")
        if thinking_enabled and compat.get("forceAdaptiveThinking") is not True:
            beta_features.append("interleaved-thinking-2025-05-14")
        if beta_features:
            body["extra_headers"] = {
                "accept": "application/json",
                "anthropic-dangerous-direct-browser-access": "true",
                "anthropic-beta": ",".join(beta_features),
            }
        if is_oauth:
            oauth_betas = ["claude-code-20250219", "oauth-2025-04-20", *beta_features]
            body["extra_headers"] = {
                "accept": "application/json",
                "anthropic-dangerous-direct-browser-access": "true",
                "anthropic-beta": ",".join(oauth_betas),
                "user-agent": "claude-cli/2.1.75",
                "x-app": "cli",
            }
        elif session_id and compat.get("sendSessionAffinityHeaders") is True:
            body.setdefault("extra_headers", {})["x-session-affinity"] = session_id
        if isinstance(metadata, dict) and isinstance(metadata.get("user_id"), str):
            body["metadata"] = {"user_id": metadata["user_id"]}
        if tool_choice is not None:
            body["tool_choice"] = {"type": tool_choice} if isinstance(tool_choice, str) else tool_choice
        if request_overrides:
            body.update(request_overrides)
        _apply_anthropic_wire_compatibility(
            body,
            compat=compat,
            thinking_enabled=thinking_enabled,
        )
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


def _codex_instructions(
    context: Context | None,
    messages: list[dict[str, Any]],
) -> str:
    if context is not None and isinstance(context.system_prompt, str) and context.system_prompt.strip():
        return context.system_prompt
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
            continue
        content = _content_to_text(message.get("content"))
        if content.strip():
            return content
    return "You are a helpful assistant."


class CodexResponsesTransport:
    api = "openai-codex-responses"
    api_mode = "openai_codex_responses"
    endpoint_path = "/responses"

    @staticmethod
    def build_url(
        base_url: str,
        _model: str,
        _options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = (base_url or "https://chatgpt.com/backend-api").rstrip("/")
        if normalized.endswith("/codex/responses"):
            return normalized
        if normalized.endswith("/codex"):
            return normalized + "/responses"
        return normalized + "/codex/responses"

    @staticmethod
    def finalize_headers(
        headers: Mapping[str, str],
        *,
        api_key: str | None,
        session_id: str | None,
        **_kwargs: Any,
    ) -> dict[str, str]:
        if not api_key:
            raise ValueError("No API key for provider: openai-codex")
        from travis.ai.providers.codex_auth import build_codex_sse_headers

        return build_codex_sse_headers(headers, api_key, session_id)

    def convert_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        include_system: bool = False,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "system":
                if include_system:
                    converted.append({"role": "system", "content": _content_to_text(message.get("content"))})
                continue
            if role == "developer":
                if include_system:
                    converted.append({"role": "developer", "content": _content_to_text(message.get("content"))})
                continue
            if role == "user":
                converted.append({"role": "user", "content": _openai_content_to_responses(message.get("content"))})
                continue
            if role == "assistant":
                for reasoning_item in message.get("codex_reasoning_items") or []:
                    if isinstance(reasoning_item, dict) and reasoning_item.get("type") == "reasoning":
                        converted.append(copy.deepcopy(reasoning_item))
                output_text = _openai_content_to_responses(message.get("content"), output=True)
                if output_text:
                    converted.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": output_text,
                            "status": "completed",
                        }
                    )
                for tool_call in message.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    function = _tool_function(tool_call)
                    name = str(function.get("name") or "")
                    call_id, item_id = _split_responses_tool_call_id(str(tool_call.get("id") or ""))
                    item: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(
                            _tool_arguments(function.get("arguments") or "{}", name),
                            separators=(",", ":"),
                        ),
                    }
                    if item_id:
                        item["id"] = item_id
                    converted.append(item)
                continue
            if role == "tool":
                call_id, _item_id = _split_responses_tool_call_id(str(message.get("tool_call_id") or ""))
                converted.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _content_to_text(message.get("content")),
                    }
                )
        return converted

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            function = _tool_function(tool)
            name = str(function.get("name") or "")
            if not name:
                continue
            converted.append(
                {
                    "type": "function",
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
                    "strict": None,
                }
            )
        return converted

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        session_id: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        reasoning_summary: str | None = None,
        service_tier: str | None = None,
        text_verbosity: str | None = None,
        tool_choice: Any | None = None,
        request_overrides: dict[str, Any] | None = None,
        context: Context | None = None,
        target_model: Any = None,
        model_compat: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        instructions = _codex_instructions(context, messages)
        immediate_tools: list[Any] = []
        deferred_tools: dict[str, Any] = {}
        if context is not None and target_model is not None:
            immediate_tools, deferred_tools = split_deferred_tools(
                context,
                (model_compat or {}).get("supportsToolSearch") is True,
            )
            converted_input = convert_responses_messages(
                target_model,
                context,
                {"openai", "openai-codex", "opencode"},
                include_system_prompt=False,
                deferred_tools=deferred_tools,
            )
        else:
            converted_input = self.convert_messages(messages)
        body: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": stream,
            "instructions": instructions,
            "input": converted_input,
            "text": {"verbosity": text_verbosity or "low"},
            "include": ["reasoning.encrypted_content"],
            "tool_choice": tool_choice if tool_choice is not None else "auto",
            "parallel_tool_calls": True,
        }
        if service_tier is not None:
            body["service_tier"] = service_tier
        if session_id:
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
        if immediate_tools:
            body["tools"] = convert_responses_tools(immediate_tools, strict=None)
        elif context is None and tools:
            body["tools"] = self.convert_tools(tools)
        if isinstance(reasoning_config, dict):
            effort = str(reasoning_config.get("effort") or "").strip().lower()
            if reasoning_config.get("enabled") is False or effort == "none":
                effort = "none"
            if effort:
                body["reasoning"] = {"effort": effort, "summary": reasoning_summary or "auto"}
        if request_overrides:
            body.update(request_overrides)
        for unsupported_field in ("temperature", "top_p", "max_output_tokens"):
            body.pop(unsupported_field, None)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class OpenAIResponsesTransport(CodexResponsesTransport):
    api = "openai-responses"
    api_mode = "openai_responses"

    @staticmethod
    def finalize_headers(
        headers: Mapping[str, str],
        **_kwargs: Any,
    ) -> dict[str, str]:
        return dict(headers)

    @staticmethod
    def build_url(
        base_url: str,
        _model: str,
        _options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = base_url.rstrip("/")
        return normalized if normalized.endswith("/responses") else normalized + "/responses"

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = super().convert_tools(tools)
        for tool in converted:
            tool["strict"] = False
        return converted

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        session_id: str | None = None,
        cache_retention: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        reasoning_summary: str | None = None,
        service_tier: str | None = None,
        tool_choice: Any | None = None,
        request_overrides: dict[str, Any] | None = None,
        model_compat: dict[str, Any] | None = None,
        model_reasoning: bool = False,
        model_thinking_level_map: dict[str, str | None] | None = None,
        context: Context | None = None,
        target_model: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        resolved_cache_retention = cache_retention or "short"
        supports_long = (model_compat or {}).get("supportsLongCacheRetention") is not False
        immediate_tools: list[Any] = []
        deferred_tools: dict[str, Any] = {}
        if context is not None and target_model is not None:
            immediate_tools, deferred_tools = split_deferred_tools(
                context,
                (model_compat or {}).get("supportsToolSearch") is True,
            )
            converted_input = convert_responses_messages(
                target_model,
                context,
                {"openai", "openai-codex", "opencode"},
                deferred_tools=deferred_tools,
            )
        else:
            converted_input = self.convert_messages(messages, include_system=True)
        body: dict[str, Any] = {
            "model": model,
            "input": converted_input,
            "stream": stream,
            "store": False,
        }
        if session_id and resolved_cache_retention != "none" and target_model is not None:
            from travis.ai.providers.openai_compat import resolve_openai_compat

            compat = resolve_openai_compat(target_model)
            if compat.session_affinity_format == "openrouter":
                body["extra_headers"] = {"x-session-id": session_id}
            else:
                affinity_headers = {"x-client-request-id": session_id}
                if compat.session_affinity_format == "openai":
                    affinity_headers["session_id"] = session_id
                body["extra_headers"] = affinity_headers
        if session_id and resolved_cache_retention != "none":
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
        if resolved_cache_retention == "long" and supports_long:
            body["prompt_cache_retention"] = "24h"
        if max_tokens is not None:
            body["max_output_tokens"] = max(max_tokens, 16)
        if temperature is not None:
            body["temperature"] = temperature
        if service_tier is not None:
            body["service_tier"] = service_tier
        if immediate_tools:
            body["tools"] = convert_responses_tools(immediate_tools)
        elif context is None and tools:
            body["tools"] = self.convert_tools(tools)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if model_reasoning:
            config = reasoning_config or {}
            enabled = config.get("enabled", True) is not False
            effort = str(config.get("effort") or "").strip().lower()
            if enabled and ((effort and effort != "none") or reasoning_summary):
                selected_effort = effort if effort and effort != "none" else "medium"
                mapped = (model_thinking_level_map or {}).get(selected_effort, selected_effort)
                body["reasoning"] = {"effort": mapped, "summary": reasoning_summary or "auto"}
                body["include"] = ["reasoning.encrypted_content"]
            elif (model_thinking_level_map or {}).get("off", "none") is not None:
                body["reasoning"] = {"effort": (model_thinking_level_map or {}).get("off", "none")}
        if request_overrides:
            body.update(request_overrides)
        return body


class AzureOpenAIResponsesTransport(OpenAIResponsesTransport):
    api = "azure-openai-responses"
    api_mode = "azure_openai_responses"

    @staticmethod
    def _resolve_base_url(base_url: str, options: object | None) -> str:
        explicit = str(getattr(options, "azure_base_url", None) or os.environ.get("AZURE_OPENAI_BASE_URL") or "").strip()
        resource = str(
            getattr(options, "azure_resource_name", None)
            or os.environ.get("AZURE_OPENAI_RESOURCE_NAME")
            or ""
        ).strip()
        raw = explicit or (f"https://{resource}.openai.azure.com/openai/v1" if resource else base_url)
        parsed = urlsplit(raw.strip().rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid Azure OpenAI base URL: {raw}")
        hostname = (parsed.hostname or "").lower()
        is_azure_host = hostname.endswith(
            (".openai.azure.com", ".cognitiveservices.azure.com", ".ai.azure.com")
        )
        path = parsed.path.rstrip("/")
        if is_azure_host and path in {"", "/openai", "/openai/v1/responses"}:
            return urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1", "", ""))
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))

    @classmethod
    def build_url(
        cls,
        base_url: str,
        _model: str,
        options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = cls._resolve_base_url(base_url, options)
        parsed = urlsplit(normalized)
        path = parsed.path if parsed.path.endswith("/responses") else parsed.path.rstrip("/") + "/responses"
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault(
            "api-version",
            str(getattr(options, "azure_api_version", None) or os.environ.get("AZURE_OPENAI_API_VERSION") or "v1"),
        )
        return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))

    @staticmethod
    def finalize_headers(
        headers: Mapping[str, str],
        *,
        api_key: str | None,
        **_kwargs: Any,
    ) -> dict[str, str]:
        resolved = {key: value for key, value in headers.items() if key.lower() != "authorization"}
        if not api_key:
            raise ValueError("No API key for provider: azure-openai-responses")
        for key in tuple(resolved):
            if key.lower() == "api-key":
                del resolved[key]
        resolved["api-key"] = api_key
        return resolved

    def build_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        session_id = kwargs.get("session_id")
        options = kwargs.get("options")
        model_id = str(kwargs.get("model") or "")
        deployment_name = str(getattr(options, "azure_deployment_name", None) or "").strip()
        if not deployment_name:
            mappings = str(os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME_MAP") or "")
            for entry in mappings.split(","):
                source, separator, target = entry.strip().partition("=")
                if separator and source.strip() == model_id and target.strip():
                    deployment_name = target.strip()
                    break
        kwargs["model"] = deployment_name or model_id
        body = super().build_kwargs(**kwargs)
        body.pop("extra_headers", None)
        body.pop("prompt_cache_retention", None)
        body.pop("service_tier", None)
        body.pop("tool_choice", None)
        if session_id:
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(str(session_id))
        return body


def get_transport(api_mode: str):
    """Compatibility wrapper for the registry-owned lookup function."""

    from travis.ai.providers.transport_registry import get_transport as registry_get_transport

    return registry_get_transport(api_mode)
