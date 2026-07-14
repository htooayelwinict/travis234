"""Per-model OpenAI Chat Completions compatibility resolution.

This compatibility boundary is conservative: provider detection supplies
defaults and generated model metadata wins field by
field.  Provider discovery must never infer behavioral capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from travis.ai.types import Model


@dataclass(frozen=True)
class OpenAICompat:
    supports_store: bool = True
    supports_developer_role: bool = True
    supports_reasoning_effort: bool = True
    supports_usage_in_streaming: bool = True
    max_tokens_field: str = "max_completion_tokens"
    requires_tool_result_name: bool = False
    requires_assistant_after_tool_result: bool = False
    requires_thinking_as_text: bool = False
    requires_reasoning_content_on_assistant_messages: bool = False
    thinking_format: str = "openai"
    openrouter_routing: dict[str, Any] = field(default_factory=dict)
    vercel_gateway_routing: dict[str, Any] = field(default_factory=dict)
    chat_template_kwargs: dict[str, Any] = field(default_factory=dict)
    zai_tool_stream: bool = False
    supports_strict_mode: bool = True
    cache_control_format: str | None = None
    send_session_affinity_headers: bool = False
    session_affinity_format: str = "openai"
    supports_long_cache_retention: bool = True


_CATALOG_KEYS = {
    "supports_store": "supportsStore",
    "supports_developer_role": "supportsDeveloperRole",
    "supports_reasoning_effort": "supportsReasoningEffort",
    "supports_usage_in_streaming": "supportsUsageInStreaming",
    "max_tokens_field": "maxTokensField",
    "requires_tool_result_name": "requiresToolResultName",
    "requires_assistant_after_tool_result": "requiresAssistantAfterToolResult",
    "requires_thinking_as_text": "requiresThinkingAsText",
    "requires_reasoning_content_on_assistant_messages": "requiresReasoningContentOnAssistantMessages",
    "thinking_format": "thinkingFormat",
    "openrouter_routing": "openRouterRouting",
    "vercel_gateway_routing": "vercelGatewayRouting",
    "chat_template_kwargs": "chatTemplateKwargs",
    "zai_tool_stream": "zaiToolStream",
    "supports_strict_mode": "supportsStrictMode",
    "cache_control_format": "cacheControlFormat",
    "send_session_affinity_headers": "sendSessionAffinityHeaders",
    "session_affinity_format": "sessionAffinityFormat",
    "supports_long_cache_retention": "supportsLongCacheRetention",
}


def resolve_openai_compat(model: Model) -> OpenAICompat:
    detected = _detect_openai_compat(model)
    explicit = model.compat or {}
    values: dict[str, Any] = {}
    for item in fields(OpenAICompat):
        key = _CATALOG_KEYS[item.name]
        value = explicit.get(key)
        values[item.name] = value if value is not None else getattr(detected, item.name)
    return OpenAICompat(**values)


def _detect_openai_compat(model: Model) -> OpenAICompat:
    provider = model.provider
    base_url = model.base_url
    is_zai = provider in {"zai", "zai-coding-cn"} or "api.z.ai" in base_url or "open.bigmodel.cn" in base_url
    is_together = provider == "together" or "api.together.ai" in base_url or "api.together.xyz" in base_url
    is_moonshot = provider in {"moonshotai", "moonshotai-cn"} or "api.moonshot." in base_url
    is_openrouter = provider == "openrouter" or "openrouter.ai" in base_url
    is_cloudflare_workers = provider == "cloudflare-workers-ai" or "api.cloudflare.com" in base_url
    is_cloudflare_gateway = provider == "cloudflare-ai-gateway" or "gateway.ai.cloudflare.com" in base_url
    is_nvidia = provider == "nvidia" or "integrate.api.nvidia.com" in base_url
    is_ant_ling = provider == "ant-ling" or "api.ant-ling.com" in base_url
    is_deepseek = provider == "deepseek" or "deepseek.com" in base_url
    is_xai = provider == "xai" or "api.x.ai" in base_url
    is_nonstandard = any(
        (
            is_nvidia,
            provider == "cerebras" or "cerebras.ai" in base_url,
            is_xai,
            is_together,
            "chutes.ai" in base_url,
            is_deepseek,
            is_zai,
            is_moonshot,
            provider == "opencode" or "opencode.ai" in base_url,
            is_cloudflare_workers,
            is_cloudflare_gateway,
            is_ant_ling,
        )
    )
    use_max_tokens = any(
        ("chutes.ai" in base_url, is_moonshot, is_cloudflare_gateway, is_together, is_nvidia, is_ant_ling)
    )
    openrouter_developer = is_openrouter and model.id.startswith(("anthropic/", "openai/"))
    if is_deepseek:
        thinking_format = "deepseek"
    elif is_zai:
        thinking_format = "zai"
    elif is_together:
        thinking_format = "together"
    elif is_ant_ling:
        thinking_format = "ant-ling"
    elif is_openrouter:
        thinking_format = "openrouter"
    else:
        thinking_format = "openai"
    return OpenAICompat(
        supports_store=not is_nonstandard,
        supports_developer_role=openrouter_developer or (not is_nonstandard and not is_openrouter),
        supports_reasoning_effort=not any(
            (is_xai, is_zai, is_moonshot, is_together, is_cloudflare_gateway, is_nvidia, is_ant_ling)
        ),
        max_tokens_field="max_tokens" if use_max_tokens else "max_completion_tokens",
        requires_reasoning_content_on_assistant_messages=is_deepseek,
        thinking_format=thinking_format,
        supports_strict_mode=not any((is_moonshot, is_together, is_cloudflare_gateway, is_nvidia)),
        cache_control_format="anthropic" if is_openrouter and model.id.startswith("anthropic/") else None,
        session_affinity_format="openrouter" if is_openrouter else "openai",
        supports_long_cache_retention=not any(
            (is_together, is_cloudflare_workers, is_cloudflare_gateway, is_nvidia, is_ant_ling)
        ),
    )


__all__ = ["OpenAICompat", "resolve_openai_compat"]
