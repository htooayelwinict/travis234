"""Side-effect-free Travis model, provider, and message interfaces."""

# Protocol value types are imported first so concrete runtime classes below
# cannot be shadowed by legacy type aliases such as ``Provider = str``.
from travis.ai.types import *  # noqa: F403 - package root exposes protocol types.

from travis.ai.auth import (
    ApiKeyAuth,
    AuthContext,
    AuthResult,
    CredentialStore,
    InMemoryCredentialStore,
    ModelAuth,
    ModelsError,
    OAuthAuth,
    ProviderAuth,
    default_auth_context,
    env_api_key_auth,
)
from travis.ai.event_stream import (
    AssistantMessageEventStream,
    EventStream,
    create_assistant_message_event_stream,
)
from travis.ai.models import (
    Models,
    Provider,
    ProviderStreams,
    calculate_cost,
    clamp_thinking_level,
    create_models,
    create_provider,
    get_supported_thinking_levels,
    models_are_equal,
)
from travis.ai.overflow import is_context_overflow, parse_available_output_tokens_from_error
__all__ = [
    "ApiKeyAuth",
    "AssistantMessageEventStream",
    "AuthContext",
    "AuthResult",
    "CredentialStore",
    "EventStream",
    "InMemoryCredentialStore",
    "ModelAuth",
    "Models",
    "ModelsError",
    "OAuthAuth",
    "Provider",
    "ProviderAuth",
    "ProviderStreams",
    "calculate_cost",
    "clamp_thinking_level",
    "create_assistant_message_event_stream",
    "create_models",
    "create_provider",
    "default_auth_context",
    "env_api_key_auth",
    "get_supported_thinking_levels",
    "is_context_overflow",
    "models_are_equal",
    "parse_available_output_tokens_from_error",
]
