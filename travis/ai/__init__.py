"""Side-effect-free Travis model, provider, and message interfaces."""

# Protocol value types are imported first so concrete runtime classes below
# cannot be shadowed by legacy type aliases such as ``Provider = str``.
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
from travis.ai.context_estimate import calculate_prompt_tokens, calculate_total_tokens
from travis.ai.event_stream import (
    AssistantMessageEventStream,
    EventStream,
    create_assistant_message_event_stream,
)
from travis.ai.image_types import GeneratedImage, ImageGenerationOptions, ImageModel
from travis.ai.images import (
    ImageGenerationError,
    generate_images,
    register_image_provider,
    unregister_image_provider,
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
from travis.ai.stream_proxy import ProxyEventStream, stream_proxy
from travis.ai.types import (
    Api as Api,
)
from travis.ai.types import (
    AssistantMessage as AssistantMessage,
)
from travis.ai.types import (
    AssistantMessageEvent as AssistantMessageEvent,
)
from travis.ai.types import (
    ContentBlock as ContentBlock,
)
from travis.ai.types import (
    Context as Context,
)
from travis.ai.types import (
    Cost as Cost,
)
from travis.ai.types import (
    CostTier as CostTier,
)
from travis.ai.types import (
    DoneEvent as DoneEvent,
)
from travis.ai.types import (
    ErrorEvent as ErrorEvent,
)
from travis.ai.types import (
    ImageContent as ImageContent,
)
from travis.ai.types import (
    Message as Message,
)
from travis.ai.types import (
    Model as Model,
)
from travis.ai.types import (
    ProviderResponse as ProviderResponse,
)
from travis.ai.types import (
    SimpleStreamOptions as SimpleStreamOptions,
)
from travis.ai.types import (
    StartEvent as StartEvent,
)
from travis.ai.types import (
    StopReason as StopReason,
)
from travis.ai.types import (
    StreamOptions as StreamOptions,
)
from travis.ai.types import (
    TextContent as TextContent,
)
from travis.ai.types import (
    TextDeltaEvent as TextDeltaEvent,
)
from travis.ai.types import (
    TextEndEvent as TextEndEvent,
)
from travis.ai.types import (
    TextStartEvent as TextStartEvent,
)
from travis.ai.types import (
    ThinkingContent as ThinkingContent,
)
from travis.ai.types import (
    ThinkingDeltaEvent as ThinkingDeltaEvent,
)
from travis.ai.types import (
    ThinkingEndEvent as ThinkingEndEvent,
)
from travis.ai.types import (
    ThinkingLevel as ThinkingLevel,
)
from travis.ai.types import (
    ThinkingStartEvent as ThinkingStartEvent,
)
from travis.ai.types import (
    Tool as Tool,
)
from travis.ai.types import (
    ToolCall as ToolCall,
)
from travis.ai.types import (
    ToolcallDeltaEvent as ToolcallDeltaEvent,
)
from travis.ai.types import (
    ToolcallEndEvent as ToolcallEndEvent,
)
from travis.ai.types import (
    ToolcallStartEvent as ToolcallStartEvent,
)
from travis.ai.types import (
    ToolResultMessage as ToolResultMessage,
)
from travis.ai.types import (
    Transport as Transport,
)
from travis.ai.types import (
    Usage as Usage,
)
from travis.ai.types import (
    UserMessage as UserMessage,
)
from travis.ai.types import (
    empty_usage as empty_usage,
)
from travis.ai.types import (
    now_ms as now_ms,
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
