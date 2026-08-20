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


def _merge_body(base: dict[str, object], extra: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in extra.items():
        existing_value = merged.get(key)
        if key == "provider" and isinstance(value, dict) and isinstance(existing_value, dict):
            provider = dict(existing_value)
            provider.update(value)
            merged[key] = provider
            continue
        merged[key] = value
    return merged


def _model_consumes_thought_signature(model: Any) -> bool:
    model_name = str(model or "").lower()
    return "gemini" in model_name or "gemma" in model_name


def _add_cache_control_to_text_content(message: dict[str, object], marker: dict[str, str]) -> bool:
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
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None,
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


_INTERNAL_CHAT_MESSAGE_FIELDS = (
    "codex_reasoning_items",
    "codex_message_items",
    "tool_name",
    "timestamp",
)


def _chat_tool_call_needs_sanitize(tool_call: object, strip_extra_content: bool) -> bool:
    return bool(
        isinstance(tool_call, dict)
        and (
            "call_id" in tool_call
            or "response_item_id" in tool_call
            or (strip_extra_content and "extra_content" in tool_call)
        )
    )


def _chat_message_needs_sanitize(
    message: dict[str, object],
    strip_extra_content: bool,
) -> bool:
    if any(field in message for field in _INTERNAL_CHAT_MESSAGE_FIELDS):
        return True
    if any(isinstance(key, str) and key.startswith("_") for key in message):
        return True
    tool_calls = message.get("tool_calls")
    return bool(
        isinstance(tool_calls, list)
        and any(
            _chat_tool_call_needs_sanitize(tool_call, strip_extra_content)
            for tool_call in tool_calls
        )
    )


def _sanitize_chat_tool_call(
    tool_call: dict[object, object],
    strip_extra_content: bool,
) -> None:
    tool_call.pop("call_id", None)
    tool_call.pop("response_item_id", None)
    if strip_extra_content:
        tool_call.pop("extra_content", None)


def _sanitize_chat_message(
    message: dict[str, object],
    strip_extra_content: bool,
) -> None:
    for field in _INTERNAL_CHAT_MESSAGE_FIELDS:
        message.pop(field, None)
    for key in [key for key in message if isinstance(key, str) and key.startswith("_")]:
        message.pop(key, None)
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            _sanitize_chat_tool_call(tool_call, strip_extra_content)


def _prepare_chat_cache_payload(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None,
    compat: OpenAICompat,
    cache_retention: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]] | None]:
    if compat.cache_control_format != "anthropic" or cache_retention == "none":
        return messages, tools
    prepared_messages = copy.deepcopy(messages)
    prepared_tools = copy.deepcopy(tools)
    marker = {"type": "ephemeral"}
    if cache_retention == "long" and compat.supports_long_cache_retention:
        marker["ttl"] = "1h"
    _apply_anthropic_cache_control(prepared_messages, prepared_tools, marker)
    return prepared_messages, prepared_tools


def _apply_chat_cache_options(
    body: dict[str, object],
    compat: OpenAICompat,
    *,
    session_id: str | None,
    base_url: str,
    cache_retention: str,
) -> None:
    if session_id and (
        ("api.openai.com" in base_url and cache_retention != "none")
        or (cache_retention == "long" and compat.supports_long_cache_retention)
    ):
        body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
    if cache_retention == "long" and compat.supports_long_cache_retention:
        body["prompt_cache_retention"] = "24h"


def _apply_chat_generation_options(
    body: dict[str, object],
    compat: OpenAICompat,
    profile: ProviderProfile,
    *,
    model: str,
    temperature: float | None,
    max_tokens: int | None,
    omit_max_tokens: bool,
) -> None:
    if profile.fixed_temperature is OMIT_TEMPERATURE:
        pass
    elif profile.fixed_temperature is not None:
        body["temperature"] = profile.fixed_temperature
    elif temperature is not None:
        body["temperature"] = temperature
    resolved_max_tokens = (
        None
        if omit_max_tokens
        else max_tokens if max_tokens is not None else profile.get_max_tokens(model)
    )
    if resolved_max_tokens is not None:
        body[compat.max_tokens_field] = resolved_max_tokens


