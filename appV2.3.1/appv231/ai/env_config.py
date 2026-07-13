"""Fresh env config for the appv231 ai provider (self-contained)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from appv231.ai.providers.params import GenerationParams, merge_generation_params, params_from_mapping
from appv231.ai.providers.catalog import get_provider

TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_MODEL_PER_PROVIDER = {
    "amazon-bedrock": "us.anthropic.claude-opus-4-6-v1",
    "ant-ling": "Ring-2.6-1T",
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.4",
    "azure-openai-responses": "gpt-5.4",
    "openai-codex": "gpt-5.5",
    "nvidia": "nvidia/nemotron-3-super-120b-a12b",
    "deepseek": "deepseek-v4-pro",
    "google": "gemini-3.1-pro-preview",
    "google-vertex": "gemini-3.1-pro-preview",
    "github-copilot": "gpt-5.4",
    "openrouter": "moonshotai/kimi-k2.6",
    "vercel-ai-gateway": "zai/glm-5.1",
    "xai": "grok-4.20-0309-reasoning",
    "groq": "openai/gpt-oss-120b",
    "cerebras": "zai-glm-4.7",
    "zai": "glm-5.1",
    "zai-coding-cn": "glm-5.1",
    "stepfun": "step-3.7-flash",
    "mistral": "devstral-medium-latest",
    "minimax": "MiniMax-M2.7",
    "minimax-cn": "MiniMax-M2.7",
    "moonshotai": "kimi-k2.6",
    "moonshotai-cn": "kimi-k2.6",
    "huggingface": "moonshotai/Kimi-K2.6",
    "fireworks": "accounts/fireworks/models/kimi-k2p6",
    "together": "moonshotai/Kimi-K2.6",
    "opencode": "kimi-k2.6",
    "opencode-go": "kimi-k2.6",
    "kimi-coding": "kimi-for-coding",
    "cloudflare-workers-ai": "@cf/moonshotai/kimi-k2.6",
    "cloudflare-ai-gateway": "workers-ai/@cf/moonshotai/kimi-k2.6",
    "xiaomi": "mimo-v2.5-pro",
    "xiaomi-token-plan-cn": "mimo-v2.5-pro",
    "xiaomi-token-plan-ams": "mimo-v2.5-pro",
    "xiaomi-token-plan-sgp": "mimo-v2.5-pro",
}
COMMON_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_PROVIDER_SORT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)
PROVIDER_API_KEY_ENV = {
    "ant-ling": ("ANT_LING_API_KEY",),
    "anthropic": ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "azure-openai-responses": ("AZURE_OPENAI_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "cloudflare-ai-gateway": ("CLOUDFLARE_API_KEY",),
    "cloudflare-workers-ai": ("CLOUDFLARE_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "github-copilot": ("COPILOT_GITHUB_TOKEN",),
    "google": ("GEMINI_API_KEY",),
    "google-vertex": ("GOOGLE_CLOUD_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "huggingface": ("HF_TOKEN",),
    "kimi-coding": ("KIMI_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "minimax-cn": ("MINIMAX_CN_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "moonshotai": ("MOONSHOT_API_KEY",),
    "moonshotai-cn": ("MOONSHOT_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "opencode": ("OPENCODE_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "vercel-ai-gateway": ("AI_GATEWAY_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "xiaomi": ("XIAOMI_API_KEY",),
    "xiaomi-token-plan-ams": ("XIAOMI_TOKEN_PLAN_AMS_API_KEY",),
    "xiaomi-token-plan-cn": ("XIAOMI_TOKEN_PLAN_CN_API_KEY",),
    "xiaomi-token-plan-sgp": ("XIAOMI_TOKEN_PLAN_SGP_API_KEY",),
    "zai": ("ZAI_API_KEY",),
    "zai-coding-cn": ("ZAI_CODING_CN_API_KEY",),
    "stepfun": ("STEPFUN_API_KEY",),
}
SUFFIXES = (
    "ENABLED", "API_KEY", "MODEL", "BASE_URL", "TIMEOUT_SECONDS", "TEMPERATURE",
    "TOP_P", "FREQUENCY_PENALTY", "PRESENCE_PENALTY", "SEED", "STOP",
    "PROVIDER_SORT", "MAX_TOKENS",
)


@dataclass(frozen=True)
class ModelConfig:
    enabled: bool
    api_key: str | None
    model: str | None
    base_url: str
    timeout_seconds: float
    temperature: float
    top_p: float | None
    frequency_penalty: float | None
    presence_penalty: float | None
    seed: int | None
    stop: list[str] = field(default_factory=list)
    provider_sort: str | None = "latency"
    max_tokens: int | None = None
    generation_params: GenerationParams = field(default_factory=GenerationParams)


def load_dotenv_values(path: "str | Path" = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_model_config(prefix: str, dotenv_path: "str | Path" = ".env") -> ModelConfig:
    config = load_dotenv_values(dotenv_path)
    for key in (*COMMON_KEYS, *(f"{prefix}_{suffix}" for suffix in SUFFIXES)):
        if key in os.environ:
            config[key] = os.environ[key]
    enabled = config.get(f"{prefix}_ENABLED", "").lower() in TRUE_VALUES
    api_key = config.get(f"{prefix}_API_KEY") or config.get("OPENROUTER_API_KEY") or config.get("OPENAI_API_KEY")
    model = (
        config.get(f"{prefix}_MODEL")
        or config.get("OPENROUTER_MODEL")
        or config.get("OPENAI_MODEL")
        or _default_model(prefix)
    )
    generation_params = _load_generation_params(prefix, config)
    return ModelConfig(
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=config.get(f"{prefix}_BASE_URL") or config.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        timeout_seconds=float(config.get(f"{prefix}_TIMEOUT_SECONDS", "60")),
        temperature=float(config.get(f"{prefix}_TEMPERATURE", "0")),
        top_p=_optional_float(config.get(f"{prefix}_TOP_P")),
        frequency_penalty=_optional_float(config.get(f"{prefix}_FREQUENCY_PENALTY")),
        presence_penalty=_optional_float(config.get(f"{prefix}_PRESENCE_PENALTY")),
        seed=_optional_int(config.get(f"{prefix}_SEED")),
        stop=_optional_list(config.get(f"{prefix}_STOP")),
        provider_sort=config.get(f"{prefix}_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT") or "latency",
        max_tokens=_optional_int(config.get(f"{prefix}_MAX_TOKENS")),
        generation_params=generation_params,
    )


def find_env_keys(provider: str) -> list[str] | None:
    descriptor = get_provider(provider)
    keys = descriptor.api_key_env_vars if descriptor is not None else PROVIDER_API_KEY_ENV.get(provider)
    if not keys:
        return None
    found = [key for key in keys if os.environ.get(key)]
    return found or None


def get_env_api_key(provider: str) -> str | None:
    keys = find_env_keys(provider)
    if keys:
        return os.environ.get(keys[0])
    if provider == "amazon-bedrock" and (
        os.environ.get("AWS_PROFILE")
        or (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
        or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        or os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    ):
        return "<authenticated>"
    return None


def get_default_model_for_provider(provider: str) -> str | None:
    return DEFAULT_MODEL_PER_PROVIDER.get(provider)


def _load_generation_params(prefix: str, config: dict[str, str]) -> GenerationParams:
    parsed_params: list[GenerationParams] = []
    for key, value in {
        "temperature": config.get(f"{prefix}_TEMPERATURE"),
        "top_p": config.get(f"{prefix}_TOP_P"),
        "frequency_penalty": config.get(f"{prefix}_FREQUENCY_PENALTY"),
        "presence_penalty": config.get(f"{prefix}_PRESENCE_PENALTY"),
        "seed": config.get(f"{prefix}_SEED"),
        "stop": config.get(f"{prefix}_STOP"),
        "provider_sort": config.get(f"{prefix}_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT"),
        "max_tokens": config.get(f"{prefix}_MAX_TOKENS"),
        "timeout_seconds": config.get(f"{prefix}_TIMEOUT_SECONDS"),
    }.items():
        try:
            parsed_params.append(params_from_mapping({key: value}, source="env"))
        except ValueError:
            continue
    return merge_generation_params(*parsed_params)


def _default_model(prefix: str) -> str | None:
    return get_default_model_for_provider("openrouter") if prefix == "APPV231_WORKER_LLM" else None


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].strip()
    return value


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("max token settings must be positive or blank.")
    return parsed


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return None
    return float(value)


def _optional_list(value: str | None) -> list[str]:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return []
    stripped = value.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("Stop setting must be a JSON array or comma-separated list.")
        return [str(item) for item in parsed]
    return [item.strip() for item in stripped.split(",") if item.strip()]
