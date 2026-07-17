"""Provider generation-parameter capability policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from travis.ai.providers.params import GenerationParams


@dataclass(frozen=True)
class ProviderParamWarning:
    param: str
    action: str
    reason: str


@dataclass(frozen=True)
class GenerationPayload:
    temperature: float | None = None
    max_tokens: int | None = None
    provider_preferences: dict[str, Any] | None = None
    request_overrides: dict[str, Any] = field(default_factory=dict)
    warnings: list[ProviderParamWarning] = field(default_factory=list)


_CHAT_COMMON = (
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "stop",
    "parallel_tool_calls",
    "tool_choice",
)
_ANTHROPIC_DIRECT = ("top_p", "stop")
_ANTHROPIC_TOOL_CHOICES = {"auto", "any", "none"}
_RESPONSES_COMMON = ("top_p", "parallel_tool_calls", "tool_choice")
_CODEX_RESPONSES_SUPPORTED = ("parallel_tool_calls", "tool_choice")
_CODEX_RESPONSES_UNSUPPORTED = (
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "provider_sort",
)
_CHAT_API_MODES = {"chat_completions", "mistral_conversations"}


def build_generation_payload(
    *,
    provider: str,
    api_mode: str,
    params: GenerationParams,
    tools_enabled: bool,
) -> GenerationPayload:
    provider_id = provider.lower()
    request_overrides: dict[str, Any] = {}
    warnings: list[ProviderParamWarning] = []

    if api_mode == "anthropic_messages":
        temperature = params.temperature
        if (
            provider_id in {"anthropic", "github-copilot"}
            and temperature is not None
            and not 0.0 <= temperature <= 1.0
        ):
            warnings.append(
                ProviderParamWarning(
                    param="temperature",
                    action="dropped",
                    reason="Anthropic Messages temperature must be between 0 and 1.",
                )
            )
            temperature = None
        _copy_supported(params, request_overrides, _ANTHROPIC_DIRECT)
        if params.stop:
            request_overrides["stop_sequences"] = list(params.stop)
        _warn_if_set(
            params,
            warnings,
            "frequency_penalty",
            "dropped",
            "Anthropic Messages does not support frequency_penalty.",
        )
        _warn_if_set(
            params,
            warnings,
            "presence_penalty",
            "dropped",
            "Anthropic Messages does not support presence_penalty.",
        )
        _warn_if_set(params, warnings, "seed", "dropped", "Anthropic Messages does not support seed.")
        _warn_if_set(
            params,
            warnings,
            "parallel_tool_calls",
            "dropped",
            "Anthropic parallel tool control uses tool_choice.disable_parallel_tool_use.",
        )
        anthropic_tool_choice = _anthropic_tool_choice(params.tool_choice, warnings)
        if anthropic_tool_choice is not None:
            request_overrides["tool_choice"] = anthropic_tool_choice
        return GenerationPayload(
            temperature=temperature,
            max_tokens=params.max_tokens,
            request_overrides=request_overrides,
            warnings=warnings,
        )

    if api_mode == "openai_codex_responses":
        _copy_supported(params, request_overrides, _CODEX_RESPONSES_SUPPORTED)
        _drop_parallel_tools_without_tools(params, request_overrides, warnings, tools_enabled=tools_enabled)
        for name in _CODEX_RESPONSES_UNSUPPORTED:
            _warn_if_set(
                params,
                warnings,
                name,
                "dropped",
                f"Codex Responses does not accept {name}.",
            )
        return GenerationPayload(
            request_overrides=request_overrides,
            warnings=warnings,
        )

    if api_mode in {"openai_responses", "azure_openai_responses"}:
        _copy_supported(params, request_overrides, _RESPONSES_COMMON)
        _drop_parallel_tools_without_tools(params, request_overrides, warnings, tools_enabled=tools_enabled)
        if params.stop:
            _warn_if_set(
                params,
                warnings,
                "stop",
                "dropped",
                "Responses transport does not expose stop in travis yet.",
            )
        _warn_if_set(
            params,
            warnings,
            "frequency_penalty",
            "dropped",
            "Responses transport does not expose frequency_penalty in travis yet.",
        )
        _warn_if_set(
            params,
            warnings,
            "presence_penalty",
            "dropped",
            "Responses transport does not expose presence_penalty in travis yet.",
        )
        _warn_if_set(
            params,
            warnings,
            "seed",
            "dropped",
            "Responses transport does not expose seed in travis yet.",
        )
        return GenerationPayload(
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            request_overrides=request_overrides,
            warnings=warnings,
        )

    if api_mode in {"google_generative_ai", "google_vertex"}:
        if params.tool_choice is not None:
            request_overrides["toolConfig"] = {
                "functionCallingConfig": {"mode": params.tool_choice.upper()}
            }
        return GenerationPayload(
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            request_overrides=request_overrides,
            warnings=warnings,
        )

    if api_mode == "bedrock_converse_stream":
        return GenerationPayload(
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            request_overrides=request_overrides,
            warnings=warnings,
        )

    if api_mode not in _CHAT_API_MODES:
        raise ValueError(f"Unsupported api_mode for generation payload: {api_mode}")

    _copy_supported(params, request_overrides, _CHAT_COMMON)
    provider_preferences = _provider_preferences_for(provider_id, params, warnings)
    if params.stop:
        request_overrides["stop"] = list(params.stop)
    _drop_parallel_tools_without_tools(params, request_overrides, warnings, tools_enabled=tools_enabled)
    return GenerationPayload(
        temperature=params.temperature,
        max_tokens=params.max_tokens,
        provider_preferences=provider_preferences,
        request_overrides=request_overrides,
        warnings=warnings,
    )


def _provider_preferences_for(
    provider_id: str,
    params: GenerationParams,
    warnings: list[ProviderParamWarning],
) -> dict[str, Any] | None:
    if provider_id != "openrouter":
        if params.provider_sort:
            warnings.append(
                ProviderParamWarning(
                    param="provider_sort",
                    action="dropped",
                    reason=f"{provider_id} does not support provider routing sort preferences.",
                )
            )
        if params.provider_preferences:
            warnings.append(
                ProviderParamWarning(
                    param="provider_preferences",
                    action="dropped",
                    reason=f"{provider_id} does not support provider routing preferences.",
                )
            )
        return None

    preferences = dict(params.provider_preferences or {})
    if params.provider_sort:
        preferences["sort"] = params.provider_sort
    if preferences:
        preferences.setdefault("allow_fallbacks", True)
    return preferences or None


def _copy_supported(params: GenerationParams, target: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(params, name)
        if value is None:
            continue
        if name == "stop":
            continue
        target[name] = value


def _anthropic_tool_choice(
    value: str | None,
    warnings: list[ProviderParamWarning],
) -> dict[str, str] | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "required":
        warnings.append(
            ProviderParamWarning(
                param="tool_choice",
                action="translated",
                reason="Anthropic names required tool use 'any'.",
            )
        )
        return {"type": "any"}
    if normalized in _ANTHROPIC_TOOL_CHOICES:
        return {"type": normalized}
    warnings.append(
        ProviderParamWarning(
            param="tool_choice",
            action="dropped",
            reason="Anthropic tool_choice must be auto, any, none, or a structured named-tool choice.",
        )
    )
    return None


def _warn_if_set(
    params: GenerationParams,
    warnings: list[ProviderParamWarning],
    name: str,
    action: str,
    reason: str,
) -> None:
    value = getattr(params, name)
    if value is None:
        return
    if name == "stop" and value == ():
        return
    warnings.append(ProviderParamWarning(param=name, action=action, reason=reason))


def _drop_parallel_tools_without_tools(
    params: GenerationParams,
    request_overrides: dict[str, Any],
    warnings: list[ProviderParamWarning],
    *,
    tools_enabled: bool,
) -> None:
    if params.parallel_tool_calls is None or tools_enabled:
        return
    warnings.append(
        ProviderParamWarning(
            param="parallel_tool_calls",
            action="dropped",
            reason="parallel_tool_calls has no effect when tools are disabled.",
        )
    )
    request_overrides.pop("parallel_tool_calls", None)


__all__ = [
    "GenerationPayload",
    "ProviderParamWarning",
    "build_generation_payload",
]