def _build_chat_base_body(
    *,
    model: str,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None,
    compat: OpenAICompat,
    profile: ProviderProfile,
    stream: bool,
    temperature: float | None,
    max_tokens: int | None,
    omit_max_tokens: bool,
    tool_choice: object | None,
    session_id: str | None,
    cache_retention: str,
    timeout: float | None,
    base_url: str,
) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    _apply_chat_cache_options(
        body,
        compat,
        session_id=session_id,
        base_url=base_url,
        cache_retention=cache_retention,
    )
    if stream and compat.supports_usage_in_streaming:
        body["stream_options"] = {"include_usage": True}
    if compat.supports_store:
        body["store"] = False
    if timeout is not None:
        body["timeout"] = timeout
    _apply_chat_generation_options(
        body,
        compat,
        profile,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        omit_max_tokens=omit_max_tokens,
    )
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    return body


def _chat_gateway_options(compat: OpenAICompat) -> dict[str, object] | None:
    routing = compat.vercel_gateway_routing
    if not routing:
        return None
    gateway = {
        key: routing[key]
        for key in ("only", "order")
        if key in routing and routing[key] is not None
    }
    return {"gateway": gateway} if gateway else None


def _chat_affinity_headers(
    compat: OpenAICompat,
    session_id: str | None,
) -> dict[str, str] | None:
    if not session_id or not compat.send_session_affinity_headers:
        return None
    if compat.session_affinity_format == "openrouter":
        return {"x-session-id": session_id}
    headers: dict[str, str] = {}
    if compat.session_affinity_format == "openai":
        headers["session_id"] = session_id
    headers["x-client-request-id"] = session_id
    headers["x-session-affinity"] = session_id
    return headers


def _compose_chat_extensions(
    compat: OpenAICompat,
    *,
    tools: list[dict[str, object]] | None,
    provider_preferences: dict[str, object] | None,
    session_id: str | None,
    model_reasoning: bool,
    reasoning_config: dict[str, object] | None,
    thinking_level_map: dict[str, str | None] | None,
    extra_body_additions: dict[str, object] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    extra_body: dict[str, object] = {}
    if provider_preferences:
        extra_body["provider"] = dict(provider_preferences)
    top_level: dict[str, object] = {}
    if model_reasoning:
        _apply_reasoning_payload(
            top_level,
            compat,
            reasoning_config,
            thinking_level_map,
        )
    if compat.openrouter_routing:
        extra_body = _merge_body(extra_body, {"provider": compat.openrouter_routing})
    gateway_options = _chat_gateway_options(compat)
    if gateway_options is not None:
        top_level["providerOptions"] = gateway_options
    if compat.zai_tool_stream and tools:
        top_level["tool_stream"] = True
    affinity_headers = _chat_affinity_headers(compat, session_id)
    if affinity_headers is not None:
        top_level["extra_headers"] = affinity_headers
    if extra_body_additions:
        extra_body = _merge_body(extra_body, extra_body_additions)
    return top_level, extra_body


def _apply_chat_request_overrides(
    body: dict[str, object],
    extra_body: dict[str, object],
    request_overrides: dict[str, object] | None,
) -> dict[str, object]:
    if not request_overrides:
        return extra_body
    for key, value in request_overrides.items():
        if key == "extra_body" and isinstance(value, dict):
            extra_body = _merge_body(extra_body, value)
        else:
            body[key] = value
    return extra_body


def _chat_model_extra_value(owner: object, key: str) -> object | None:
    if not hasattr(owner, "model_extra"):
        return None
    model_extra = getattr(owner, "model_extra", None) or {}
    return model_extra.get(key) if isinstance(model_extra, dict) else None


def _chat_dumped_extra_content(extra_content: object) -> object:
    if not hasattr(extra_content, "model_dump"):
        return extra_content
    try:
        model_dump: object = getattr(extra_content, "model_dump")
        if callable(model_dump):
            return model_dump()
    except Exception:
        pass
    return extra_content


def _chat_tool_call_extra_content(raw_tool_call: object) -> tuple[bool, object | None]:
    extra_content = getattr(raw_tool_call, "extra_content", None)
    if extra_content is None:
        extra_content = _chat_model_extra_value(raw_tool_call, "extra_content")
    if extra_content is None:
        return False, None
    return True, _chat_dumped_extra_content(extra_content)


def _normalize_chat_tool_call(raw_tool_call: object) -> NormalizedToolCall:
    provider_data: dict[str, object] = {}
    has_extra_content, extra_content = _chat_tool_call_extra_content(raw_tool_call)
    if has_extra_content:
        provider_data["extra_content"] = extra_content
    function = getattr(raw_tool_call, "function", None)
    return NormalizedToolCall(
        id=getattr(raw_tool_call, "id", None),
        name=getattr(function, "name", "") if function is not None else "",
        arguments=getattr(function, "arguments", "") if function is not None else "",
        provider_data=provider_data or None,
    )


def _normalize_chat_tool_calls(message: object) -> list[NormalizedToolCall] | None:
    raw_tool_calls = getattr(message, "tool_calls", None)
    if not raw_tool_calls:
        return None
    return [_normalize_chat_tool_call(raw_tool_call) for raw_tool_call in raw_tool_calls]


def _normalize_chat_usage(response: object) -> NormalizedUsage | None:
    raw_usage = getattr(response, "usage", None)
    if raw_usage is None:
        return None
    return NormalizedUsage(
        prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(raw_usage, "total_tokens", 0) or 0),
        cached_tokens=int(getattr(raw_usage, "cached_tokens", 0) or 0),
    )


