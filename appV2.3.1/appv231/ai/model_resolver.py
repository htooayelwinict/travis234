"""Model resolution helpers. Port of pi coding-agent core/model-resolver.ts."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from dataclasses import replace
from fnmatch import fnmatchcase
from typing import Protocol

from appv231.ai.env_config import DEFAULT_MODEL_PER_PROVIDER
from appv231.ai.providers.catalog import get_provider_profile, list_provider_profiles, normalize_provider
from appv231.ai.types import Model

DEFAULT_THINKING_LEVEL = "off"
VALID_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh"}


class ModelRegistryLike(Protocol):
    def get_all(self) -> list[Model]: ...
    def get_available(self) -> list[Model]: ...
    def find(self, provider: str, model_id: str) -> Model | None: ...
    def has_configured_auth(self, model: Model) -> bool: ...


@dataclass
class ScopedModel:
    model: Model
    thinking_level: str | None = None


@dataclass
class ParsedModelResult:
    model: Model | None
    thinking_level: str | None = None
    warning: str | None = None


@dataclass
class ResolveCliModelResult:
    model: Model | None
    thinking_level: str | None = None
    warning: str | None = None
    error: str | None = None


@dataclass
class InitialModelResult:
    model: Model | None
    thinking_level: str = DEFAULT_THINKING_LEVEL
    fallback_message: str | None = None


def find_exact_model_reference_match(model_reference: str, available_models: list[Model]) -> Model | None:
    trimmed_reference = model_reference.strip()
    if not trimmed_reference:
        return None

    normalized_reference = trimmed_reference.lower()
    canonical_matches = [
        model
        for model in available_models
        if f"{model.provider}/{model.id}".lower() == normalized_reference
    ]
    if len(canonical_matches) == 1:
        return canonical_matches[0]
    if len(canonical_matches) > 1:
        return None

    slash_index = trimmed_reference.find("/")
    if slash_index != -1:
        provider = trimmed_reference[:slash_index].strip()
        model_id = trimmed_reference[slash_index + 1 :].strip()
        if provider and model_id:
            provider_matches = [
                model
                for model in available_models
                if model.provider.lower() == provider.lower() and model.id.lower() == model_id.lower()
            ]
            if len(provider_matches) == 1:
                return provider_matches[0]
            if len(provider_matches) > 1:
                return None

    id_matches = [model for model in available_models if model.id.lower() == normalized_reference]
    return id_matches[0] if len(id_matches) == 1 else None


def parse_model_pattern(
    pattern: str,
    available_models: list[Model],
    *,
    allow_invalid_thinking_level_fallback: bool = True,
) -> ParsedModelResult:
    exact_match = _try_match_model(pattern, available_models)
    if exact_match is not None:
        return ParsedModelResult(model=exact_match)

    last_colon_index = pattern.rfind(":")
    if last_colon_index == -1:
        return ParsedModelResult(model=None)

    prefix = pattern[:last_colon_index]
    suffix = pattern[last_colon_index + 1 :]

    if _is_valid_thinking_level(suffix):
        result = parse_model_pattern(
            prefix,
            available_models,
            allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback,
        )
        if result.model is not None:
            return ParsedModelResult(
                model=result.model,
                thinking_level=None if result.warning else suffix,
                warning=result.warning,
            )
        return result

    if not allow_invalid_thinking_level_fallback:
        return ParsedModelResult(model=None)

    result = parse_model_pattern(
        prefix,
        available_models,
        allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback,
    )
    if result.model is not None:
        return ParsedModelResult(
            model=result.model,
            warning=f'Invalid thinking level "{suffix}" in pattern "{pattern}". Using default instead.',
        )
    return result


def resolve_model_scope(patterns: list[str], model_registry: object) -> list[ScopedModel]:
    available_models = _registry_get_available(model_registry)
    scoped_models: list[ScopedModel] = []

    for pattern in patterns:
        if _has_glob_characters(pattern):
            glob_pattern = pattern
            thinking_level: str | None = None
            colon_index = pattern.rfind(":")
            if colon_index != -1:
                suffix = pattern[colon_index + 1 :]
                if _is_valid_thinking_level(suffix):
                    thinking_level = suffix
                    glob_pattern = pattern[:colon_index]

            matching_models = [
                model
                for model in available_models
                if _glob_matches(f"{model.provider}/{model.id}", glob_pattern)
                or _glob_matches(model.id, glob_pattern)
            ]
            if not matching_models:
                _warn_no_models_match(pattern)
                continue

            for model in matching_models:
                if not _scoped_contains(scoped_models, model):
                    scoped_models.append(ScopedModel(model=model, thinking_level=thinking_level))
            continue

        parsed = parse_model_pattern(pattern, available_models)
        if parsed.warning:
            print(f"Warning: {parsed.warning}", file=sys.stderr)
        if parsed.model is None:
            _warn_no_models_match(pattern)
            continue
        if not _scoped_contains(scoped_models, parsed.model):
            scoped_models.append(ScopedModel(model=parsed.model, thinking_level=parsed.thinking_level))

    return scoped_models


def resolve_cli_model(
    *,
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_thinking: str | None = None,
    model_registry: object,
) -> ResolveCliModelResult:
    if not cli_model:
        return ResolveCliModelResult(model=None)

    available_models = _registry_get_all(model_registry)
    provider_map = {model.provider.lower(): model.provider for model in available_models}
    known_provider_profiles = {profile.name.lower(): profile.name for profile in list_provider_profiles()}
    provider_map.update({key: value for key, value in known_provider_profiles.items() if key not in provider_map})
    if cli_provider:
        normalized_cli_provider = normalize_provider(cli_provider)
        if get_provider_profile(normalized_cli_provider):
            provider_map.setdefault(cli_provider.lower(), normalized_cli_provider)
            provider_map.setdefault(normalized_cli_provider.lower(), normalized_cli_provider)
    provider = provider_map.get(cli_provider.lower()) if cli_provider else None
    if cli_provider and not provider:
        return ResolveCliModelResult(
            model=None,
            error=f'Unknown provider "{cli_provider}". Use --list-models to see available providers/models.',
        )

    pattern = cli_model
    inferred_provider = False
    if not provider:
        slash_index = cli_model.find("/")
        if slash_index != -1:
            maybe_provider = cli_model[:slash_index]
            canonical = provider_map.get(maybe_provider.lower())
            if canonical is None:
                normalized = normalize_provider(maybe_provider)
                if get_provider_profile(normalized):
                    canonical = provider_map.get(normalized.lower()) or normalized
            if canonical:
                provider = canonical
                pattern = cli_model[slash_index + 1 :]
                inferred_provider = True

    if not provider:
        lower = cli_model.lower()
        exact = next(
            (
                model
                for model in available_models
                if model.id.lower() == lower or f"{model.provider}/{model.id}".lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(model=exact)

    if cli_provider and provider:
        prefix = f"{provider}/"
        if cli_model.lower().startswith(prefix.lower()):
            pattern = cli_model[len(prefix) :]

    candidates = [model for model in available_models if model.provider == provider] if provider else available_models
    parsed = parse_model_pattern(pattern, candidates, allow_invalid_thinking_level_fallback=False)
    if parsed.model is not None:
        if inferred_provider:
            raw_exact_matches = [
                model
                for model in available_models
                if model.id.lower() == cli_model.lower() and not _models_are_equal(model, parsed.model)
            ]
            if raw_exact_matches and not _registry_has_configured_auth(model_registry, parsed.model):
                authenticated_raw_matches = [
                    model for model in raw_exact_matches if _registry_has_configured_auth(model_registry, model)
                ]
                if len(authenticated_raw_matches) == 1:
                    return ResolveCliModelResult(model=authenticated_raw_matches[0])
        return ResolveCliModelResult(model=parsed.model, thinking_level=parsed.thinking_level, warning=parsed.warning)

    if inferred_provider:
        lower = cli_model.lower()
        exact = next(
            (
                model
                for model in available_models
                if model.id.lower() == lower or f"{model.provider}/{model.id}".lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(model=exact)

        fallback = parse_model_pattern(cli_model, available_models, allow_invalid_thinking_level_fallback=False)
        if fallback.model is not None:
            return ResolveCliModelResult(
                model=fallback.model,
                thinking_level=fallback.thinking_level,
                warning=fallback.warning,
            )

    if provider:
        fallback_pattern = pattern
        fallback_thinking: str | None = None
        if not cli_thinking:
            last_colon = pattern.rfind(":")
            if last_colon != -1:
                suffix = pattern[last_colon + 1 :]
                if _is_valid_thinking_level(suffix):
                    fallback_pattern = pattern[:last_colon]
                    fallback_thinking = suffix

        fallback_model = _build_fallback_model(provider, fallback_pattern, available_models)
        if fallback_model is not None:
            requested_thinking = cli_thinking or fallback_thinking
            model = (
                replace(fallback_model, reasoning=True)
                if requested_thinking and requested_thinking != DEFAULT_THINKING_LEVEL
                else fallback_model
            )
            warning = (
                f'{parsed.warning} Model "{fallback_pattern}" not found for provider "{provider}". '
                "Using custom model id."
                if parsed.warning
                else f'Model "{fallback_pattern}" not found for provider "{provider}". Using custom model id.'
            )
            return ResolveCliModelResult(model=model, thinking_level=fallback_thinking, warning=warning)

    display = f"{provider}/{pattern}" if provider else cli_model
    return ResolveCliModelResult(
        model=None,
        thinking_level=None,
        warning=parsed.warning,
        error=f'Model "{display}" not found. Use --list-models to see available models.',
    )


def find_initial_model(
    *,
    scoped_models: list[ScopedModel],
    is_continuing: bool,
    model_registry: object,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    default_provider: str | None = None,
    default_model_id: str | None = None,
    default_thinking_level: str | None = None,
) -> InitialModelResult:
    if cli_provider and cli_model:
        resolved = resolve_cli_model(cli_provider=cli_provider, cli_model=cli_model, model_registry=model_registry)
        if resolved.error:
            return InitialModelResult(model=None, fallback_message=resolved.error)
        if resolved.model is not None:
            return InitialModelResult(model=resolved.model)

    if scoped_models and not is_continuing:
        scoped = scoped_models[0]
        return InitialModelResult(
            model=scoped.model,
            thinking_level=scoped.thinking_level or default_thinking_level or DEFAULT_THINKING_LEVEL,
        )

    if default_provider and default_model_id:
        found = _registry_find(model_registry, default_provider, default_model_id)
        if found is not None:
            return InitialModelResult(
                model=found,
                thinking_level=default_thinking_level or DEFAULT_THINKING_LEVEL,
            )

    available_models = _registry_get_available(model_registry)
    if available_models:
        default_model = _first_default_model(available_models)
        return InitialModelResult(model=default_model or available_models[0])

    return InitialModelResult(model=None)


def _try_match_model(model_pattern: str, available_models: list[Model]) -> Model | None:
    exact_match = find_exact_model_reference_match(model_pattern, available_models)
    if exact_match is not None:
        return exact_match

    lowered_pattern = model_pattern.lower()
    matches = [
        model
        for model in available_models
        if lowered_pattern in model.id.lower() or lowered_pattern in (model.name or "").lower()
    ]
    if not matches:
        return None

    aliases = [model for model in matches if _is_alias(model.id)]
    dated_versions = [model for model in matches if not _is_alias(model.id)]
    if aliases:
        return sorted(aliases, key=lambda model: model.id, reverse=True)[0]
    return sorted(dated_versions, key=lambda model: model.id, reverse=True)[0]


def _build_fallback_model(provider: str, model_id: str, available_models: list[Model]) -> Model | None:
    provider_models = [model for model in available_models if model.provider == provider]
    if provider_models:
        default_id = DEFAULT_MODEL_PER_PROVIDER.get(provider)
        base_model = next((model for model in provider_models if model.id == default_id), provider_models[0])
        return replace(base_model, id=model_id, name=model_id)

    profile = get_provider_profile(provider)
    if profile is None:
        return None
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url=profile.base_url or "",
        reasoning=False,
        input=["text"],
        context_window=128000,
        max_tokens=profile.default_max_tokens or 8192,
    )


def _first_default_model(models: list[Model]) -> Model | None:
    for provider, default_id in DEFAULT_MODEL_PER_PROVIDER.items():
        match = next((model for model in models if model.provider == provider and model.id == default_id), None)
        if match is not None:
            return match
    return None


def _is_alias(model_id: str) -> bool:
    if model_id.endswith("-latest"):
        return True
    return re.search(r"-\d{8}$", model_id) is None


def _is_valid_thinking_level(level: str) -> bool:
    return level in VALID_THINKING_LEVELS


def _models_are_equal(left: Model, right: Model) -> bool:
    return left.provider == right.provider and left.id == right.id


def _scoped_contains(scoped_models: list[ScopedModel], model: Model) -> bool:
    return any(_models_are_equal(scoped.model, model) for scoped in scoped_models)


def _has_glob_characters(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern or "[" in pattern


def _glob_matches(value: str, pattern: str) -> bool:
    return fnmatchcase(value.lower(), pattern.lower())


def _warn_no_models_match(pattern: str) -> None:
    print(f'Warning: No models match pattern "{pattern}"', file=sys.stderr)


def _registry_get_all(registry: object) -> list[Model]:
    method = getattr(registry, "get_all", None) or getattr(registry, "getAll", None)
    return list(method()) if callable(method) else []


def _registry_get_available(registry: object) -> list[Model]:
    method = getattr(registry, "get_available", None) or getattr(registry, "getAvailable", None)
    if callable(method):
        return list(method())
    return _registry_get_all(registry)


def _registry_find(registry: object, provider: str, model_id: str) -> Model | None:
    method = getattr(registry, "find", None)
    return method(provider, model_id) if callable(method) else None


def _registry_has_configured_auth(registry: object, model: Model) -> bool:
    method = getattr(registry, "has_configured_auth", None) or getattr(registry, "hasConfiguredAuth", None)
    return bool(method(model)) if callable(method) else True
