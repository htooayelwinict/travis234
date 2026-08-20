"""Amazon Bedrock ConverseStream transport family."""

from __future__ import annotations

import base64
import os
import re
from typing import Any
from urllib.parse import quote

from travis.ai.providers.base import NormalizedResponse, ProviderProfile
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
    UserMessage,
)


def _bedrock_supports_cache(model: Model) -> bool:
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


def _bedrock_image(block: ImageContent) -> dict[str, object]:
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


def _bedrock_user_message(message: UserMessage) -> dict[str, object]:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    blocks: list[dict[str, object]] = []
    if isinstance(message.content, str):
        text = _sanitize_surrogates(message.content)
        blocks.append({"text": text if text.strip() else "<empty>"})
    else:
        for block in message.content:
            if isinstance(block, TextContent) and block.text.strip():
                blocks.append({"text": _sanitize_surrogates(block.text)})
            elif isinstance(block, ImageContent):
                blocks.append(_bedrock_image(block))
    return {"role": "user", "content": blocks or [{"text": "<empty>"}]}


def _bedrock_assistant_message(
    message: AssistantMessage,
    model: Model,
) -> dict[str, object] | None:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    blocks: list[dict[str, object]] = []
    is_claude = "claude" in model.id.lower() or "claude" in model.name.lower()
    for block in message.content:
        if isinstance(block, TextContent) and block.text.strip():
            blocks.append({"text": _sanitize_surrogates(block.text)})
        elif isinstance(block, ToolCall):
            blocks.append(
                {
                    "toolUse": {
                        "toolUseId": block.id[:64],
                        "name": block.name,
                        "input": block.arguments,
                    }
                }
            )
        elif isinstance(block, ThinkingContent) and block.thinking.strip():
            thinking = _sanitize_surrogates(block.thinking)
            if is_claude and not block.thinking_signature:
                blocks.append({"text": thinking})
            else:
                reasoning_text: dict[str, object] = {"text": thinking}
                if is_claude:
                    reasoning_text["signature"] = block.thinking_signature
                blocks.append({"reasoningContent": {"reasoningText": reasoning_text}})
    return {"role": "assistant", "content": blocks} if blocks else None


def _bedrock_tool_result_block(message: ToolResultMessage) -> dict[str, object]:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    content: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextContent) and block.text.strip():
            content.append({"text": _sanitize_surrogates(block.text)})
        elif isinstance(block, ImageContent):
            content.append(_bedrock_image(block))
    return {
        "toolResult": {
            "toolUseId": message.tool_call_id[:64],
            "content": content or [{"text": "<empty>"}],
            "status": "error" if message.is_error else "success",
        }
    }


def _bedrock_tool_result_message(
    transformed: list[Message],
    start_index: int,
) -> tuple[dict[str, object], int]:
    results: list[dict[str, object]] = []
    index = start_index
    while index < len(transformed):
        result = transformed[index]
        if not isinstance(result, ToolResultMessage):
            break
        results.append(_bedrock_tool_result_block(result))
        index += 1
    return {"role": "user", "content": results}, index


def _append_bedrock_cache_point(
    messages: list[dict[str, object]],
    model: Model,
    cache_retention: str,
) -> None:
    if cache_retention == "none" or not _bedrock_supports_cache(model) or not messages:
        return
    last = messages[-1]
    content = last.get("content")
    if last.get("role") != "user" or not isinstance(content, list):
        return
    cache_point: dict[str, object] = {"type": "default"}
    if cache_retention == "long":
        cache_point["ttl"] = "1h"
    content.append({"cachePoint": cache_point})


def _bedrock_messages(
    context: Context,
    model: Model,
    cache_retention: str,
) -> list[dict[str, object]]:
    from travis.ai.providers.message_translation import _transform_messages

    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _model, _source: re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64],
    )
    messages: list[dict[str, object]] = []
    index = 0
    while index < len(transformed):
        message = transformed[index]
        if isinstance(message, UserMessage):
            messages.append(_bedrock_user_message(message))
            index += 1
        elif isinstance(message, AssistantMessage):
            converted = _bedrock_assistant_message(message, model)
            if converted is not None:
                messages.append(converted)
            index += 1
        else:
            converted, index = _bedrock_tool_result_message(transformed, index)
            messages.append(converted)
    _append_bedrock_cache_point(messages, model, cache_retention)
    return messages


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


__all__ = ["BedrockConverseStreamTransport"]