def _chat_response_provider_data(
    message: object,
    reasoning_content: object | None,
) -> dict[str, object]:
    provider_data: dict[str, object] = {}
    if reasoning_content is not None:
        provider_data["reasoning_content"] = reasoning_content
    reasoning_details = getattr(message, "reasoning_details", None)
    if reasoning_details:
        provider_data["reasoning_details"] = reasoning_details
    return provider_data


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
        if not any(
            isinstance(message, dict)
            and _chat_message_needs_sanitize(message, strip_extra_content)
            for message in messages
        ):
            return messages

        sanitized = copy.deepcopy(messages)
        for message in sanitized:
            if isinstance(message, dict):
                _sanitize_chat_message(message, strip_extra_content)
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
        prepared_messages, prepared_tools = _prepare_chat_cache_payload(
            prepared_messages,
            prepared_tools,
            compat,
            resolved_cache_retention,
        )
        body = _build_chat_base_body(
            model=model,
            messages=prepared_messages,
            tools=prepared_tools,
            compat=compat,
            profile=profile,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            omit_max_tokens=omit_max_tokens,
            tool_choice=tool_choice,
            session_id=session_id,
            cache_retention=resolved_cache_retention,
            timeout=timeout,
            base_url=base_url or profile.base_url,
        )
        top_level, extra_body = _compose_chat_extensions(
            compat,
            tools=prepared_tools,
            provider_preferences=provider_preferences,
            session_id=session_id,
            model_reasoning=model_reasoning,
            reasoning_config=reasoning_config,
            thinking_level_map=model_thinking_level_map,
            extra_body_additions=extra_body_additions,
        )
        body.update(top_level)
        extra_body = _apply_chat_request_overrides(body, extra_body, request_overrides)
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
        tool_calls = _normalize_chat_tool_calls(message)
        usage = _normalize_chat_usage(response)
        reasoning = getattr(message, "reasoning", None)
        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content is None and hasattr(message, "model_extra"):
            model_extra = getattr(message, "model_extra", None) or {}
            if isinstance(model_extra, dict):
                reasoning_content = model_extra.get("reasoning_content")
        provider_data = _chat_response_provider_data(message, reasoning_content)
        content = getattr(message, "content", None)
        refusal = getattr(message, "refusal", None)
        if refusal is None:
            refusal = _chat_model_extra_value(message, "refusal")
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
