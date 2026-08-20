"""Immutable provider request preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Mapping

from travis.ai.env_config import ModelConfig
from travis.ai.context_estimate import clamp_max_tokens_to_context
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers._shared import settle_callback
from travis.ai.providers.capabilities import GenerationPayload, build_generation_payload
from travis.ai.providers.catalog import ResolvedProviderRuntime, normalize_provider, resolve_provider_runtime
from travis.ai.providers.chat_stream import parse_sse_chunks
from travis.ai.providers.copilot_headers import build_copilot_dynamic_headers
from travis.ai.providers.message_translation import convert_messages
from travis.ai.providers.params import GenerationParams, merge_generation_params
from travis.ai.providers.provider_auth import apply_provider_auth_headers, resolve_provider_base_url
from travis.ai.providers.provider_identity import apply_provider_identity_headers
from travis.ai.providers.request_auth import build_request_auth_headers
from travis.ai.providers.transport_registry import TransportLookup
from travis.ai.providers.transports import get_transport
from travis.ai.types import Context, Model


@dataclass(frozen=True)
class PreparedProviderRequest:
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, object]
    timeout_seconds: float | None
    api_mode: str
    decoder: Callable[[Iterable[str]], Iterator[object]]


def _merge_headers(target: dict[str, str], updates: Mapping[str, object] | None) -> None:
    if not updates:
        return
    for raw_key, value in updates.items():
        key = str(raw_key)
        lowered = key.lower()
        for existing in tuple(target):
            if existing.lower() == lowered:
                del target[existing]
        if value is not None:
            target[key] = str(value)


def _generation_settings(
    options: object | None,
    config: ModelConfig,
) -> tuple[GenerationParams, float | None]:
    option_params = getattr(options, "generation_params", None) if options is not None else None
    if not isinstance(option_params, GenerationParams):
        option_params = None
    generation_params = merge_generation_params(config.generation_params, option_params)
    max_tokens = getattr(options, "max_tokens", None) if options is not None else None
    if max_tokens is not None:
        generation_params = merge_generation_params(
            generation_params,
            GenerationParams(max_tokens=max_tokens, sources={"max_tokens": "runtime_options"}),
        )
    timeout_seconds: float | None = generation_params.timeout_seconds or config.timeout_seconds
    timeout_ms = getattr(options, "timeout_ms", None) if options is not None else None
    if isinstance(timeout_ms, (int, float)) and not isinstance(timeout_ms, bool):
        if timeout_ms < 0:
            raise ValueError(f"Invalid timeout_ms: {timeout_ms}")
        timeout_seconds = None if timeout_ms == 0 else float(timeout_ms) / 1000
    return generation_params, timeout_seconds


def _request_api_key(
    options: object | None,
    config: ModelConfig,
    runtime: ResolvedProviderRuntime,
    model: Model,
) -> str | None:
    option_api_key = getattr(options, "api_key", None) if options is not None else None
    configured_api_key = config.api_key if normalize_provider(config.provider) == runtime.provider else None
    api_key = option_api_key if isinstance(option_api_key, str) and option_api_key.strip() else configured_api_key
    if (
        model.api == "google-vertex"
        and isinstance(api_key, str)
        and (api_key == "gcp-vertex-credentials" or (api_key.startswith("<") and api_key.endswith(">")))
    ):
        return None
    return api_key


def _notify_generation_warnings(
    options: object | None,
    generation_payload: GenerationPayload,
) -> None:
    callback = getattr(options, "on_generation_warning", None) if options is not None else None
    if not callable(callback):
        return
    for warning in generation_payload.warnings:
        callback(warning)


def _requested_max_tokens(
    options: object | None,
    generation_payload: GenerationPayload,
    model: Model,
    context: Context,
) -> tuple[int | None, bool]:
    requested_max_tokens = generation_payload.max_tokens
    omit_max_tokens = bool(getattr(options, "omit_max_tokens", False)) if options is not None else False
    if omit_max_tokens:
        return None, True
    if requested_max_tokens is None:
        requested_max_tokens = int(model.max_tokens or 0) or None
    if requested_max_tokens is not None:
        requested_max_tokens = clamp_max_tokens_to_context(model, context, requested_max_tokens)
    return requested_max_tokens, False


def _optional_transport_fields(
    options: object | None,
    model: Model,
) -> dict[str, object]:
    fields: dict[str, object] = {}
    for option_name in (
        "tool_choice",
        "reasoning_summary",
        "service_tier",
        "text_verbosity",
        "metadata",
    ):
        value = getattr(options, option_name, None) if options is not None else None
        if value is not None:
            fields[option_name] = value
    cache_retention = getattr(options, "cache_retention", None) if options is not None else None
    if isinstance(cache_retention, str) and cache_retention.strip():
        fields["cache_retention"] = cache_retention.strip()
    session_id = getattr(options, "session_id", None) if options is not None else None
    if isinstance(session_id, str) and session_id.strip():
        fields["session_id"] = session_id
    reasoning_config = getattr(options, "reasoning_config", None) if options is not None else None
    if model.reasoning and isinstance(reasoning_config, dict):
        fields["reasoning_config"] = dict(reasoning_config)
        return fields
    reasoning = getattr(options, "reasoning", None) if options is not None else None
    if model.reasoning and isinstance(reasoning, str) and reasoning.strip():
        effort = reasoning.strip().lower()
        fields["reasoning_config"] = (
            {"enabled": False, "effort": "none"}
            if effort == "off"
            else {"enabled": True, "effort": effort}
        )
    return fields


def _apply_payload_callback(
    body: dict[str, object],
    options: object | None,
    model: Model,
) -> dict[str, object]:
    callback = getattr(options, "on_payload", None) if options is not None else None
    if not callable(callback):
        return body
    next_body = settle_callback(callback(body, model))
    return next_body if isinstance(next_body, dict) else body


def _request_headers(
    *,
    profile: ProviderProfile,
    generated_headers: object,
    runtime: ResolvedProviderRuntime,
    model: Model,
    context: Context,
    transport: TransportLookup,
    options: object | None,
    api_key: str | None,
) -> dict[str, str]:
    headers = dict(profile.default_headers)
    if isinstance(generated_headers, dict):
        _merge_headers(headers, generated_headers)
    if api_key:
        _merge_headers(headers, build_request_auth_headers(runtime.provider, model.api, api_key))
    if isinstance(model.headers, dict):
        _merge_headers(headers, model.headers)
    if model.provider == "github-copilot":
        _merge_headers(headers, build_copilot_dynamic_headers(context.messages))
    apply_provider_auth_headers(model.provider, headers, api_key)
    option_headers = getattr(options, "headers", None) if options is not None else None
    if isinstance(option_headers, dict):
        _merge_headers(headers, option_headers)
    if not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/json"
    finalize_headers = getattr(transport, "finalize_headers", None)
    if callable(finalize_headers):
        headers = finalize_headers(
            headers,
            api_key=api_key,
            session_id=getattr(options, "session_id", None) if options is not None else None,
            cache_retention=getattr(options, "cache_retention", None) if options is not None else None,
            model=model,
        )
    apply_provider_identity_headers(model.provider, headers)
    callback = getattr(options, "on_headers", None) if options is not None else None
    if callable(callback):
        next_headers = settle_callback(callback(headers, model))
        if isinstance(next_headers, dict):
            headers = next_headers
    return {
        str(key): str(value)
        for key, value in headers.items()
        if value is not None
    }


def _request_url(
    transport: TransportLookup,
    base_url: str,
    model: Model,
    config: ModelConfig,
    options: object | None,
    api_key: str | None,
    runtime: ResolvedProviderRuntime,
) -> str:
    build_url = getattr(transport, "build_url", None)
    if callable(build_url):
        return build_url(base_url, model.id or config.model, options, api_key)
    return base_url.rstrip("/") + str(getattr(transport, "endpoint_path", runtime.endpoint_path))


def prepare_provider_request(
    model: Model,
    context: Context,
    options: object | None,
    config: ModelConfig,
    default_profile: ProviderProfile,
) -> PreparedProviderRequest:
    messages, tools = convert_messages(context, model)
    generation_params, timeout_seconds = _generation_settings(options, config)
    runtime = resolve_provider_runtime(
        model.provider,
        explicit_base_url=model.base_url,
        fallback_base_url=config.base_url,
    )
    profile = runtime.profile or default_profile
    transport = get_transport(model.api)
    request_env = getattr(options, "env", None) if options is not None else None
    base_url = resolve_provider_base_url(
        model.provider,
        runtime.base_url or model.base_url or profile.base_url or config.base_url,
        request_env if isinstance(request_env, dict) else None,
    )
    api_mode = getattr(transport, "api_mode", model.api)
    api_key = _request_api_key(options, config, runtime, model)
    generation_payload = build_generation_payload(
        provider=runtime.provider,
        api_mode=api_mode,
        params=generation_params,
        tools_enabled=bool(tools),
    )
    _notify_generation_warnings(options, generation_payload)
    requested_max_tokens, omit_max_tokens = _requested_max_tokens(
        options,
        generation_payload,
        model,
        context,
    )
    transport_kwargs: dict[str, object] = {
        "model": model.id or config.model,
        "messages": messages,
        "tools": tools,
        "profile": profile,
        "stream": True,
        "temperature": generation_payload.temperature,
        "max_tokens": requested_max_tokens,
        "omit_max_tokens": omit_max_tokens,
        "provider_preferences": generation_payload.provider_preferences,
        "request_overrides": generation_payload.request_overrides,
        "base_url": base_url,
        "model_compat": dict(model.compat or {}),
        "model_reasoning": model.reasoning,
        "model_thinking_level_map": dict(model.thinking_level_map or {}) or None,
        "context": context,
        "target_model": model,
        "api_key": api_key,
        "options": options,
    }
    transport_kwargs.update(_optional_transport_fields(options, model))
    body = transport.build_kwargs(**transport_kwargs)
    generated_headers = body.pop("extra_headers", None)
    body = _apply_payload_callback(body, options, model)
    headers = _request_headers(
        profile=profile,
        generated_headers=generated_headers,
        runtime=runtime,
        model=model,
        context=context,
        transport=transport,
        options=options,
        api_key=api_key,
    )
    include_reasoning = model.reasoning and bool(getattr(options, "reasoning", None))
    stream_options = body.get("stream_options")
    wait_for_usage_after_finish = (
        api_mode == "chat_completions"
        and isinstance(stream_options, dict)
        and stream_options.get("include_usage") is True
    )

    def decode(lines: Iterable[str]) -> Iterator[object]:
        return parse_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=timeout_seconds,
            include_reasoning=include_reasoning,
            api_mode=api_mode,
            tools=context.tools,
            wait_for_usage_after_finish=wait_for_usage_after_finish,
            anthropic_oauth=(
                api_mode == "anthropic_messages"
                and isinstance(api_key, str)
                and "sk-ant-oat" in api_key
            ),
        )

    request_url = _request_url(transport, base_url, model, config, options, api_key, runtime)
    return PreparedProviderRequest(
        url=request_url,
        headers=headers,
        body=body,
        timeout_seconds=timeout_seconds,
        api_mode=api_mode,
        decoder=decode,
    )
