"""provider transports for travis."""

from __future__ import annotations

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

def get_transport(api_mode: str):
    """Compatibility wrapper for the registry-owned lookup function."""

    from travis.ai.providers.transport_registry import get_transport as registry_get_transport

    return registry_get_transport(api_mode)


__all__ = [
    "AnthropicMessagesTransport",
    "AzureOpenAIResponsesTransport",
    "BedrockConverseStreamTransport",
    "ChatCompletionsTransport",
    "CodexResponsesTransport",
    "GoogleGenerativeAITransport",
    "GoogleVertexTransport",
    "MistralConversationsTransport",
    "OpenAIResponsesTransport",
    "UnsupportedTransport",
    "get_transport",
]
