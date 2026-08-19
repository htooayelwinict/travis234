"""Canonical provider transport modes and compatibility aliases."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

CANONICAL_API_MODES: frozenset[str] = frozenset(
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

API_MODE_ALIASES: Mapping[str, str] = MappingProxyType(
    {
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
)


def normalize_api_mode(value: str) -> str:
    """Return the canonical transport mode for a public mode or alias."""

    return API_MODE_ALIASES.get(value, value)


def transport_mode_is_supported(value: str) -> bool:
    """Report whether *value* names a built-in transport mode."""

    return normalize_api_mode(value) in CANONICAL_API_MODES


__all__ = [
    "API_MODE_ALIASES",
    "CANONICAL_API_MODES",
    "normalize_api_mode",
    "transport_mode_is_supported",
]
