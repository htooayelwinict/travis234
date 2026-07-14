"""Canonical metadata for Travis234's built-in model providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderMetadata:
    id: str
    name: str
    api: str
    base_url: str
    api_key_env_vars: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    auth_type: str = "api_key"
    base_url_env_var: str = ""


BUILTIN_PROVIDER_METADATA: tuple[ProviderMetadata, ...] = (
    ProviderMetadata(
        "amazon-bedrock",
        "Amazon Bedrock",
        "bedrock-converse-stream",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
        aliases=("bedrock", "aws-bedrock"),
        auth_type="ambient",
    ),
    ProviderMetadata("ant-ling", "Ant Ling", "openai-completions", "https://api.ant-ling.com/v1", ("ANT_LING_API_KEY",)),
    ProviderMetadata(
        "anthropic",
        "Anthropic",
        "anthropic-messages",
        "https://api.anthropic.com",
        ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
        aliases=("claude",),
        auth_type="oauth_or_api_key",
    ),
    ProviderMetadata(
        "azure-openai-responses",
        "Azure OpenAI Responses",
        "azure-openai-responses",
        "",
        ("AZURE_OPENAI_API_KEY",),
        aliases=("azure-openai",),
        base_url_env_var="AZURE_OPENAI_BASE_URL",
    ),
    ProviderMetadata("cerebras", "Cerebras", "openai-completions", "https://api.cerebras.ai/v1", ("CEREBRAS_API_KEY",)),
    ProviderMetadata(
        "cloudflare-ai-gateway",
        "Cloudflare AI Gateway",
        "openai-completions",
        "https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/compat",
        ("CLOUDFLARE_API_KEY",),
    ),
    ProviderMetadata(
        "cloudflare-workers-ai",
        "Cloudflare Workers AI",
        "openai-completions",
        "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        ("CLOUDFLARE_API_KEY",),
    ),
    ProviderMetadata("deepseek", "DeepSeek", "openai-completions", "https://api.deepseek.com", ("DEEPSEEK_API_KEY",)),
    ProviderMetadata("fireworks", "Fireworks", "openai-completions", "https://api.fireworks.ai/inference/v1", ("FIREWORKS_API_KEY",)),
    ProviderMetadata(
        "github-copilot",
        "GitHub Copilot",
        "openai-completions",
        "https://api.individual.githubcopilot.com",
        ("COPILOT_GITHUB_TOKEN",),
        aliases=("copilot",),
        auth_type="oauth",
    ),
    ProviderMetadata("google", "Google Gemini", "google-generative-ai", "https://generativelanguage.googleapis.com/v1beta", ("GEMINI_API_KEY",), aliases=("gemini",)),
    ProviderMetadata(
        "google-vertex",
        "Google Vertex AI",
        "google-vertex",
        "https://{location}-aiplatform.googleapis.com",
        ("GOOGLE_CLOUD_API_KEY",),
        aliases=("vertex", "vertex-ai"),
        auth_type="ambient_or_api_key",
    ),
    ProviderMetadata("groq", "Groq", "openai-completions", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",)),
    ProviderMetadata("huggingface", "Hugging Face", "openai-completions", "https://router.huggingface.co/v1", ("HF_TOKEN",), aliases=("hf",)),
    ProviderMetadata("kimi-coding", "Kimi For Coding", "anthropic-messages", "https://api.kimi.com/coding", ("KIMI_API_KEY",), aliases=("kimi",)),
    ProviderMetadata("minimax", "MiniMax", "anthropic-messages", "https://api.minimax.io/anthropic", ("MINIMAX_API_KEY",)),
    ProviderMetadata("minimax-cn", "MiniMax (China)", "anthropic-messages", "https://api.minimaxi.com/anthropic", ("MINIMAX_CN_API_KEY",)),
    ProviderMetadata("mistral", "Mistral", "mistral-conversations", "https://api.mistral.ai", ("MISTRAL_API_KEY",)),
    ProviderMetadata("moonshotai", "Moonshot AI", "openai-completions", "https://api.moonshot.ai/v1", ("MOONSHOT_API_KEY",), aliases=("moonshot",)),
    ProviderMetadata("moonshotai-cn", "Moonshot AI (China)", "openai-completions", "https://api.moonshot.cn/v1", ("MOONSHOT_API_KEY",)),
    ProviderMetadata("nvidia", "NVIDIA NIM", "openai-completions", "https://integrate.api.nvidia.com/v1", ("NVIDIA_API_KEY",)),
    ProviderMetadata("openai", "OpenAI", "openai-responses", "https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    ProviderMetadata(
        "openai-codex",
        "OpenAI Codex",
        "openai-codex-responses",
        "https://chatgpt.com/backend-api",
        aliases=("codex",),
        auth_type="oauth",
    ),
    ProviderMetadata("opencode", "OpenCode Zen", "openai-completions", "https://opencode.ai/zen/v1", ("OPENCODE_API_KEY",), aliases=("opencode-zen",)),
    ProviderMetadata("opencode-go", "OpenCode Go", "openai-completions", "https://opencode.ai/zen/go/v1", ("OPENCODE_API_KEY",)),
    ProviderMetadata("openrouter", "OpenRouter", "openai-completions", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",), aliases=("or",), base_url_env_var="OPENROUTER_BASE_URL"),
    ProviderMetadata("together", "Together AI", "openai-completions", "https://api.together.ai/v1", ("TOGETHER_API_KEY",)),
    ProviderMetadata("vercel-ai-gateway", "Vercel AI Gateway", "anthropic-messages", "https://ai-gateway.vercel.sh", ("AI_GATEWAY_API_KEY",)),
    ProviderMetadata("xai", "xAI", "openai-completions", "https://api.x.ai/v1", ("XAI_API_KEY",), aliases=("grok",)),
    ProviderMetadata("xiaomi", "Xiaomi MiMo", "openai-completions", "https://api.xiaomimimo.com/v1", ("XIAOMI_API_KEY",)),
    ProviderMetadata("xiaomi-token-plan-ams", "Xiaomi MiMo Token Plan (Amsterdam)", "openai-completions", "https://token-plan-ams.xiaomimimo.com/v1", ("XIAOMI_TOKEN_PLAN_AMS_API_KEY",)),
    ProviderMetadata("xiaomi-token-plan-cn", "Xiaomi MiMo Token Plan (China)", "openai-completions", "https://token-plan-cn.xiaomimimo.com/v1", ("XIAOMI_TOKEN_PLAN_CN_API_KEY",)),
    ProviderMetadata("xiaomi-token-plan-sgp", "Xiaomi MiMo Token Plan (Singapore)", "openai-completions", "https://token-plan-sgp.xiaomimimo.com/v1", ("XIAOMI_TOKEN_PLAN_SGP_API_KEY",)),
    ProviderMetadata("zai", "ZAI", "openai-completions", "https://api.z.ai/api/coding/paas/v4", ("ZAI_API_KEY",), aliases=("glm",)),
    ProviderMetadata("zai-coding-cn", "ZAI Coding Plan (China)", "openai-completions", "https://open.bigmodel.cn/api/coding/paas/v4", ("ZAI_CODING_CN_API_KEY",)),
)

EXTRA_PROVIDER_METADATA: tuple[ProviderMetadata, ...] = (
    ProviderMetadata("stepfun", "StepFun", "openai-completions", "https://api.stepfun.ai/step_plan/v1", ("STEPFUN_API_KEY",), aliases=("step",)),
    ProviderMetadata("custom", "Custom", "openai-completions", "", aliases=("ollama", "local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"), auth_type="optional"),
)

PROVIDER_METADATA: tuple[ProviderMetadata, ...] = BUILTIN_PROVIDER_METADATA + EXTRA_PROVIDER_METADATA
PROVIDER_METADATA_BY_ID = {provider.id: provider for provider in PROVIDER_METADATA}
PROVIDER_ALIASES = {
    alias: provider.id
    for provider in PROVIDER_METADATA
    for alias in (provider.id, *provider.aliases)
}


def normalize_provider_id(value: str | None) -> str:
    provider = str(value or "").strip().lower()
    return PROVIDER_ALIASES.get(provider, provider)


def string_mapping(value: object) -> dict[str, str | None] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): None if item is None else str(item) for key, item in value.items()}


__all__ = [
    "BUILTIN_PROVIDER_METADATA",
    "EXTRA_PROVIDER_METADATA",
    "PROVIDER_ALIASES",
    "PROVIDER_METADATA",
    "PROVIDER_METADATA_BY_ID",
    "ProviderMetadata",
    "normalize_provider_id",
    "string_mapping",
]
