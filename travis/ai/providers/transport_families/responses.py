"""Codex and OpenAI Responses transport family."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from travis.ai.providers.base import NormalizedResponse, ProviderProfile
from travis.ai.providers.responses_translation import (
    convert_responses_messages,
    convert_responses_tools,
    split_deferred_tools,
)
from travis.ai.providers.transport_families._shared import (
    content_to_text as _content_to_text,
    tool_arguments as _tool_arguments,
    tool_function as _tool_function,
)
from travis.ai.providers.transport_families.chat_completions import _clamp_openai_prompt_cache_key
from travis.ai.types import Context

def _split_responses_tool_call_id(tool_call_id: str) -> tuple[str, str | None]:
    if "|" not in tool_call_id:
        return tool_call_id, None
    call_id, item_id = tool_call_id.split("|", 1)
    return call_id, item_id or None




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


def _codex_reasoning_fields(
    reasoning_config: dict[str, Any] | None,
    reasoning_summary: str | None,
) -> dict[str, Any]:
    if not isinstance(reasoning_config, dict):
        return {}
    effort = str(reasoning_config.get("effort") or "").strip().lower()
    if reasoning_config.get("enabled") is False or effort == "none":
        effort = "none"
    return (
        {"reasoning": {"effort": effort, "summary": reasoning_summary or "auto"}}
        if effort
        else {}
    )


def _openai_cache_fields(
    session_id: str | None,
    cache_retention: str | None,
    *,
    supports_long: bool,
) -> dict[str, Any]:
    resolved_retention = cache_retention or "short"
    if resolved_retention == "none":
        return {}
    fields: dict[str, Any] = {}
    if session_id:
        fields["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
    if resolved_retention == "long" and supports_long:
        fields["prompt_cache_retention"] = "24h"
    return fields


def _openai_sampling_fields(
    *,
    max_tokens: int | None,
    temperature: float | None,
    service_tier: str | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if max_tokens is not None:
        fields["max_output_tokens"] = max(max_tokens, 16)
    if temperature is not None:
        fields["temperature"] = temperature
    if service_tier is not None:
        fields["service_tier"] = service_tier
    return fields


def _openai_reasoning_fields(
    *,
    model_reasoning: bool,
    reasoning_config: dict[str, Any] | None,
    reasoning_summary: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> dict[str, Any]:
    if not model_reasoning:
        return {}
    config = reasoning_config or {}
    enabled = config.get("enabled", True) is not False
    effort = str(config.get("effort") or "").strip().lower()
    if enabled and ((effort and effort != "none") or reasoning_summary):
        selected_effort = effort if effort and effort != "none" else "medium"
        mapped = (thinking_level_map or {}).get(selected_effort, selected_effort)
        return {
            "reasoning": {"effort": mapped, "summary": reasoning_summary or "auto"},
            "include": ["reasoning.encrypted_content"],
        }
    off_effort = (thinking_level_map or {}).get("off", "none")
    return {"reasoning": {"effort": off_effort}} if off_effort is not None else {}


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
        body.update(_codex_reasoning_fields(reasoning_config, reasoning_summary))
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
        body.update(
            _openai_cache_fields(
                session_id,
                resolved_cache_retention,
                supports_long=supports_long,
            )
        )
        body.update(
            _openai_sampling_fields(
                max_tokens=max_tokens,
                temperature=temperature,
                service_tier=service_tier,
            )
        )
        if immediate_tools:
            body["tools"] = convert_responses_tools(immediate_tools)
        elif context is None and tools:
            body["tools"] = self.convert_tools(tools)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        body.update(
            _openai_reasoning_fields(
                model_reasoning=model_reasoning,
                reasoning_config=reasoning_config,
                reasoning_summary=reasoning_summary,
                thinking_level_map=model_thinking_level_map,
            )
        )
        if request_overrides:
            body.update(request_overrides)
        return body


__all__ = ["CodexResponsesTransport", "OpenAIResponsesTransport"]
