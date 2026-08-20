"""Anthropic Messages transport family."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from travis.ai.providers.base import OMIT_TEMPERATURE, NormalizedResponse, ProviderProfile
from travis.ai.providers.responses_translation import split_deferred_tools
from travis.ai.providers.transport_families._shared import (
    content_to_text as _content_to_text,
    tool_arguments as _tool_arguments,
    tool_function as _tool_function,
)
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

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


def _anthropic_default_supports_tool_references(model: Model) -> bool:
    if model.provider != "anthropic" or "haiku" in model.id:
        return False
    match = re.match(r"^claude-(?:opus|sonnet|fable)-(\d+)(?:-(\d+))?(?:-|$)", model.id)
    if match is None:
        return False
    major = int(match.group(1))
    minor_text = match.group(2)
    minor = int(minor_text) if minor_text and len(minor_text) < 8 else 0
    return major > 4 or (major == 4 and minor >= 5)
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
def _anthropic_image_block(block: ImageContent) -> dict[str, object]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": block.mime_type,
            "data": block.data,
        },
    }


def _anthropic_user_message(message: UserMessage) -> dict[str, object] | None:
    if isinstance(message.content, str):
        return (
            {"role": "user", "content": message.content}
            if message.content.strip()
            else None
        )
    blocks: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextContent) and block.text.strip():
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            blocks.append(_anthropic_image_block(block))
    return {"role": "user", "content": blocks} if blocks else None


def _anthropic_thinking_block(
    block: ThinkingContent,
    allow_empty_signature: bool,
) -> dict[str, object] | None:
    if block.redacted and block.thinking_signature:
        return {"type": "redacted_thinking", "data": block.thinking_signature}
    if not block.thinking.strip():
        return None
    if block.thinking_signature:
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.thinking_signature,
        }
    if allow_empty_signature:
        return {"type": "thinking", "thinking": block.thinking, "signature": ""}
    return {"type": "text", "text": block.thinking}


def _anthropic_assistant_message(
    message: AssistantMessage,
    allow_empty_signature: bool,
    normalize_tool_name: Callable[[str], str],
) -> dict[str, object] | None:
    blocks: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextContent) and block.text.strip():
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent):
            thinking = _anthropic_thinking_block(block, allow_empty_signature)
            if thinking is not None:
                blocks.append(thinking)
        elif isinstance(block, ToolCall):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": normalize_tool_name(block.name),
                    "input": block.arguments or {},
                }
            )
    return {"role": "assistant", "content": blocks} if blocks else None


def _anthropic_result_content(
    result: ToolResultMessage,
) -> str | list[dict[str, object]]:
    content: list[dict[str, object]] = []
    for block in result.content:
        if isinstance(block, TextContent) and block.text.strip():
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            content.append(_anthropic_image_block(block))
    has_images = any(isinstance(block, ImageContent) for block in result.content)
    if has_images and not any(part.get("type") == "text" for part in content):
        content.insert(0, {"type": "text", "text": "(see attached image)"})
    if has_images:
        return content
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


def _anthropic_tool_references(
    result: ToolResultMessage,
    deferred_tool_names: set[str],
    loaded_tool_names: set[str],
    normalize_tool_name: Callable[[str], str],
) -> list[dict[str, object]]:
    references: list[dict[str, object]] = []
    for name in result.added_tool_names or []:
        normalized_name = normalize_tool_name(name)
        if normalized_name not in deferred_tool_names or normalized_name in loaded_tool_names:
            continue
        loaded_tool_names.add(normalized_name)
        references.append(
            {"type": "tool_reference", "tool_name": normalize_tool_name(name)}
        )
    return references


def _anthropic_tool_result_message(
    transformed: list[Message],
    start_index: int,
    deferred_tool_names: set[str],
    loaded_tool_names: set[str],
    normalize_tool_name: Callable[[str], str],
) -> tuple[dict[str, object], int]:
    results: list[dict[str, object]] = []
    sibling_content: list[dict[str, object]] = []
    index = start_index
    while index < len(transformed):
        result = transformed[index]
        if not isinstance(result, ToolResultMessage):
            break
        converted_content = _anthropic_result_content(result)
        references = _anthropic_tool_references(
            result,
            deferred_tool_names,
            loaded_tool_names,
            normalize_tool_name,
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
    return {"role": "user", "content": [*results, *sibling_content]}, index


def _apply_anthropic_message_cache_control(
    messages: list[dict[str, object]],
    cache_control: dict[str, str] | None,
) -> None:
    if not cache_control or not messages or messages[-1].get("role") != "user":
        return
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
        return
    if not isinstance(content, list) or not content:
        return
    final_block = content[-1]
    if isinstance(final_block, dict) and final_block.get("type") in {
        "text",
        "image",
        "tool_result",
    }:
        final_block["cache_control"] = cache_control


def _anthropic_native_messages(
    context: Context,
    model: Model,
    cache_control: dict[str, str] | None,
    *,
    allow_empty_signature: bool = False,
    deferred_tool_names: set[str] | None = None,
    normalize_tool_name: Callable[[str], str] = lambda name: name,
) -> list[dict[str, object]]:
    from travis.ai.providers.message_translation import _transform_messages

    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _model, _source: re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64],
    )
    messages: list[dict[str, object]] = []
    loaded_tool_names: set[str] = set()
    resolved_deferred_names = deferred_tool_names or set()
    index = 0
    while index < len(transformed):
        message = transformed[index]
        if isinstance(message, UserMessage):
            converted = _anthropic_user_message(message)
            if converted is not None:
                messages.append(converted)
            index += 1
        elif isinstance(message, AssistantMessage):
            converted = _anthropic_assistant_message(
                message,
                allow_empty_signature,
                normalize_tool_name,
            )
            if converted is not None:
                messages.append(converted)
            index += 1
        else:
            converted, index = _anthropic_tool_result_message(
                transformed,
                index,
                resolved_deferred_names,
                loaded_tool_names,
                normalize_tool_name,
            )
            messages.append(converted)
    _apply_anthropic_message_cache_control(messages, cache_control)
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


def _anthropic_cache_control(
    retention: str | None,
    *,
    supports_long: bool,
) -> dict[str, str] | None:
    resolved = retention or "short"
    if resolved == "none":
        return None
    cache_control = {"type": "ephemeral"}
    if resolved == "long" and supports_long:
        cache_control["ttl"] = "1h"
    return cache_control


def _anthropic_thinking_enabled(reasoning_config: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(reasoning_config, dict)
        and reasoning_config.get("enabled", True) is not False
        and str(reasoning_config.get("effort") or "").strip().lower()
        not in {"", "none", "off"}
    )


def _anthropic_sampling_fields(
    *,
    temperature: float | None,
    thinking_enabled: bool,
    supports_temperature: bool,
    fixed_temperature: object,
) -> dict[str, Any]:
    if (
        temperature is None
        or thinking_enabled
        or not supports_temperature
        or fixed_temperature is OMIT_TEMPERATURE
    ):
        return {}
    return {
        "temperature": fixed_temperature if fixed_temperature is not None else temperature
    }


def _anthropic_system_blocks(
    *,
    messages: list[dict[str, Any]],
    context: Context | None,
    cache_control: dict[str, Any] | None,
    is_oauth: bool,
) -> list[dict[str, Any]]:
    system_text = context.system_prompt if context is not None else None
    system_blocks = (
        [
            {
                "type": "text",
                "text": system_text,
                **({"cache_control": cache_control} if cache_control else {}),
            }
        ]
        if system_text
        else [
            {
                "type": "text",
                "text": content,
                **({"cache_control": cache_control} if cache_control else {}),
            }
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
    return system_blocks


def _anthropic_tool_fields(
    *,
    immediate_tools: list[Any],
    deferred_tools: list[Any],
    fallback_tools: list[dict[str, Any]] | None,
    cache_control: dict[str, Any] | None,
    supports_cache_control: bool,
    supports_eager_input: bool,
    normalize_tool_name: Callable[[str], str],
) -> dict[str, Any]:
    if immediate_tools or deferred_tools:
        return {
            "tools": [
                *_anthropic_native_tools(
                    immediate_tools,
                    cache_control if supports_cache_control else None,
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
        }
    return {"tools": fallback_tools} if fallback_tools else {}


def _anthropic_thinking_fields(
    *,
    target_model: Any,
    reasoning_config: dict[str, Any] | None,
    max_tokens: int,
    force_adaptive: bool,
) -> dict[str, Any]:
    if (
        target_model is None
        or not target_model.reasoning
        or not isinstance(reasoning_config, dict)
    ):
        return {}
    thinking_enabled = _anthropic_thinking_enabled(reasoning_config)
    if not thinking_enabled:
        return (
            {"thinking": {"type": "disabled"}}
            if _anthropic_allows_disabled_thinking(target_model)
            else {}
        )
    effort = str(reasoning_config.get("effort") or "medium").strip().lower()
    if force_adaptive:
        mapped = "low" if effort in {"minimal", "low"} else effort
        if mapped not in {"low", "medium", "high", "xhigh", "max"}:
            mapped = "high"
        return {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": mapped},
        }
    if max_tokens < 2048:
        raise ValueError(
            "Anthropic manual thinking requires max_tokens >= 2048 "
            "to preserve the 1024-token minimum thinking budget and response reserve."
        )
    budget = {
        "minimal": 1024,
        "low": 2048,
        "medium": 8192,
        "high": 16384,
        "xhigh": 16384,
        "max": 16384,
    }.get(effort, 1024)
    return {
        "thinking": {
            "type": "enabled",
            "budget_tokens": min(budget, max(0, max_tokens - 1024)),
            "display": "summarized",
        }
    }


def _anthropic_native_tool_sets(
    context: Context | None,
    model: Model | None,
    compat: Mapping[str, object],
    normalize_tool_name: Callable[[str], str],
) -> tuple[list[Tool], list[Tool], set[str]]:
    if context is None or model is None:
        return [], [], set()
    supports_references = compat.get("supportsToolReferences")
    if supports_references is None:
        supports_references = _anthropic_default_supports_tool_references(model)
    immediate_tools, deferred_by_name = split_deferred_tools(
        context,
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
    return immediate_tools, deferred_tools, deferred_tool_names


def _restore_oauth_tool_names(messages: list[dict[str, object]]) -> None:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if isinstance(name, str):
                block["name"] = _claude_code_tool_name(name)


def _resolve_anthropic_max_tokens(
    *,
    omit_max_tokens: bool,
    max_tokens: int | None,
    profile: ProviderProfile,
    model_id: str,
    native_model: Model | None,
    native_context: Context | None,
) -> int:
    if not omit_max_tokens:
        return max_tokens if max_tokens is not None else profile.get_max_tokens(model_id) or 4096
    native_ceiling = (
        (native_model.max_tokens if native_model is not None else 0)
        or profile.get_max_tokens(model_id)
        or 4096
    )
    if native_context is None or native_model is None:
        return native_ceiling
    from travis.ai.context_estimate import clamp_max_tokens_to_context

    return clamp_max_tokens_to_context(native_model, native_context, native_ceiling)


def _anthropic_beta_features(
    *,
    native_context: Context | None,
    thinking_enabled: bool,
    supports_eager_input: bool,
    force_adaptive: bool,
) -> list[str]:
    features: list[str] = []
    if native_context is not None and native_context.tools and not supports_eager_input:
        features.append("fine-grained-tool-streaming-2025-05-14")
    if thinking_enabled and not force_adaptive:
        features.append("interleaved-thinking-2025-05-14")
    return features


def _anthropic_request_headers(
    *,
    beta_features: list[str],
    is_oauth: bool,
    session_id: str | None,
    send_session_affinity: bool,
) -> dict[str, str] | None:
    headers: dict[str, str] = {}
    if beta_features:
        headers = {
            "accept": "application/json",
            "anthropic-dangerous-direct-browser-access": "true",
            "anthropic-beta": ",".join(beta_features),
        }
    if is_oauth:
        oauth_betas = ["claude-code-20250219", "oauth-2025-04-20", *beta_features]
        return {
            "accept": "application/json",
            "anthropic-dangerous-direct-browser-access": "true",
            "anthropic-beta": ",".join(oauth_betas),
            "user-agent": "claude-cli/2.1.75",
            "x-app": "cli",
        }
    if session_id and send_session_affinity:
        headers["x-session-affinity"] = session_id
    return headers or None






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
        target_model: Model | None = None,
        model_compat: dict[str, Any] | None = None,
        api_key: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        compat = model_compat or {}
        is_oauth = isinstance(api_key, str) and "sk-ant-oat" in api_key
        normalize_tool_name = _claude_code_tool_name if is_oauth else (lambda name: name)
        cache_control = _anthropic_cache_control(
            cache_retention,
            supports_long=compat.get("supportsLongCacheRetention") is not False,
        )
        native_context = context
        native_model = target_model
        immediate_tools, deferred_tools, deferred_tool_names = _anthropic_native_tool_sets(
            native_context,
            native_model,
            compat,
            normalize_tool_name,
        )
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
            _restore_oauth_tool_names(converted_messages)
        resolved_max_tokens = _resolve_anthropic_max_tokens(
            omit_max_tokens=omit_max_tokens,
            max_tokens=max_tokens,
            profile=profile,
            model_id=model,
            native_model=native_model,
            native_context=native_context,
        )
        body: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": resolved_max_tokens,
            "stream": stream,
        }
        system_blocks = _anthropic_system_blocks(
            messages=messages,
            context=native_context,
            cache_control=cache_control,
            is_oauth=is_oauth,
        )
        if system_blocks:
            body["system"] = system_blocks
        thinking_enabled = _anthropic_thinking_enabled(reasoning_config)
        body.update(
            _anthropic_sampling_fields(
                temperature=temperature,
                thinking_enabled=thinking_enabled,
                supports_temperature=compat.get("supportsTemperature") is not False,
                fixed_temperature=profile.fixed_temperature,
            )
        )
        supports_eager_input = compat.get("supportsEagerToolInputStreaming") is not False
        body.update(
            _anthropic_tool_fields(
                immediate_tools=immediate_tools,
                deferred_tools=deferred_tools,
                fallback_tools=self.convert_tools(tools) if tools else None,
                cache_control=cache_control,
                supports_cache_control=compat.get("supportsCacheControlOnTools") is not False,
                supports_eager_input=supports_eager_input,
                normalize_tool_name=normalize_tool_name,
            )
        )
        body.update(
            _anthropic_thinking_fields(
                target_model=native_model,
                reasoning_config=reasoning_config,
                max_tokens=int(body["max_tokens"]),
                force_adaptive=compat.get("forceAdaptiveThinking") is True,
            )
        )
        beta_features = _anthropic_beta_features(
            native_context=native_context,
            thinking_enabled=thinking_enabled,
            supports_eager_input=supports_eager_input,
            force_adaptive=compat.get("forceAdaptiveThinking") is True,
        )
        request_headers = _anthropic_request_headers(
            beta_features=beta_features,
            is_oauth=is_oauth,
            session_id=session_id,
            send_session_affinity=compat.get("sendSessionAffinityHeaders") is True,
        )
        if request_headers:
            body["extra_headers"] = request_headers
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


__all__ = ["AnthropicMessagesTransport"]
