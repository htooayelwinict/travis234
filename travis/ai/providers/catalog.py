"""Provider metadata and runtime resolution.

Model behavior belongs to the generated model catalog. This module owns only
provider names, authentication metadata, and base-URL selection.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from travis.ai.provider_metadata import PROVIDER_METADATA, PROVIDER_METADATA_BY_ID, normalize_provider_id
from travis.ai.providers.base import ProviderProfile


@dataclass(frozen=True)
class ProviderDef:
    id: str
    name: str
    transport: str
    api_key_env_vars: tuple[str, ...]
    base_url: str = ""
    base_url_env_var: str = ""
    is_aggregator: bool = False
    auth_type: str = "api_key"
    doc: str = ""
    source: str = "travis234"


@dataclass(frozen=True)
class ProviderDescriptor:
    slug: str
    label: str
    description: str
    auth_type: str
    tab: str
    api_key_env_vars: tuple[str, ...]
    base_url_env_var: str
    signup_url: str
    order: int


@dataclass(frozen=True)
class ProviderEntry:
    slug: str
    label: str
    tui_desc: str


@dataclass(frozen=True)
class ResolvedProviderRuntime:
    provider: str
    requested_provider: str
    profile: ProviderProfile
    api_mode: str
    transport: str
    endpoint_path: str
    base_url: str
    api_key_env_vars: tuple[str, ...]
    auth_type: str
    source: str


_API_TO_MODE = {
    "openai-completions": "chat_completions",
    "anthropic-messages": "anthropic_messages",
    "openai-responses": "openai_responses",
    "azure-openai-responses": "azure_openai_responses",
    "openai-codex-responses": "openai_codex_responses",
    "google-generative-ai": "google_generative_ai",
    "google-vertex": "google_vertex",
    "mistral-conversations": "mistral_conversations",
    "bedrock-converse-stream": "bedrock_converse_stream",
}
_MODE_TO_TRANSPORT = {
    "chat_completions": "openai_chat",
    "anthropic_messages": "anthropic_messages",
    "openai_responses": "openai_responses",
    "azure_openai_responses": "azure_openai_responses",
    "openai_codex_responses": "openai_codex_responses",
    "google_generative_ai": "google_generative_ai",
    "google_vertex": "google_vertex",
    "mistral_conversations": "mistral_conversations",
    "bedrock_converse_stream": "bedrock_converse_stream",
}
TRANSPORT_TO_API_MODE = {transport: mode for mode, transport in _MODE_TO_TRANSPORT.items()}
_OAUTH_AUTH_TYPES = {"oauth", "oauth_or_api_key"}

CANONICAL_PROVIDERS = tuple(
    ProviderEntry(provider.id, provider.name, provider.name)
    for provider in PROVIDER_METADATA
)
ALIASES = {
    alias: provider.id
    for provider in PROVIDER_METADATA
    for alias in provider.aliases
}

_REGISTRY: dict[str, ProviderProfile] = {}
_PROFILE_ALIASES: dict[str, str] = {}


def normalize_provider(name: str | None) -> str:
    normalized = normalize_provider_id(name)
    return _PROFILE_ALIASES.get(normalized, normalized)


def register_provider(profile: ProviderProfile) -> None:
    canonical = profile.name.strip().lower()
    _REGISTRY[canonical] = profile
    _PROFILE_ALIASES[canonical] = canonical
    for alias in profile.aliases:
        _PROFILE_ALIASES[str(alias).strip().lower()] = canonical


def get_provider_profile(name: str | None) -> ProviderProfile | None:
    return _REGISTRY.get(normalize_provider(name))


def list_provider_profiles() -> list[ProviderProfile]:
    return list(_REGISTRY.values())


def tab_for_auth_type(auth_type: str) -> str:
    return "accounts" if auth_type in _OAUTH_AUTH_TYPES else "keys"


def provider_catalog() -> list[ProviderDescriptor]:
    return [
        ProviderDescriptor(
            slug=provider.id,
            label=provider.name,
            description=provider.name,
            auth_type=provider.auth_type,
            tab=tab_for_auth_type(provider.auth_type),
            api_key_env_vars=provider.api_key_env_vars,
            base_url_env_var=provider.base_url_env_var,
            signup_url="",
            order=index,
        )
        for index, provider in enumerate(PROVIDER_METADATA)
    ]


def provider_catalog_by_slug() -> dict[str, ProviderDescriptor]:
    return {provider.slug: provider for provider in provider_catalog()}


def get_provider(name: str | None) -> ProviderDef | None:
    metadata = PROVIDER_METADATA_BY_ID.get(normalize_provider(name))
    if metadata is None:
        return None
    mode = _API_TO_MODE.get(metadata.api, "chat_completions")
    return ProviderDef(
        id=metadata.id,
        name=metadata.name,
        transport=_MODE_TO_TRANSPORT.get(mode, mode),
        api_key_env_vars=metadata.api_key_env_vars,
        base_url=metadata.base_url,
        base_url_env_var=metadata.base_url_env_var,
        is_aggregator=metadata.id == "openrouter",
        auth_type=metadata.auth_type,
    )


def is_aggregator(provider: str | None) -> bool:
    definition = get_provider(provider)
    return bool(definition and definition.is_aggregator)


def is_routing_aggregator(provider: str | None) -> bool:
    return normalize_provider(provider) == "openrouter"


def determine_api_mode(provider: str | None, base_url: str = "") -> str:
    del base_url
    metadata = PROVIDER_METADATA_BY_ID.get(normalize_provider(provider))
    return _API_TO_MODE.get(metadata.api, "chat_completions") if metadata else "chat_completions"


def resolve_user_provider(name: str, user_providers: dict[str, Any] | None) -> ProviderDef | None:
    if not isinstance(user_providers, dict):
        return None
    entry = user_providers.get(name)
    if not isinstance(entry, dict):
        return None
    api = str(entry.get("api") or "openai-completions")
    mode = _API_TO_MODE.get(api, str(entry.get("api_mode") or "chat_completions"))
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "")
    return ProviderDef(
        id=name,
        name=str(entry.get("name") or name),
        transport=_MODE_TO_TRANSPORT.get(mode, mode),
        api_key_env_vars=(key_env,) if key_env else (),
        base_url=str(entry.get("baseUrl") or entry.get("base_url") or entry.get("url") or ""),
        auth_type=str(entry.get("auth_type") or "api_key"),
        source="user-config",
    )


def custom_provider_slug(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(display_name or "").strip().lower()).strip("-")
    return f"custom:{slug or 'provider'}"


def resolve_custom_provider(name: str, custom_providers: list[dict[str, Any]] | None) -> ProviderDef | None:
    if not isinstance(custom_providers, list):
        return None
    requested = str(name or "").strip().lower()
    first: ProviderDef | None = None
    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue
        display_name = str(entry.get("name") or entry.get("label") or "").strip()
        base_url = str(entry.get("baseUrl") or entry.get("base_url") or entry.get("url") or "").strip()
        if not display_name or not base_url:
            continue
        slug = custom_provider_slug(display_name)
        api = str(entry.get("api") or "openai-completions")
        mode = _API_TO_MODE.get(api, str(entry.get("api_mode") or "chat_completions"))
        key_env = str(entry.get("api_key_env") or entry.get("key_env") or "")
        candidate = ProviderDef(
            id=slug,
            name=display_name,
            transport=_MODE_TO_TRANSPORT.get(mode, mode),
            api_key_env_vars=(key_env,) if key_env else (),
            base_url=base_url,
            is_aggregator=True,
            auth_type=str(entry.get("auth_type") or "api_key"),
            source="custom-provider",
        )
        first = first or candidate
        if requested in {display_name.lower(), slug}:
            return candidate
    return first if requested == "custom" else None


def resolve_provider_full(
    name: str,
    user_providers: dict[str, Any] | None = None,
    custom_providers: list[dict[str, Any]] | None = None,
) -> ProviderDef | None:
    requested = str(name or "").strip().lower()
    if not requested:
        return None
    return (
        resolve_user_provider(requested, user_providers)
        or get_provider(requested)
        or resolve_custom_provider(requested, custom_providers)
    )


def resolve_provider_runtime(
    provider: str | None,
    *,
    explicit_base_url: str | None = None,
    fallback_base_url: str | None = None,
    user_providers: dict[str, Any] | None = None,
    custom_providers: list[dict[str, Any]] | None = None,
) -> ResolvedProviderRuntime:
    requested = str(provider or "").strip() or "custom"
    definition = resolve_provider_full(
        requested,
        user_providers=user_providers,
        custom_providers=custom_providers,
    )
    if definition is None:
        definition = ProviderDef(
            id=normalize_provider(requested) or "custom",
            name=requested,
            transport="openai_chat",
            api_key_env_vars=(),
            source="custom-fallback",
        )
    profile = get_provider_profile(definition.id) or ProviderProfile(
        name=definition.id,
        display_name=definition.name,
        env_vars=definition.api_key_env_vars,
        base_url=definition.base_url,
        auth_type=definition.auth_type,
    )
    env_base_url = os.environ.get(definition.base_url_env_var, "").strip() if definition.base_url_env_var else ""
    base_url = (
        str(explicit_base_url or "").strip()
        or env_base_url
        or definition.base_url
        or str(fallback_base_url or "").strip()
    )
    mode = determine_api_mode(definition.id)
    from travis.ai.providers.transports import get_transport

    transport = get_transport(mode)
    return ResolvedProviderRuntime(
        provider=definition.id,
        requested_provider=requested,
        profile=profile,
        api_mode=mode,
        transport=definition.transport,
        endpoint_path=str(getattr(transport, "endpoint_path", "/chat/completions")),
        base_url=base_url,
        api_key_env_vars=definition.api_key_env_vars,
        auth_type=definition.auth_type,
        source=definition.source,
    )


for metadata in PROVIDER_METADATA:
    register_provider(
        ProviderProfile(
            name=metadata.id,
            api_mode=_API_TO_MODE.get(metadata.api, "chat_completions"),
            aliases=metadata.aliases,
            display_name=metadata.name,
            description=metadata.name,
            env_vars=metadata.api_key_env_vars,
            base_url=metadata.base_url,
            auth_type=metadata.auth_type,
            default_max_tokens=65_536 if metadata.id == "custom" else None,
        )
    )


__all__ = [
    "ALIASES",
    "CANONICAL_PROVIDERS",
    "ProviderDef",
    "ProviderDescriptor",
    "ProviderEntry",
    "ResolvedProviderRuntime",
    "TRANSPORT_TO_API_MODE",
    "custom_provider_slug",
    "determine_api_mode",
    "get_provider",
    "get_provider_profile",
    "is_aggregator",
    "is_routing_aggregator",
    "list_provider_profiles",
    "normalize_provider",
    "provider_catalog",
    "provider_catalog_by_slug",
    "register_provider",
    "resolve_custom_provider",
    "resolve_provider_full",
    "resolve_provider_runtime",
    "resolve_user_provider",
    "tab_for_auth_type",
]
