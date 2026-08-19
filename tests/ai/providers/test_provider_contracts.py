from __future__ import annotations

import dataclasses
from types import MappingProxyType

CANONICAL_MODES = frozenset(
    {
        "anthropic-messages",
        "azure-openai-responses",
        "bedrock-converse-stream",
        "google-generative-ai",
        "google-vertex",
        "mistral-conversations",
        "openai-codex-responses",
        "openai-completions",
        "openai-responses",
    }
)

MODE_ALIASES = {
    "anthropic_messages": "anthropic-messages",
    "azure_openai_responses": "azure-openai-responses",
    "bedrock_converse": "bedrock-converse-stream",
    "bedrock_converse_stream": "bedrock-converse-stream",
    "chat_completions": "openai-completions",
    "google_generative_ai": "google-generative-ai",
    "google_vertex": "google-vertex",
    "mistral_conversations": "mistral-conversations",
    "openai_codex_responses": "openai-codex-responses",
    "openai_responses": "openai-responses",
}


def test_normalized_contracts_have_one_leaf_owner() -> None:
    from travis.ai.providers.provider_contracts import (
        NormalizedResponse,
        NormalizedToolCall,
        NormalizedUsage,
        ProviderTransport,
    )

    assert dataclasses.is_dataclass(NormalizedToolCall)
    assert dataclasses.is_dataclass(NormalizedUsage)
    assert dataclasses.is_dataclass(NormalizedResponse)
    assert NormalizedToolCall.__module__ == "travis.ai.providers.provider_contracts"
    assert NormalizedUsage.__module__ == "travis.ai.providers.provider_contracts"
    assert NormalizedResponse.__module__ == "travis.ai.providers.provider_contracts"
    assert ProviderTransport.__module__ == "travis.ai.providers.provider_contracts"


def test_provider_profile_has_one_declarative_owner() -> None:
    from travis.ai.providers.provider_profiles import ProviderProfile

    assert dataclasses.is_dataclass(ProviderProfile)
    assert ProviderProfile.__module__ == "travis.ai.providers.provider_profiles"


def test_mode_facts_match_the_existing_transport_surface() -> None:
    from travis.ai.providers.provider_modes import (
        API_MODE_ALIASES,
        CANONICAL_API_MODES,
        normalize_api_mode,
        transport_mode_is_supported,
    )

    assert CANONICAL_API_MODES == CANONICAL_MODES
    assert isinstance(API_MODE_ALIASES, MappingProxyType)
    assert dict(API_MODE_ALIASES) == MODE_ALIASES
    for mode in CANONICAL_MODES:
        assert normalize_api_mode(mode) == mode
        assert transport_mode_is_supported(mode)
    for alias, canonical in MODE_ALIASES.items():
        assert normalize_api_mode(alias) == canonical
        assert transport_mode_is_supported(alias)
    assert normalize_api_mode("future-provider") == "future-provider"
    assert not transport_mode_is_supported("future-provider")


def test_compatibility_modules_reexport_canonical_contract_objects() -> None:
    import travis.ai.providers as providers
    from travis.ai.providers import base
    from travis.ai.providers.provider_contracts import (
        NormalizedResponse,
        NormalizedToolCall,
        NormalizedUsage,
        ProviderTransport,
    )
    from travis.ai.providers.provider_profiles import ProviderProfile

    assert base.NormalizedToolCall is NormalizedToolCall
    assert base.NormalizedUsage is NormalizedUsage
    assert base.NormalizedResponse is NormalizedResponse
    assert base.ProviderTransport is ProviderTransport
    assert base.ProviderProfile is ProviderProfile
    assert providers.NormalizedToolCall is NormalizedToolCall
    assert providers.NormalizedUsage is NormalizedUsage
    assert providers.NormalizedResponse is NormalizedResponse
    assert providers.ProviderTransport is ProviderTransport
    assert providers.ProviderProfile is ProviderProfile
