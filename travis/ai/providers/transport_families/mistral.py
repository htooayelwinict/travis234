"""Mistral conversations transport family."""

from __future__ import annotations

import json
import re
from typing import Any

from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.responses_translation import short_hash
from travis.ai.providers.transport_families.chat_completions import ChatCompletionsTransport
from travis.ai.types import AssistantMessage, Context, ImageContent, TextContent, ThinkingContent, ToolCall, ToolResultMessage

def _mistral_tool_call_id_normalizer():
    by_original: dict[str, str] = {}
    by_normalized: dict[str, str] = {}

    def normalize(tool_call_id: str, _model=None, _source=None) -> str:
        existing = by_original.get(tool_call_id)
        if existing:
            return existing
        normalized = re.sub(r"[^a-zA-Z0-9]", "", tool_call_id)
        attempt = 0
        while True:
            if attempt == 0 and len(normalized) == 9:
                candidate = normalized
            else:
                seed_base = normalized or tool_call_id
                seed = seed_base if attempt == 0 else f"{seed_base}:{attempt}"
                candidate = re.sub(r"[^a-zA-Z0-9]", "", short_hash(seed))[:9]
            owner = by_normalized.get(candidate)
            if owner is None or owner == tool_call_id:
                by_original[tool_call_id] = candidate
                by_normalized[candidate] = tool_call_id
                return candidate
            attempt += 1

    return normalize


def _mistral_tool_result_text(message: ToolResultMessage, supports_images: bool) -> str:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    text = "\n".join(
        _sanitize_surrogates(block.text)
        for block in message.content
        if isinstance(block, TextContent)
    ).strip()
    has_images = any(isinstance(block, ImageContent) for block in message.content)
    prefix = "[tool error] " if message.is_error else ""
    if text:
        suffix = "\n[tool image omitted: model does not support images]" if has_images and not supports_images else ""
        return f"{prefix}{text}{suffix}"
    if has_images:
        if supports_images:
            return f"{prefix}(see attached image)"
        return f"{prefix}(image omitted: model does not support images)"
    return f"{prefix}(no tool output)"


def _mistral_messages(context: Context, model: Any) -> list[dict[str, Any]]:
    from travis.ai.providers.message_translation import _sanitize_surrogates, _transform_messages

    normalize_id = _mistral_tool_call_id_normalizer()
    transformed = _transform_messages(context.messages, model, normalize_id)
    supports_images = "image" in model.input
    messages: list[dict[str, Any]] = []
    for message in transformed:
        if message.role == "user":
            if isinstance(message.content, str):
                messages.append({"role": "user", "content": _sanitize_surrogates(message.content)})
                continue
            had_images = any(isinstance(block, ImageContent) for block in message.content)
            content: list[dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, TextContent):
                    content.append({"type": "text", "text": _sanitize_surrogates(block.text)})
                elif isinstance(block, ImageContent) and supports_images:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": f"data:{block.mime_type};base64,{block.data}",
                        }
                    )
            if content:
                messages.append({"role": "user", "content": content})
            elif had_images:
                messages.append(
                    {"role": "user", "content": "(image omitted: model does not support images)"}
                )
            continue
        if isinstance(message, AssistantMessage):
            content: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, TextContent) and block.text.strip():
                    content.append({"type": "text", "text": _sanitize_surrogates(block.text)})
                elif isinstance(block, ThinkingContent) and block.thinking.strip():
                    content.append(
                        {
                            "type": "thinking",
                            "thinking": [
                                {"type": "text", "text": _sanitize_surrogates(block.thinking)}
                            ],
                        }
                    )
                elif isinstance(block, ToolCall):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.arguments or {}, separators=(",", ":")),
                            },
                        }
                    )
            if content or tool_calls:
                item: dict[str, Any] = {"role": "assistant"}
                if content:
                    item["content"] = content
                if tool_calls:
                    item["tool_calls"] = tool_calls
                messages.append(item)
            continue
        if isinstance(message, ToolResultMessage):
            tool_content: list[dict[str, Any]] = [
                {"type": "text", "text": _mistral_tool_result_text(message, supports_images)}
            ]
            if supports_images:
                for block in message.content:
                    if isinstance(block, ImageContent):
                        tool_content.append(
                            {
                                "type": "image_url",
                                "image_url": f"data:{block.mime_type};base64,{block.data}",
                            }
                        )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "name": message.tool_name,
                    "content": tool_content,
                }
            )
    return messages


class MistralConversationsTransport(ChatCompletionsTransport):
    api = "mistral-conversations"
    api_mode = "mistral_conversations"
    endpoint_path = "/chat/completions"

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
        tool_choice: Any | None = None,
        session_id: str | None = None,
        cache_retention: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        context: Context | None = None,
        target_model: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        prepared_messages = (
            _mistral_messages(context, target_model)
            if context is not None and target_model is not None
            else self.convert_messages(messages, model=model)
        )
        if context is not None and context.system_prompt:
            from travis.ai.providers.message_translation import _sanitize_surrogates

            prepared_messages.insert(
                0,
                {"role": "system", "content": _sanitize_surrogates(context.system_prompt)},
            )
        body: dict[str, Any] = {
            "model": model,
            "stream": stream,
            "messages": prepared_messages,
        }
        if tools:
            body["tools"] = self.convert_tools(tools)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if temperature is not None:
            body["temperature"] = temperature
        resolved_max_tokens = (
            None
            if omit_max_tokens
            else max_tokens if max_tokens is not None else profile.get_max_tokens(model)
        )
        if resolved_max_tokens is not None:
            body["max_tokens"] = resolved_max_tokens
        if isinstance(reasoning_config, dict):
            enabled = reasoning_config.get("enabled", True) is not False
            effort = str(reasoning_config.get("effort") or "").strip().lower()
            if enabled and effort and effort != "none":
                if model in {"mistral-small-2603", "mistral-small-latest", "mistral-medium-3.5"}:
                    mapped = (getattr(target_model, "thinking_level_map", None) or {}).get(effort, "high")
                    body["reasoning_effort"] = mapped
                elif target_model is None:
                    body["reasoning_effort"] = "high" if effort != "high" else effort
                else:
                    body["prompt_mode"] = "reasoning"
        if session_id and (cache_retention or "short") != "none":
            body["prompt_cache_key"] = session_id
            body["extra_headers"] = {"x-affinity": session_id}
        if request_overrides:
            body.update(request_overrides)
        return body


__all__ = ["MistralConversationsTransport"]
