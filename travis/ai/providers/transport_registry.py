"""Immutable registry for provider transport singletons."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TypeAlias

from travis.ai.providers.provider_modes import CANONICAL_API_MODES, normalize_api_mode
from travis.ai.providers.transport_families.anthropic import AnthropicMessagesTransport
from travis.ai.providers.transport_families.azure_responses import AzureOpenAIResponsesTransport
from travis.ai.providers.transport_families.bedrock import BedrockConverseStreamTransport
from travis.ai.providers.transport_families.chat_completions import ChatCompletionsTransport
from travis.ai.providers.transport_families.google import (
    GoogleGenerativeAITransport,
    GoogleVertexTransport,
)
from travis.ai.providers.transport_families.mistral import MistralConversationsTransport
from travis.ai.providers.transport_families.responses import (
    CodexResponsesTransport,
    OpenAIResponsesTransport,
)
from travis.ai.providers.transport_families.unsupported import UnsupportedTransport


RegisteredTransport: TypeAlias = (
    AnthropicMessagesTransport
    | AzureOpenAIResponsesTransport
    | BedrockConverseStreamTransport
    | ChatCompletionsTransport
    | CodexResponsesTransport
    | GoogleGenerativeAITransport
    | GoogleVertexTransport
    | MistralConversationsTransport
    | OpenAIResponsesTransport
)
TransportLookup: TypeAlias = RegisteredTransport | UnsupportedTransport


def _build_registry(
    entries: Iterable[tuple[str, RegisteredTransport]],
) -> Mapping[str, RegisteredTransport]:
    registry: dict[str, RegisteredTransport] = {}
    for mode, transport in sorted(entries, key=lambda item: item[0]):
        if mode in registry:
            raise ValueError(f"duplicate transport mode: {mode}")
        registry[mode] = transport
    return MappingProxyType(registry)


DEFAULT_TRANSPORT_REGISTRY: Mapping[str, RegisteredTransport] = _build_registry(
    (
        (AnthropicMessagesTransport.api, AnthropicMessagesTransport()),
        (AzureOpenAIResponsesTransport.api, AzureOpenAIResponsesTransport()),
        (BedrockConverseStreamTransport.api, BedrockConverseStreamTransport()),
        (ChatCompletionsTransport.api, ChatCompletionsTransport()),
        (CodexResponsesTransport.api, CodexResponsesTransport()),
        (GoogleGenerativeAITransport.api, GoogleGenerativeAITransport()),
        (GoogleVertexTransport.api, GoogleVertexTransport()),
        (MistralConversationsTransport.api, MistralConversationsTransport()),
        (OpenAIResponsesTransport.api, OpenAIResponsesTransport()),
    )
)

if set(DEFAULT_TRANSPORT_REGISTRY) != set(CANONICAL_API_MODES):
    raise RuntimeError("default transport registry does not match canonical API modes")


def get_transport(api_mode: str) -> TransportLookup:
    """Return the immutable singleton for a supported mode or an owned fallback."""

    normalized = normalize_api_mode(api_mode)
    return DEFAULT_TRANSPORT_REGISTRY.get(normalized) or UnsupportedTransport(normalized)


__all__ = [
    "DEFAULT_TRANSPORT_REGISTRY",
    "RegisteredTransport",
    "TransportLookup",
    "get_transport",
]
