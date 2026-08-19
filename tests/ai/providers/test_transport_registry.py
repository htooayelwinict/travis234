from __future__ import annotations

from types import MappingProxyType
from typing import Any, get_args, get_type_hints

import pytest


EXPECTED_TRANSPORT_FACTS = {
    "anthropic-messages": ("AnthropicMessagesTransport", "anthropic_messages", "/v1/messages"),
    "azure-openai-responses": (
        "AzureOpenAIResponsesTransport",
        "azure_openai_responses",
        "/responses",
    ),
    "bedrock-converse-stream": ("BedrockConverseStreamTransport", "bedrock_converse_stream", ""),
    "google-generative-ai": ("GoogleGenerativeAITransport", "google_generative_ai", ""),
    "google-vertex": ("GoogleVertexTransport", "google_vertex", ""),
    "mistral-conversations": ("MistralConversationsTransport", "mistral_conversations", "/chat/completions"),
    "openai-codex-responses": ("CodexResponsesTransport", "openai_codex_responses", "/responses"),
    "openai-completions": ("ChatCompletionsTransport", "chat_completions", "/chat/completions"),
    "openai-responses": ("OpenAIResponsesTransport", "openai_responses", "/responses"),
}


def _contains_any(annotation: object) -> bool:
    return annotation is Any or any(_contains_any(argument) for argument in get_args(annotation))


def test_transport_registry_annotations_do_not_erase_the_boundary_to_any() -> None:
    from travis.ai.providers import transport_registry

    module_hints = get_type_hints(transport_registry)
    assert "DEFAULT_TRANSPORT_REGISTRY" in module_hints
    annotations = {
        *module_hints.values(),
        *get_type_hints(transport_registry._build_registry).values(),
        *get_type_hints(transport_registry.get_transport).values(),
    }

    assert not any(_contains_any(annotation) for annotation in annotations)


def test_default_transport_registry_is_immutable_complete_and_deterministic() -> None:
    from travis.ai.providers.provider_modes import CANONICAL_API_MODES
    from travis.ai.providers.transport_registry import DEFAULT_TRANSPORT_REGISTRY

    assert isinstance(DEFAULT_TRANSPORT_REGISTRY, MappingProxyType)
    assert tuple(DEFAULT_TRANSPORT_REGISTRY) == tuple(sorted(CANONICAL_API_MODES))
    assert set(DEFAULT_TRANSPORT_REGISTRY) == set(EXPECTED_TRANSPORT_FACTS)


def test_registry_preserves_canonical_transport_class_and_endpoint_facts() -> None:
    from travis.ai.providers.transport_families.unsupported import UnsupportedTransport
    from travis.ai.providers.transport_registry import get_transport

    for mode, (class_name, api_mode, endpoint_path) in EXPECTED_TRANSPORT_FACTS.items():
        transport = get_transport(mode)
        assert not isinstance(transport, UnsupportedTransport)
        assert type(transport).__name__ == class_name
        assert transport.api == mode
        assert transport.api_mode == api_mode
        assert transport.endpoint_path == endpoint_path
        assert get_transport(mode) is transport


def test_every_alias_resolves_to_the_canonical_singleton() -> None:
    from travis.ai.providers.provider_modes import API_MODE_ALIASES
    from travis.ai.providers.transport_registry import get_transport

    for alias, canonical in API_MODE_ALIASES.items():
        assert get_transport(alias) is get_transport(canonical)


def test_unknown_mode_returns_owned_unsupported_transport_with_normalized_value() -> None:
    from travis.ai.providers.transport_families.unsupported import UnsupportedTransport
    from travis.ai.providers.transport_registry import get_transport

    transport = get_transport("future_provider")

    assert type(transport) is UnsupportedTransport
    assert transport.api_mode == "future_provider"
    assert transport.endpoint_path == "/unsupported"


def test_registry_builder_rejects_duplicate_modes() -> None:
    from travis.ai.providers.transport_registry import _build_registry
    from travis.ai.providers.transports import ChatCompletionsTransport

    first = ChatCompletionsTransport()
    second = ChatCompletionsTransport()

    with pytest.raises(ValueError, match="duplicate transport mode: duplicate"):
        _build_registry((("duplicate", first), ("duplicate", second)))


def test_compatibility_surface_reexports_registry_function_and_unsupported_owner() -> None:
    import travis.ai.providers as providers
    from travis.ai.providers import transports
    from travis.ai.providers.transport_families.unsupported import UnsupportedTransport
    from travis.ai.providers.transport_registry import get_transport

    assert providers.get_transport is get_transport
    assert transports.UnsupportedTransport is UnsupportedTransport
    assert transports.get_transport("chat_completions") is get_transport("openai-completions")


def test_chat_and_mistral_transports_have_family_owners_and_compatibility_exports() -> None:
    from travis.ai.providers import transports
    from travis.ai.providers.transport_families.chat_completions import ChatCompletionsTransport
    from travis.ai.providers.transport_families.mistral import MistralConversationsTransport

    assert ChatCompletionsTransport.__module__ == (
        "travis.ai.providers.transport_families.chat_completions"
    )
    assert MistralConversationsTransport.__module__ == "travis.ai.providers.transport_families.mistral"
    assert transports.ChatCompletionsTransport is ChatCompletionsTransport
    assert transports.MistralConversationsTransport is MistralConversationsTransport


def test_google_and_bedrock_transports_have_family_owners_and_compatibility_exports() -> None:
    from travis.ai.providers import transports
    from travis.ai.providers.transport_families.bedrock import BedrockConverseStreamTransport
    from travis.ai.providers.transport_families.google import (
        GoogleGenerativeAITransport,
        GoogleVertexTransport,
    )

    assert GoogleGenerativeAITransport.__module__ == "travis.ai.providers.transport_families.google"
    assert GoogleVertexTransport.__module__ == "travis.ai.providers.transport_families.google"
    assert BedrockConverseStreamTransport.__module__ == "travis.ai.providers.transport_families.bedrock"
    assert transports.GoogleGenerativeAITransport is GoogleGenerativeAITransport
    assert transports.GoogleVertexTransport is GoogleVertexTransport
    assert transports.BedrockConverseStreamTransport is BedrockConverseStreamTransport


def test_anthropic_transport_has_family_owner_and_compatibility_export() -> None:
    from travis.ai.providers import transports
    from travis.ai.providers.transport_families.anthropic import AnthropicMessagesTransport

    assert AnthropicMessagesTransport.__module__ == "travis.ai.providers.transport_families.anthropic"
    assert transports.AnthropicMessagesTransport is AnthropicMessagesTransport
