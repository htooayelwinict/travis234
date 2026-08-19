"""Amazon Bedrock ConverseStream transport family."""

from __future__ import annotations

import base64
import os
import re
from typing import Any
from urllib.parse import quote

from travis.ai.providers.base import NormalizedResponse, ProviderProfile
from travis.ai.types import AssistantMessage, Context, ImageContent, TextContent, ThinkingContent, ToolCall, ToolResultMessage

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
            while index < len(transformed):
                result = transformed[index]
                if not isinstance(result, ToolResultMessage):
                    break
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
