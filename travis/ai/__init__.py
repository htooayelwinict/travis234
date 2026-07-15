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
    AsyncModels,
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
from travis.ai.context_estimate import calculate_prompt_tokens, calculate_total_tokens
from travis.ai.stream_proxy import ProxyEventStream, stream_proxy
from travis.ai.image_types import GeneratedImage, ImageGenerationOptions, ImageModel
from travis.ai.images import (
    ImageGenerationError,
    generate_images,
    register_image_provider,
    unregister_image_provider,
)
__all__ = [
    "ApiKeyAuth",
    "AssistantMessageEventStream",
    "AuthContext",
    "AuthResult",
    "AsyncModels",
    "CredentialStore",
    "EventStream",
    "InMemoryCredentialStore",
    "GeneratedImage",
    "ImageGenerationError",
    "ImageGenerationOptions",
    "ImageModel",
    "ModelAuth",
    "Models",
    "ModelsError",
    "OAuthAuth",
    "Provider",
    "ProviderAuth",
    "ProviderStreams",
    "ProxyEventStream",
    "calculate_cost",
    "calculate_prompt_tokens",
    "calculate_total_tokens",
    "clamp_thinking_level",
    "create_assistant_message_event_stream",
    "create_models",
    "create_provider",
    "default_auth_context",
    "env_api_key_auth",
    "get_supported_thinking_levels",
    "generate_images",
    "is_context_overflow",
    "models_are_equal",
    "parse_available_output_tokens_from_error",
    "stream_proxy",
    "register_image_provider",
    "unregister_image_provider",
]
