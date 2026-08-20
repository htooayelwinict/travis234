"""OpenAI-compatible chat-completions transport family."""

from __future__ import annotations

import copy
from typing import Any

from travis.ai.providers.base import (
    OMIT_TEMPERATURE,
    NormalizedResponse,
    NormalizedToolCall,
    NormalizedUsage,
    ProviderProfile,
)
from travis.ai.providers.openai_compat import OpenAICompat

def _clamp_openai_prompt_cache_key(key: str | None) -> str | None:
    if key is None:
        return None
    return "".join(list(key)[:64])


def _merge_body(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key == "provider" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            provider = dict(merged[key])
            provider.update(value)
            merged[key] = provider
            continue
        merged[key] = value
    return merged


def _model_consumes_thought_signature(model: Any) -> bool:
    model_name = str(model or "").lower()
    return "gemini" in model_name or "gemma" in model_name


def _add_cache_control_to_text_content(message: dict[str, Any], marker: dict[str, str]) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return False
        message["content"] = [{"type": "text", "text": content, "cache_control": marker}]
        return True
    if not isinstance(content, list):
        return False
    for part in reversed(content):
        if isinstance(part, dict) and part.get("type") == "text":
            part["cache_control"] = marker
            return True
    return False


def _apply_anthropic_cache_control(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    marker: dict[str, str],
) -> None:
    for message in messages:
        if message.get("role") in {"system", "developer"}:
            _add_cache_control_to_text_content(message, marker)
            break
    if tools:
        tools[-1]["cache_control"] = marker
    for message in reversed(messages):
        if message.get("role") in {"user", "assistant"} and _add_cache_control_to_text_content(message, marker):
            break


def _thinking_enabled(reasoning_config: dict[str, object] | None) -> tuple[bool, str | None]:
    if not isinstance(reasoning_config, dict) or reasoning_config.get("enabled") is False:
        return False, None
    effort = str(reasoning_config.get("effort") or "").strip().lower()
    if not effort or effort in {"none", "off"}:
        return False, None
    return True, effort


def _mapped_thinking_level(
    thinking_level_map: dict[str, str | None] | None,
    level: str,
) -> str | None:
    if thinking_level_map is None or level not in thinking_level_map:
        return level
    return thinking_level_map[level]


def _off_thinking_supported(thinking_level_map: dict[str, str | None] | None) -> bool:
    return not (
        thinking_level_map is not None
        and "off" in thinking_level_map
        and thinking_level_map["off"] is None
    )


def _resolve_chat_template_value(
    value: object,
    *,
    enabled: bool,
    effort: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> object | None:
    if not isinstance(value, dict):
        return value
    if not enabled and value.get("omitWhenOff"):
        return None
    if value.get("$var") == "thinking.enabled":
        return enabled
    level = effort if enabled and effort else "off"
    mapped = _mapped_thinking_level(thinking_level_map, level)
    return mapped if isinstance(mapped, str) else effort


def _apply_zai_reasoning(
    body: dict[str, object],
    compat: OpenAICompat,
    enabled: bool,
    mapped_effort: str | None,
) -> None:
    thinking: dict[str, object] = {"type": "enabled" if enabled else "disabled"}
    if enabled:
        thinking["clear_thinking"] = False
        if compat.supports_reasoning_effort and mapped_effort is not None:
            body["reasoning_effort"] = mapped_effort
    body["thinking"] = thinking


def _apply_chat_template_reasoning(
    body: dict[str, object],
    compat: OpenAICompat,
    enabled: bool,
    effort: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> None:
    kwargs: dict[str, object] = {}
    for key, value in compat.chat_template_kwargs.items():
        resolved = _resolve_chat_template_value(
            value,
            enabled=enabled,
            effort=effort,
            thinking_level_map=thinking_level_map,
        )
        if resolved is not None:
            kwargs[key] = resolved
    if kwargs:
        body["chat_template_kwargs"] = kwargs


def _apply_deepseek_reasoning(
    body: dict[str, object],
    compat: OpenAICompat,
    enabled: bool,
    mapped_effort: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> None:
    if enabled:
        body["thinking"] = {"type": "enabled"}
    elif _off_thinking_supported(thinking_level_map):
        body["thinking"] = {"type": "disabled"}
    if enabled and compat.supports_reasoning_effort and mapped_effort is not None:
        body["reasoning_effort"] = mapped_effort


def _off_thinking_value(
    thinking_level_map: dict[str, str | None] | None,
) -> str:
    value = (thinking_level_map or {}).get("off", "none")
    return value if isinstance(value, str) else "none"


def _apply_openrouter_reasoning(
    body: dict[str, object],
    enabled: bool,
    mapped_effort: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> None:
    if enabled and mapped_effort is not None:
        body["reasoning"] = {"effort": mapped_effort}
    elif _off_thinking_supported(thinking_level_map):
        body["reasoning"] = {"effort": _off_thinking_value(thinking_level_map)}


def _apply_string_reasoning(
    body: dict[str, object],
    enabled: bool,
    mapped_effort: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> None:
    if enabled and mapped_effort is not None:
        body["thinking"] = mapped_effort
    elif _off_thinking_supported(thinking_level_map):
        body["thinking"] = _off_thinking_value(thinking_level_map)


def _apply_default_reasoning(
    body: dict[str, object],
    compat: OpenAICompat,
    enabled: bool,
    mapped_effort: str | None,
    thinking_level_map: dict[str, str | None] | None,
) -> None:
    if enabled and compat.supports_reasoning_effort and mapped_effort is not None:
        body["reasoning_effort"] = mapped_effort
    elif compat.supports_reasoning_effort and thinking_level_map is not None:
        off_value = thinking_level_map.get("off")
        if isinstance(off_value, str):
            body["reasoning_effort"] = off_value


def _apply_reasoning_payload(
    body: dict[str, object],
    compat: OpenAICompat,
    reasoning_config: dict[str, object] | None,
    thinking_level_map: dict[str, str | None] | None,
) -> None:
    enabled, effort = _thinking_enabled(reasoning_config)
    mapped = _mapped_thinking_level(thinking_level_map, effort) if effort else None
    thinking_format = compat.thinking_format
    if thinking_format == "zai":
        _apply_zai_reasoning(body, compat, enabled, mapped)
    elif thinking_format == "qwen":
        body["enable_thinking"] = enabled
    elif thinking_format == "qwen-chat-template":
        body["chat_template_kwargs"] = {
            "enable_thinking": enabled,
            "preserve_thinking": True,
        }
    elif thinking_format == "chat-template":
        _apply_chat_template_reasoning(body, compat, enabled, effort, thinking_level_map)
    elif thinking_format == "deepseek":
        _apply_deepseek_reasoning(body, compat, enabled, mapped, thinking_level_map)
    elif thinking_format == "openrouter":
        _apply_openrouter_reasoning(body, enabled, mapped, thinking_level_map)
    elif thinking_format == "ant-ling":
        if enabled and mapped is not None:
            body["reasoning"] = {"effort": mapped}
    elif thinking_format == "together":
        body["reasoning"] = {"enabled": enabled}
        if enabled and compat.supports_reasoning_effort and mapped is not None:
            body["reasoning_effort"] = mapped
    elif thinking_format == "string-thinking":
        _apply_string_reasoning(body, enabled, mapped, thinking_level_map)
    else:
        _apply_default_reasoning(body, compat, enabled, mapped, thinking_level_map)


class ChatCompletionsTransport:
    api = "openai-completions"
    api_mode = "chat_completions"
    endpoint_path = "/chat/completions"

    def convert_messages(self, messages: list[dict[str, Any]], *, model: str | None = None) -> list[dict[str, Any]]:
        """Strip travis/Travis-internal replay fields before provider payload.

        This preserves the established chat-completions provider boundary: conversation
        history can carry provider/private bookkeeping, but strict OpenAI-
        compatible providers must only receive schema-valid chat messages.
        """
        strip_extra_content = not _model_consumes_thought_signature(model)
        needs_sanitize = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            if (
                "codex_reasoning_items" in message
                or "codex_message_items" in message
                or "tool_name" in message
                or "timestamp" in message
            ):
                needs_sanitize = True
                break
            if any(isinstance(key, str) and key.startswith("_") for key in message):
                needs_sanitize = True
                break
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict) and (
                        "call_id" in tool_call
                        or "response_item_id" in tool_call
                        or (strip_extra_content and "extra_content" in tool_call)
                    ):
                        needs_sanitize = True
                        break
                if needs_sanitize:
                    break

        if not needs_sanitize:
            return messages

        sanitized = copy.deepcopy(messages)
        for message in sanitized:
            if not isinstance(message, dict):
                continue
            message.pop("codex_reasoning_items", None)
            message.pop("codex_message_items", None)
            message.pop("tool_name", None)
            message.pop("timestamp", None)
            for key in [key for key in message if isinstance(key, str) and key.startswith("_")]:
                message.pop(key, None)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call.pop("call_id", None)
                    tool_call.pop("response_item_id", None)
                    if strip_extra_content:
                        tool_call.pop("extra_content", None)
        return sanitized

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Chat Completions tools are already in OpenAI-compatible format."""
        return tools

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
        provider_preferences: dict[str, Any] | None = None,
        tool_choice: Any | None = None,
        session_id: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        extra_body_additions: dict[str, Any] | None = None,
        timeout: float | None = None,
        base_url: str | None = None,
        openrouter_min_coding_score: float | str | None = None,
        model_compat: dict[str, Any] | None = None,
        model_reasoning: bool = False,
        model_thinking_level_map: dict[str, str | None] | None = None,
        cache_retention: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from travis.ai.providers.openai_compat import resolve_openai_compat
        from travis.ai.types import Model

        compat = resolve_openai_compat(
            Model(
                id=model,
                name=model,
                api="openai-completions",
                provider=profile.name,
                base_url=base_url or profile.base_url,
                reasoning=model_reasoning,
                thinking_level_map=model_thinking_level_map,
                compat=model_compat,
            )
        )
        prepared_messages = self.convert_messages(messages, model=model)
        prepared_tools = self.convert_tools(tools) if tools is not None else None
        resolved_cache_retention = cache_retention or "short"
        if compat.cache_control_format == "anthropic" and resolved_cache_retention != "none":
            prepared_messages = copy.deepcopy(prepared_messages)
            prepared_tools = copy.deepcopy(prepared_tools)
            marker = {"type": "ephemeral"}
            if resolved_cache_retention == "long" and compat.supports_long_cache_retention:
                marker["ttl"] = "1h"
            _apply_anthropic_cache_control(prepared_messages, prepared_tools, marker)

        body: dict[str, Any] = {
            "model": model,
            "messages": prepared_messages,
            "stream": stream,
        }
        effective_base_url = base_url or profile.base_url
        if session_id and (
            ("api.openai.com" in effective_base_url and resolved_cache_retention != "none")
            or (resolved_cache_retention == "long" and compat.supports_long_cache_retention)
        ):
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
        if resolved_cache_retention == "long" and compat.supports_long_cache_retention:
            body["prompt_cache_retention"] = "24h"
        if stream and compat.supports_usage_in_streaming:
            body["stream_options"] = {"include_usage": True}
        if compat.supports_store:
            body["store"] = False
        if timeout is not None:
            body["timeout"] = timeout
        if profile.fixed_temperature is OMIT_TEMPERATURE:
            pass
        elif profile.fixed_temperature is not None:
            body["temperature"] = profile.fixed_temperature
        elif temperature is not None:
            body["temperature"] = temperature
        if prepared_tools is not None:
            body["tools"] = prepared_tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        resolved_max_tokens = (
            None
            if omit_max_tokens
            else max_tokens if max_tokens is not None else profile.get_max_tokens(model)
        )
        if resolved_max_tokens is not None:
            body[compat.max_tokens_field] = resolved_max_tokens

        extra_body: dict[str, Any] = {}
        if provider_preferences:
            extra_body["provider"] = dict(provider_preferences)
        top_level: dict[str, object] = {}
        if model_reasoning:
            _apply_reasoning_payload(
                top_level,
                compat,
                reasoning_config,
                model_thinking_level_map,
            )
        if compat.openrouter_routing:
            extra_body = _merge_body(extra_body, {"provider": compat.openrouter_routing})
        if compat.vercel_gateway_routing:
            routing = compat.vercel_gateway_routing
            gateway = {
                key: routing[key]
                for key in ("only", "order")
                if key in routing and routing[key] is not None
            }
            if gateway:
                top_level["providerOptions"] = {"gateway": gateway}
        if compat.zai_tool_stream and prepared_tools:
            top_level["tool_stream"] = True
        if session_id and compat.send_session_affinity_headers:
            affinity_headers: dict[str, str] = {}
            if compat.session_affinity_format == "openrouter":
                affinity_headers["x-session-id"] = session_id
            else:
                if compat.session_affinity_format == "openai":
                    affinity_headers["session_id"] = session_id
                affinity_headers["x-client-request-id"] = session_id
                affinity_headers["x-session-affinity"] = session_id
            top_level["extra_headers"] = affinity_headers
        if extra_body_additions:
            extra_body = _merge_body(extra_body, extra_body_additions)
        body.update(top_level)
        if request_overrides:
            for key, value in request_overrides.items():
                if key == "extra_body" and isinstance(value, dict):
                    extra_body = _merge_body(extra_body, value)
                else:
                    body[key] = value
        body.update(extra_body)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        """Normalize OpenAI ChatCompletion-like responses.

        The runtime streams directly from raw SSE today, while this transport
        keeps provider response shape centralized for non-streaming, tests,
        and future provider adapters.
        """
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason or "stop"

        tool_calls: list[NormalizedToolCall] | None = None
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            tool_calls = []
            for raw_tool_call in raw_tool_calls:
                provider_data: dict[str, Any] = {}
                extra_content = getattr(raw_tool_call, "extra_content", None)
                if extra_content is None and hasattr(raw_tool_call, "model_extra"):
                    model_extra = getattr(raw_tool_call, "model_extra", None) or {}
                    if isinstance(model_extra, dict):
                        extra_content = model_extra.get("extra_content")
                if extra_content is not None:
                    if hasattr(extra_content, "model_dump"):
                        try:
                            extra_content = extra_content.model_dump()
                        except Exception:
                            pass
                    provider_data["extra_content"] = extra_content
                function = getattr(raw_tool_call, "function", None)
                tool_calls.append(
                    NormalizedToolCall(
                        id=getattr(raw_tool_call, "id", None),
                        name=getattr(function, "name", "") if function is not None else "",
                        arguments=getattr(function, "arguments", "") if function is not None else "",
                        provider_data=provider_data or None,
                    )
                )

        usage = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage = NormalizedUsage(
                prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(raw_usage, "total_tokens", 0) or 0),
                cached_tokens=int(getattr(raw_usage, "cached_tokens", 0) or 0),
            )

        reasoning = getattr(message, "reasoning", None)
        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content is None and hasattr(message, "model_extra"):
            model_extra = getattr(message, "model_extra", None) or {}
            if isinstance(model_extra, dict):
                reasoning_content = model_extra.get("reasoning_content")

        provider_data: dict[str, Any] = {}
        if reasoning_content is not None:
            provider_data["reasoning_content"] = reasoning_content
        reasoning_details = getattr(message, "reasoning_details", None)
        if reasoning_details:
            provider_data["reasoning_details"] = reasoning_details

        content = getattr(message, "content", None)
        refusal = getattr(message, "refusal", None)
        if refusal is None and hasattr(message, "model_extra"):
            model_extra = getattr(message, "model_extra", None) or {}
            if isinstance(model_extra, dict):
                refusal = model_extra.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            provider_data["refusal"] = refusal
            has_text = isinstance(content, str) and bool(content.strip())
            has_tool_calls = bool(tool_calls)
            if not has_text and not has_tool_calls:
                content = refusal
                if finish_reason in (None, "stop"):
                    finish_reason = "content_filter"

        return NormalizedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            reasoning=reasoning or reasoning_content,
            usage=usage,
            provider_data=provider_data or None,
        )

    def validate_response(self, response: Any) -> bool:
        return bool(response is not None and getattr(response, "choices", None))

    def extract_cache_stats(self, response: Any) -> dict[str, int] | None:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
        if details is None:
            return None
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        written = int(getattr(details, "cache_write_tokens", 0) or 0)
        if cached or written:
            return {"cached_tokens": cached, "creation_tokens": written}
        return None


__all__ = ["ChatCompletionsTransport"]
