"""Immutable provider request preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Mapping

from travis.ai.env_config import ModelConfig
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.capabilities import build_generation_payload
from travis.ai.providers.catalog import resolve_provider_runtime
from travis.ai.providers.chat_stream import parse_sse_chunks
from travis.ai.providers.message_translation import convert_messages
from travis.ai.providers.params import GenerationParams, merge_generation_params
from travis.ai.providers.transports import get_transport
from travis.ai.types import Context, Model


@dataclass(frozen=True)
class PreparedProviderRequest:
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, object]
    timeout_seconds: float
    api_mode: str
    decoder: Callable[[Iterable[str]], Iterator[object]]


def prepare_provider_request(
    model: Model,
    context: Context,
    options: object | None,
    config: ModelConfig,
    default_profile: ProviderProfile,
    transport_for_profile: Callable[[ProviderProfile], object],
) -> PreparedProviderRequest:
    messages, tools = convert_messages(context, model)
    option_params = getattr(options, "generation_params", None) if options is not None else None
    if option_params is not None and not isinstance(option_params, GenerationParams):
        option_params = None
    generation_params = merge_generation_params(config.generation_params, option_params)
    max_tokens = getattr(options, "max_tokens", None) if options is not None else None
    if max_tokens is not None:
        generation_params = merge_generation_params(
            generation_params,
            GenerationParams(max_tokens=max_tokens, sources={"max_tokens": "runtime_options"}),
        )
    runtime = resolve_provider_runtime(
        model.provider,
        explicit_base_url=model.base_url,
        fallback_base_url=config.base_url,
    )
    profile = runtime.profile or default_profile
    transport = (
        transport_for_profile(profile)
        if profile.api_mode == runtime.api_mode
        else get_transport(runtime.api_mode)
    )
    base_url = runtime.base_url or model.base_url or profile.base_url or config.base_url
    api_mode = getattr(transport, "api_mode", runtime.api_mode)
    generation_payload = build_generation_payload(
        provider=runtime.provider,
        api_mode=api_mode,
        params=generation_params,
        tools_enabled=bool(tools),
    )
    on_generation_warning = getattr(options, "on_generation_warning", None) if options is not None else None
    if callable(on_generation_warning):
        for warning in generation_payload.warnings:
            on_generation_warning(warning)
    transport_kwargs: dict[str, object] = {
        "model": model.id or config.model,
        "messages": messages,
        "tools": tools,
        "profile": profile,
        "stream": True,
        "temperature": generation_payload.temperature,
        "max_tokens": generation_payload.max_tokens,
        "provider_preferences": generation_payload.provider_preferences,
        "request_overrides": generation_payload.request_overrides,
        "base_url": base_url,
    }
    session_id = getattr(options, "session_id", None) if options is not None else None
    if isinstance(session_id, str) and session_id.strip():
        transport_kwargs["session_id"] = session_id
    reasoning_config = getattr(options, "reasoning_config", None) if options is not None else None
    if isinstance(reasoning_config, dict):
        transport_kwargs["reasoning_config"] = dict(reasoning_config)
    else:
        reasoning = getattr(options, "reasoning", None) if options is not None else None
        if isinstance(reasoning, str) and reasoning.strip():
            effort = reasoning.strip().lower()
            transport_kwargs["reasoning_config"] = (
                {"enabled": False, "effort": "none"}
                if effort == "off"
                else {"enabled": True, "effort": effort}
            )
    body = transport.build_kwargs(**transport_kwargs)
    on_payload = getattr(options, "on_payload", None) if options is not None else None
    if callable(on_payload):
        next_body = on_payload(body)
        if isinstance(next_body, dict):
            body = next_body
    option_headers = getattr(options, "headers", None) if options is not None else None
    headers = dict(profile.default_headers)
    if isinstance(option_headers, dict):
        headers.update({str(key): str(value) for key, value in option_headers.items()})
    extra_headers = body.pop("extra_headers", None)
    if isinstance(extra_headers, dict):
        headers.update({str(key): str(value) for key, value in extra_headers.items()})
    option_api_key = getattr(options, "api_key", None) if options is not None else None
    configured_api_key = config.api_key if config.provider == runtime.provider else None
    api_key = option_api_key if isinstance(option_api_key, str) and option_api_key.strip() else configured_api_key
    if api_key:
        headers.update(profile.auth_headers(api_key))
    headers.setdefault("Content-Type", "application/json")
    include_reasoning = bool(getattr(options, "reasoning", None))
    wait_for_usage_after_finish = (
        api_mode == "chat_completions"
        and profile.supports_stream_usage(base_url=base_url)
        and isinstance(body.get("stream_options"), dict)
        and body["stream_options"].get("include_usage") is True
    )

    def decode(lines: Iterable[str]) -> Iterator[object]:
        return parse_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=config.timeout_seconds,
            include_reasoning=include_reasoning,
            api_mode=api_mode,
            tools=context.tools,
            wait_for_usage_after_finish=wait_for_usage_after_finish,
        )

    return PreparedProviderRequest(
        url=base_url.rstrip("/") + runtime.endpoint_path,
        headers=headers,
        body=body,
        timeout_seconds=config.timeout_seconds,
        api_mode=api_mode,
        decoder=decode,
    )
