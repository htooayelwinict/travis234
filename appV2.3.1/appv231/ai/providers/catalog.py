"""Hermes-style provider catalog for appv231.

Provider profiles are the single source of truth for auth metadata, endpoint
metadata, request quirks, aliases, and model-picker catalog entries. This ports
Hermes' model-provider catalog shape into appv231 without importing the Hermes
reference repo at runtime.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from appv231.ai.providers.base import OMIT_TEMPERATURE, ProviderProfile


@dataclass(frozen=True)
class ProviderDef:
    id: str
    name: str
    transport: str
    api_key_env_vars: tuple[str, ...]
    base_url: str = ""
    base_url_env_var: str = ""
    is_aggregator: bool = False
    auth_type: str = "api_key"
    doc: str = ""
    source: str = "appv231"


@dataclass(frozen=True)
class ProviderDescriptor:
    """One provider descriptor shared by model picker, auth UI, and runtime."""

    slug: str
    label: str
    description: str
    auth_type: str
    tab: str
    api_key_env_vars: tuple[str, ...]
    base_url_env_var: str
    signup_url: str
    order: int


@dataclass(frozen=True)
class ProviderEntry:
    """Hermes canonical provider picker entry."""

    slug: str
    label: str
    tui_desc: str


@dataclass(frozen=True)
class HermesOverlay:
    """Hermes-specific provider metadata layered on top of appv231 profiles."""

    transport: str = "openai_chat"
    is_aggregator: bool = False
    auth_type: str = "api_key"
    extra_env_vars: tuple[str, ...] = ()
    base_url_override: str = ""
    base_url_env_var: str = ""


@dataclass(frozen=True)
class ResolvedProviderRuntime:
    """Hermes-style provider runtime descriptor used by the HTTP provider."""

    provider: str
    requested_provider: str
    profile: ProviderProfile
    api_mode: str
    transport: str
    endpoint_path: str
    base_url: str
    api_key_env_vars: tuple[str, ...]
    auth_type: str
    source: str


_ACCOUNTS_AUTH_TYPES: frozenset[str] = frozenset(
    {
        "oauth_device_code",
        "oauth_external",
        "oauth_minimax",
        "external_process",
        "copilot",
    }
)

_API_MODE_TO_TRANSPORT: dict[str, str] = {
    "chat_completions": "openai_chat",
    "anthropic_messages": "anthropic_messages",
    "codex_responses": "codex_responses",
    "bedrock_converse": "bedrock_converse",
}

_TRANSPORT_TO_API_MODE: dict[str, str] = {value: key for key, value in _API_MODE_TO_TRANSPORT.items()}
TRANSPORT_TO_API_MODE = dict(_TRANSPORT_TO_API_MODE)

_BASE_URL_ENV_VARS: dict[str, str] = {
    "lmstudio": "LM_BASE_URL",
    "openrouter": "OPENROUTER_BASE_URL",
    "openai-api": "OPENAI_BASE_URL",
    "qwen-oauth": "HERMES_QWEN_BASE_URL",
    "xai-oauth": "XAI_BASE_URL",
    "tencent-tokenhub": "TOKENHUB_BASE_URL",
    "zai": "GLM_BASE_URL",
    "kimi-coding": "KIMI_BASE_URL",
    "kimi-coding-cn": "KIMI_CN_BASE_URL",
    "stepfun": "STEPFUN_BASE_URL",
    "minimax": "MINIMAX_BASE_URL",
    "minimax-cn": "MINIMAX_CN_BASE_URL",
    "opencode-zen": "OPENCODE_ZEN_BASE_URL",
    "opencode-go": "OPENCODE_GO_BASE_URL",
}

HERMES_OVERLAYS: dict[str, HermesOverlay] = {
    "moa": HermesOverlay(auth_type="virtual", base_url_override="moa://local"),
    "openrouter": HermesOverlay(is_aggregator=True, base_url_env_var="OPENROUTER_BASE_URL"),
    "nous": HermesOverlay(
        auth_type="oauth_device_code",
        base_url_override="https://inference-api.nousresearch.com/v1",
    ),
    "openai-codex": HermesOverlay(
        transport="codex_responses",
        auth_type="oauth_external",
        base_url_override="https://chatgpt.com/backend-api/codex",
    ),
    "openai-api": HermesOverlay(
        transport="codex_responses",
        base_url_override="https://api.openai.com/v1",
        base_url_env_var="OPENAI_BASE_URL",
    ),
    "xai-oauth": HermesOverlay(
        transport="codex_responses",
        auth_type="oauth_external",
        base_url_override="https://api.x.ai/v1",
        base_url_env_var="XAI_BASE_URL",
    ),
    "qwen-oauth": HermesOverlay(
        auth_type="oauth_external",
        base_url_override="https://portal.qwen.ai/v1",
        base_url_env_var="HERMES_QWEN_BASE_URL",
    ),
    "lmstudio": HermesOverlay(
        extra_env_vars=("LM_API_KEY",),
        base_url_override="http://127.0.0.1:1234/v1",
        base_url_env_var="LM_BASE_URL",
    ),
    "copilot-acp": HermesOverlay(
        transport="codex_responses",
        auth_type="external_process",
        base_url_override="acp://copilot",
        base_url_env_var="COPILOT_ACP_BASE_URL",
    ),
    "copilot": HermesOverlay(extra_env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN")),
    "anthropic": HermesOverlay(
        transport="anthropic_messages",
        extra_env_vars=("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ),
    "zai": HermesOverlay(
        extra_env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        base_url_env_var="GLM_BASE_URL",
    ),
    "kimi-coding": HermesOverlay(base_url_env_var="KIMI_BASE_URL"),
    "kimi-coding-cn": HermesOverlay(base_url_env_var="KIMI_CN_BASE_URL"),
    "stepfun": HermesOverlay(
        extra_env_vars=("STEPFUN_API_KEY",),
        base_url_override="https://api.stepfun.ai/step_plan/v1",
        base_url_env_var="STEPFUN_BASE_URL",
    ),
    "minimax": HermesOverlay(transport="anthropic_messages", base_url_env_var="MINIMAX_BASE_URL"),
    "minimax-oauth": HermesOverlay(
        transport="anthropic_messages",
        auth_type="oauth_external",
        base_url_override="https://api.minimax.io/anthropic",
    ),
    "minimax-cn": HermesOverlay(transport="anthropic_messages", base_url_env_var="MINIMAX_CN_BASE_URL"),
    "deepseek": HermesOverlay(base_url_env_var="DEEPSEEK_BASE_URL"),
    "alibaba": HermesOverlay(base_url_env_var="DASHSCOPE_BASE_URL"),
    "alibaba-coding-plan": HermesOverlay(base_url_env_var="ALIBABA_CODING_PLAN_BASE_URL"),
    "opencode-zen": HermesOverlay(is_aggregator=True, base_url_env_var="OPENCODE_ZEN_BASE_URL"),
    "opencode-go": HermesOverlay(is_aggregator=True, base_url_env_var="OPENCODE_GO_BASE_URL"),
    "kilocode": HermesOverlay(is_aggregator=True, base_url_env_var="KILOCODE_BASE_URL"),
    "huggingface": HermesOverlay(is_aggregator=True, base_url_env_var="HF_BASE_URL"),
    "novita": HermesOverlay(is_aggregator=True, base_url_env_var="NOVITA_BASE_URL"),
    "xai": HermesOverlay(
        transport="codex_responses",
        base_url_override="https://api.x.ai/v1",
        base_url_env_var="XAI_BASE_URL",
    ),
    "nvidia": HermesOverlay(
        base_url_override="https://integrate.api.nvidia.com/v1",
        base_url_env_var="NVIDIA_BASE_URL",
    ),
    "xiaomi": HermesOverlay(base_url_env_var="XIAOMI_BASE_URL"),
    "tencent-tokenhub": HermesOverlay(base_url_env_var="TOKENHUB_BASE_URL"),
    "arcee": HermesOverlay(
        base_url_override="https://api.arcee.ai/api/v1",
        base_url_env_var="ARCEE_BASE_URL",
    ),
    "gmi": HermesOverlay(
        extra_env_vars=("GMI_API_KEY",),
        base_url_override="https://api.gmi-serving.com/v1",
        base_url_env_var="GMI_BASE_URL",
    ),
    "ollama-cloud": HermesOverlay(
        base_url_override="https://ollama.com/v1",
        base_url_env_var="OLLAMA_BASE_URL",
    ),
    "azure-foundry": HermesOverlay(base_url_env_var="AZURE_FOUNDRY_BASE_URL"),
    "bedrock": HermesOverlay(transport="bedrock_converse", auth_type="aws_sdk"),
}

CANONICAL_PROVIDERS: tuple[ProviderEntry, ...] = (
    ProviderEntry("nous", "Nous Portal", "Nous Portal (Everything your agent needs, 300+ models with bundled tool use)"),
    ProviderEntry("openrouter", "OpenRouter", "OpenRouter (Pay-per-use API aggregator)"),
    ProviderEntry("moa", "Mixture of Agents", "Mixture of Agents (named presets; aggregator acts after reference models)"),
    ProviderEntry("novita", "NovitaAI", "NovitaAI (Cloud: Model API, Agent Sandbox, GPU Cloud)"),
    ProviderEntry("lmstudio", "LM Studio", "LM Studio (Local desktop app with built-in model server)"),
    ProviderEntry("anthropic", "Anthropic", "Anthropic (Claude models via API key or Claude Code)"),
    ProviderEntry("openai-codex", "OpenAI Codex", "OpenAI Codex (Codex CLI via ChatGPT subscription or API key)"),
    ProviderEntry("openai-api", "OpenAI API", "OpenAI API (api.openai.com, API key)"),
    ProviderEntry("alibaba", "Qwen Cloud", "Qwen Cloud / DashScope (Qwen + multi-provider)"),
    ProviderEntry("xai-oauth", "xAI Grok OAuth (SuperGrok / Premium+)", "xAI Grok OAuth (SuperGrok / Premium+ subscription)"),
    ProviderEntry("xiaomi", "Xiaomi MiMo", "Xiaomi MiMo (MiMo-V2.5 and V2 models: pro, omni, flash)"),
    ProviderEntry("tencent-tokenhub", "Tencent TokenHub", "Tencent TokenHub (Hy3 Preview via tokenhub.tencentmaas.com)"),
    ProviderEntry("nvidia", "NVIDIA NIM", "NVIDIA NIM (Nemotron models via build.nvidia.com or local NIM)"),
    ProviderEntry("copilot", "GitHub Copilot", "GitHub Copilot (Uses GITHUB_TOKEN or gh auth token)"),
    ProviderEntry("copilot-acp", "GitHub Copilot ACP", "GitHub Copilot ACP (Spawns copilot --acp --stdio)"),
    ProviderEntry("huggingface", "Hugging Face", "Hugging Face Inference Providers"),
    ProviderEntry("gemini", "Google AI Studio", "Google AI Studio (Native Gemini API)"),
    ProviderEntry("deepseek", "DeepSeek", "DeepSeek (V3, R1, coder, direct API)"),
    ProviderEntry("xai", "xAI", "xAI Grok (Direct API)"),
    ProviderEntry("zai", "Z.AI / GLM", "Z.AI / GLM (Zhipu direct API)"),
    ProviderEntry("kimi-coding", "Kimi / Kimi Coding Plan", "Kimi Coding Plan (api.kimi.com & Moonshot API)"),
    ProviderEntry("kimi-coding-cn", "Kimi / Moonshot (China)", "Kimi / Moonshot China (Domestic direct API)"),
    ProviderEntry("stepfun", "StepFun Step Plan", "StepFun Step Plan (Agent / coding models via Step Plan API)"),
    ProviderEntry("minimax", "MiniMax", "MiniMax (Global direct API)"),
    ProviderEntry("minimax-oauth", "MiniMax (OAuth)", "MiniMax via OAuth browser login (Coding Plan, minimax.io)"),
    ProviderEntry("minimax-cn", "MiniMax (China)", "MiniMax China (Domestic direct API)"),
    ProviderEntry("ollama-cloud", "Ollama Cloud", "Ollama Cloud (Cloud-hosted open models, ollama.com)"),
    ProviderEntry("arcee", "Arcee AI", "Arcee AI (Trinity models, direct API)"),
    ProviderEntry("gmi", "GMI Cloud", "GMI Cloud (Multi-model direct API)"),
    ProviderEntry("kilocode", "Kilo Code", "Kilo Code (Kilo Gateway API)"),
    ProviderEntry("opencode-zen", "OpenCode Zen", "OpenCode Zen (Curated models, pay-as-you-go)"),
    ProviderEntry("opencode-go", "OpenCode Go", "OpenCode Go (Open models subscription)"),
    ProviderEntry("bedrock", "AWS Bedrock", "AWS Bedrock (Claude, Nova, Llama, DeepSeek; IAM or API key)"),
    ProviderEntry("azure-foundry", "Azure Foundry", "Azure Foundry (OpenAI-style or Anthropic-style endpoint, your Azure AI deployment)"),
    ProviderEntry("qwen-oauth", "Qwen OAuth (Portal)", "Qwen OAuth (Reuses local Qwen CLI login)"),
    ProviderEntry("alibaba-coding-plan", "Alibaba Cloud (Coding Plan)", "Alibaba Cloud Coding Plan (Dedicated coding tier)"),
    ProviderEntry("custom", "Custom", "Custom OpenAI-compatible endpoint"),
)
_CANONICAL_PROVIDER_BY_SLUG: dict[str, ProviderEntry] = {entry.slug: entry for entry in CANONICAL_PROVIDERS}
_PROVIDER_ORDER: tuple[str, ...] = tuple(entry.slug for entry in CANONICAL_PROVIDERS)

_ROUTING_AGGREGATORS: frozenset[str] = frozenset({"openrouter"})
_FLAT_NAMESPACE_RESELLERS: frozenset[str] = frozenset({"opencode-zen", "opencode-go"})

_GLOBAL_ALIASES: dict[str, str] = {
    "or": "openrouter",
    "openai": "openrouter",
    "openai-api": "openai-api",
    "openai_api": "openai-api",
    "codex": "openai-codex",
    "claude": "anthropic",
    "claude-code": "anthropic",
    "claude-oauth": "anthropic",
    "qwen": "qwen-oauth",
    "qwen-portal": "qwen-oauth",
    "qwen-cli": "qwen-oauth",
    "dashscope": "alibaba",
    "alibaba-cloud": "alibaba",
    "qwen-dashscope": "alibaba",
    "aliyun": "alibaba",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "kimi-for-coding": "kimi-coding",
    "kimi-cn": "kimi-coding-cn",
    "moonshot-cn": "kimi-coding-cn",
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "deep-seek": "deepseek",
    "deepseek-chat": "deepseek",
    "opencode": "opencode-zen",
    "opencode-zen": "opencode-zen",
    "opencode_zen": "opencode-zen",
    "zen": "opencode-zen",
    "opencode_go": "opencode-go",
    "go": "opencode-go",
    "opencode-go-sub": "opencode-go",
    "lmstudio": "lmstudio",
    "lm-studio": "lmstudio",
    "lm_studio": "lmstudio",
    "tencent": "tencent-tokenhub",
    "tokenhub": "tencent-tokenhub",
    "tencent-cloud": "tencent-tokenhub",
    "tencentmaas": "tencent-tokenhub",
    "grok-oauth": "xai-oauth",
    "xai-oauth": "xai-oauth",
    "x-ai-oauth": "xai-oauth",
    "xai-grok-oauth": "xai-oauth",
    "ollama": "custom",
    "local": "custom",
    "vllm": "custom",
    "llamacpp": "custom",
    "llama.cpp": "custom",
    "llama-cpp": "custom",
    "grok": "xai",
    "x-ai": "xai",
    "x.ai": "xai",
    "hf": "huggingface",
    "hugging-face": "huggingface",
    "huggingface-hub": "huggingface",
    "gmi-cloud": "gmi",
    "gmicloud": "gmi",
    "mimo": "xiaomi",
    "xiaomi-mimo": "xiaomi",
}
ALIASES = _GLOBAL_ALIASES

_REGISTRY: dict[str, ProviderProfile] = {}
_PROFILE_ALIASES: dict[str, str] = {}

_ANTHROPIC_REASONING_OPTIONAL_SUBSTRINGS = (
    "claude-3",
    "claude-opus-4-0",
    "claude-opus-4.0",
    "claude-opus-4-1",
    "claude-opus-4.1",
    "claude-sonnet-4-0",
    "claude-sonnet-4.0",
    "claude-opus-4-2025",
    "claude-sonnet-4-2025",
    "claude-opus-4-5",
    "claude-opus-4.5",
    "claude-sonnet-4-5",
    "claude-sonnet-4.5",
    "claude-haiku-4-5",
    "claude-haiku-4.5",
)


def _anthropic_reasoning_is_mandatory(model: str | None) -> bool:
    model_name = (model or "").lower()
    if not model_name.startswith(("anthropic/", "claude")) and "claude" not in model_name:
        return False
    return not any(part in model_name for part in _ANTHROPIC_REASONING_OPTIONAL_SUBSTRINGS)


def _flat_model_name(model: str | None) -> str:
    return (model or "").strip().rsplit("/", 1)[-1].lower()


def _is_deepseek_thinking_model(model: str | None) -> bool:
    normalized = _flat_model_name(model)
    if not normalized:
        return False
    if normalized.startswith("deepseek-v") and not normalized.startswith("deepseek-v3"):
        return True
    return normalized == "deepseek-reasoner"


def _build_gemini_thinking_config(model: str, reasoning_config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(reasoning_config, dict):
        return None
    normalized_model = (model or "").strip().lower()
    if normalized_model.startswith("google/"):
        normalized_model = normalized_model.split("/", 1)[1]
    if not normalized_model.startswith("gemini"):
        return None
    if reasoning_config.get("enabled") is False:
        return {"includeThoughts": False}
    effort = str(reasoning_config.get("effort", "medium") or "medium").strip().lower()
    if effort == "none":
        return {"includeThoughts": False}
    thinking_config: dict[str, Any] = {"includeThoughts": True}
    if normalized_model.startswith("gemini-2.5-"):
        return thinking_config
    if effort not in {"minimal", "low", "medium", "high", "xhigh"}:
        effort = "medium"
    if normalized_model.startswith(("gemini-3", "gemini-3.1")):
        if "flash" in normalized_model:
            if effort in {"minimal", "low"}:
                thinking_config["thinkingLevel"] = "low"
            elif effort in {"high", "xhigh"}:
                thinking_config["thinkingLevel"] = "high"
            else:
                thinking_config["thinkingLevel"] = "medium"
        elif "pro" in normalized_model:
            thinking_config["thinkingLevel"] = "high" if effort in {"high", "xhigh"} else "low"
    return thinking_config


def _snake_case_gemini_thinking_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(config, dict) or not config:
        return None
    translated: dict[str, Any] = {}
    if isinstance(config.get("includeThoughts"), bool):
        translated["include_thoughts"] = config["includeThoughts"]
    if isinstance(config.get("thinkingLevel"), str) and config["thinkingLevel"].strip():
        translated["thinking_level"] = config["thinkingLevel"].strip().lower()
    if isinstance(config.get("thinkingBudget"), (int, float)):
        translated["thinking_budget"] = int(config["thinkingBudget"])
    return translated or None


def _is_gemini_openai_compat_base_url(base_url: Any) -> bool:
    normalized = str(base_url or "").strip().rstrip("/").lower()
    return bool(normalized and "generativelanguage.googleapis.com" in normalized and normalized.endswith("/openai"))


def _is_minimax_global_openai_base_url(base_url: str | None) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    if (parsed.hostname or "").lower() != "api.minimax.io":
        return False
    return parsed.path.rstrip("/").lower() == "/v1"


def _is_minimax_m3(model: str | None) -> bool:
    return str(model or "").strip().lower() in {"minimax-m3", "minimax/minimax-m3"}


def _appv231_nous_portal_tags() -> list[str]:
    return ["product=hermes-agent", "client=hermes-client-vappv231"]


class OpenRouterProfile(ProviderProfile):
    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        return super().fetch_models(api_key=None, base_url=base_url, timeout=timeout)

    def build_extra_body(self, *, session_id: str | None = None, **context: Any) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if session_id:
            body["session_id"] = session_id
        provider_preferences = context.get("provider_preferences")
        if provider_preferences:
            body["provider"] = provider_preferences
        model = context.get("model") or ""
        if model == "openrouter/pareto-code":
            score = context.get("openrouter_min_coding_score")
            if score is not None and score != "":
                try:
                    score_float = float(score)
                except (TypeError, ValueError):
                    score_float = None
                if score_float is not None and 0.0 <= score_float <= 1.0:
                    body["plugins"] = [{"id": "pareto-router", "min_coding_score": score_float}]
        return body

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        supports_reasoning: bool = False,
        model: str | None = None,
        session_id: str | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        if supports_reasoning:
            if _anthropic_reasoning_is_mandatory(model):
                config = reasoning_config or {}
                effort = config.get("effort")
                if config.get("enabled", True) is not False and effort and effort != "none":
                    top_level["verbosity"] = effort
            elif reasoning_config is not None:
                extra_body["reasoning"] = dict(reasoning_config)
            else:
                extra_body["reasoning"] = {"enabled": True, "effort": "medium"}
        if session_id and model and model.startswith(("x-ai/grok-", "xai/grok-")):
            top_level["extra_headers"] = {"x-grok-conv-id": session_id}
        return extra_body, top_level


class QwenProfile(ProviderProfile):
    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = copy.deepcopy(messages)
        for message in prepared:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                parts: list[dict[str, Any]] = []
                for part in content:
                    if isinstance(part, str):
                        parts.append({"type": "text", "text": part})
                    elif isinstance(part, dict):
                        parts.append(part)
                if parts:
                    message["content"] = parts
        for message in prepared:
            if message.get("role") != "system":
                continue
            content = message.get("content")
            if isinstance(content, list) and content and isinstance(content[-1], dict):
                content[-1]["cache_control"] = {"type": "ephemeral"}
            break
        return prepared

    def build_extra_body(self, *, session_id: str | None = None, **_context: Any) -> dict[str, Any]:
        return {"vl_high_resolution_images": True}

    def build_api_kwargs_extras(
        self,
        *,
        qwen_session_metadata: dict[str, Any] | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if qwen_session_metadata:
            return {}, {"metadata": qwen_session_metadata}
        return {}, {}


class KimiProfile(ProviderProfile):
    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        if not isinstance(reasoning_config, dict):
            extra_body["thinking"] = {"type": "enabled"}
            return extra_body, top_level
        if reasoning_config.get("enabled", True) is False:
            extra_body["thinking"] = {"type": "disabled"}
            return extra_body, top_level
        effort = (reasoning_config.get("effort") or "").strip().lower()
        if effort in {"low", "medium", "high"}:
            top_level["reasoning_effort"] = effort
        else:
            extra_body["thinking"] = {"type": "enabled"}
        return extra_body, top_level


class DeepSeekProfile(ProviderProfile):
    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        model: str | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        if not _is_deepseek_thinking_model(model):
            return extra_body, top_level
        enabled = not (isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is False)
        extra_body["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if not enabled:
            return extra_body, top_level
        if isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort in {"xhigh", "max"}:
                top_level["reasoning_effort"] = "max"
            elif effort in {"low", "medium", "high"}:
                top_level["reasoning_effort"] = effort
        return extra_body, top_level


class CustomProfile(ProviderProfile):
    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        ollama_num_ctx: int | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        if ollama_num_ctx:
            extra_body["options"] = {"num_ctx": ollama_num_ctx}
        if isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort == "none" or reasoning_config.get("enabled", True) is False:
                extra_body["think"] = False
        return extra_body, {}

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        if not (base_url or self.base_url):
            return None
        return super().fetch_models(api_key=api_key, base_url=base_url, timeout=timeout)


class GeminiProfile(ProviderProfile):
    def build_extra_body(self, *, session_id: str | None = None, **context: Any) -> dict[str, Any]:
        raw_config = _build_gemini_thinking_config(context.get("model") or "", context.get("reasoning_config"))
        if not raw_config:
            return {}
        base_url = context.get("base_url") or self.base_url
        if self.name == "gemini" and _is_gemini_openai_compat_base_url(base_url):
            thinking_config = _snake_case_gemini_thinking_config(raw_config)
            return {"extra_body": {"google": {"thinking_config": thinking_config}}} if thinking_config else {}
        return {"thinking_config": raw_config}


class NousProfile(ProviderProfile):
    def build_extra_body(self, *, session_id: str | None = None, **_context: Any) -> dict[str, Any]:
        return {"tags": _appv231_nous_portal_tags()}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        supports_reasoning: bool = False,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        if supports_reasoning:
            if reasoning_config is not None:
                config = dict(reasoning_config)
                if config.get("enabled") is not False:
                    extra_body["reasoning"] = config
            else:
                extra_body["reasoning"] = {"enabled": True, "effort": "medium"}
        return extra_body, {}


class MiniMaxProfile(ProviderProfile):
    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        model: str | None = None,
        base_url: str | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not _is_minimax_global_openai_base_url(base_url) or not _is_minimax_m3(model):
            return {}, {}
        extra_body: dict[str, Any] = {"reasoning_split": True}
        if isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is False:
            extra_body["thinking"] = {"type": "disabled"}
        elif reasoning_config is not None:
            extra_body["thinking"] = {"type": "adaptive"}
        return extra_body, {}


class OpenCodeGoProfile(ProviderProfile):
    _MODEL_MAX_TOKENS: dict[str, int] = {"mimo-v2.5-pro": 131072}

    def get_max_tokens(self, model: str | None) -> int | None:
        return self._MODEL_MAX_TOKENS.get(_flat_model_name(model), self.default_max_tokens)

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        model: str | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        flat = _flat_model_name(model)
        if flat.startswith("kimi-k2"):
            if not isinstance(reasoning_config, dict):
                return extra_body, top_level
            if reasoning_config.get("enabled") is False:
                extra_body["thinking"] = {"type": "disabled"}
                return extra_body, top_level
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort in {"xhigh", "max"}:
                top_level["reasoning_effort"] = "high"
            elif effort in {"low", "medium", "high"}:
                top_level["reasoning_effort"] = effort
            if "reasoning_effort" not in top_level:
                extra_body["thinking"] = {"type": "enabled"}
            return extra_body, top_level
        if not _is_deepseek_thinking_model(model):
            return extra_body, top_level
        enabled = not (isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is False)
        if not enabled:
            extra_body["thinking"] = {"type": "disabled"}
            return extra_body, top_level
        if isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort in {"xhigh", "max"}:
                top_level["reasoning_effort"] = "max"
            elif effort in {"low", "medium", "high"}:
                top_level["reasoning_effort"] = effort
        if "reasoning_effort" not in top_level:
            extra_body["thinking"] = {"type": "enabled"}
        return extra_body, top_level


class OllamaCloudProfile(ProviderProfile):
    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(reasoning_config, dict):
            return {}, {}
        if reasoning_config.get("enabled", True) is False:
            return {}, {}
        effort = (reasoning_config.get("effort") or "").strip().lower()
        if not effort or effort == "none":
            return {}, {}
        if effort in {"xhigh", "max"}:
            return {}, {"reasoning_effort": "max"}
        if effort in {"low", "medium", "high"}:
            return {}, {"reasoning_effort": effort}
        return {}, {"reasoning_effort": effort}


def normalize_provider(name: str | None) -> str:
    raw = (name or "").strip().lower()
    if not raw:
        return raw
    return _PROFILE_ALIASES.get(raw) or _GLOBAL_ALIASES.get(raw, raw)


def register_provider(profile: ProviderProfile) -> None:
    canonical = profile.name.strip().lower()
    _REGISTRY[canonical] = profile
    _PROFILE_ALIASES[canonical] = canonical
    for alias in profile.aliases:
        _PROFILE_ALIASES[str(alias).strip().lower()] = canonical


def get_provider_profile(name: str | None) -> ProviderProfile | None:
    return _REGISTRY.get(normalize_provider(name))


def list_provider_profiles() -> list[ProviderProfile]:
    ordered = [profile for name in _PROVIDER_ORDER if (profile := _REGISTRY.get(name)) is not None]
    extras = [profile for name, profile in _REGISTRY.items() if name not in _PROVIDER_ORDER]
    return ordered + extras


def tab_for_auth_type(auth_type: str) -> str:
    return "accounts" if auth_type in _ACCOUNTS_AUTH_TYPES else "keys"


def _canonical_entry(slug: str) -> ProviderEntry | None:
    return _CANONICAL_PROVIDER_BY_SLUG.get(slug)


def _provider_label(profile: ProviderProfile) -> str:
    entry = _canonical_entry(profile.name)
    return profile.display_name or (entry.label if entry is not None else "") or profile.name


def _provider_description(profile: ProviderProfile) -> str:
    entry = _canonical_entry(profile.name)
    label = profile.display_name or (entry.label if entry is not None else "") or profile.name
    return profile.description or (entry.tui_desc if entry is not None else "") or label


def _split_env_vars(env_vars: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    keys = tuple(value for value in env_vars if not (value.endswith("_BASE_URL") or value.endswith("_URL")))
    base = next((value for value in env_vars if value.endswith("_BASE_URL") or value.endswith("_URL")), "")
    return keys, base


def _provider_env_vars(profile: ProviderProfile, overlay: HermesOverlay | None) -> tuple[tuple[str, ...], str]:
    api_key_env_vars, base_url_env_var = _split_env_vars(tuple(profile.env_vars))
    merged = list(api_key_env_vars)
    if overlay is not None:
        for env_var in overlay.extra_env_vars:
            if env_var and env_var not in merged:
                merged.append(env_var)
    base_url_env_var = (
        (overlay.base_url_env_var if overlay is not None else "")
        or _BASE_URL_ENV_VARS.get(profile.name, "")
        or base_url_env_var
    )
    return tuple(merged), base_url_env_var


def _provider_auth_type(profile: ProviderProfile, overlay: HermesOverlay | None) -> str:
    if profile.auth_type and profile.auth_type != "api_key":
        return profile.auth_type
    if overlay is not None and overlay.auth_type:
        return overlay.auth_type
    return profile.auth_type or "api_key"


def provider_catalog() -> list[ProviderDescriptor]:
    descriptors: list[ProviderDescriptor] = []
    for order, profile in enumerate(list_provider_profiles()):
        overlay = HERMES_OVERLAYS.get(profile.name)
        env_vars, base_url_env_var = _provider_env_vars(profile, overlay)
        auth_type = _provider_auth_type(profile, overlay)
        label = _provider_label(profile)
        descriptors.append(
            ProviderDescriptor(
                slug=profile.name,
                label=label,
                description=_provider_description(profile),
                auth_type=auth_type,
                tab=tab_for_auth_type(auth_type),
                api_key_env_vars=env_vars,
                base_url_env_var=base_url_env_var,
                signup_url=profile.signup_url,
                order=order,
            )
        )
    return descriptors


def provider_catalog_by_slug() -> dict[str, ProviderDescriptor]:
    return {descriptor.slug: descriptor for descriptor in provider_catalog()}


def get_provider(name: str | None) -> ProviderDef | None:
    canonical = normalize_provider(name)
    profile = _REGISTRY.get(canonical)
    if profile is None:
        return None
    overlay = HERMES_OVERLAYS.get(profile.name)
    env_vars, base_url_env_var = _provider_env_vars(profile, overlay)
    transport = overlay.transport if overlay is not None else _API_MODE_TO_TRANSPORT.get(profile.api_mode, "openai_chat")
    auth_type = _provider_auth_type(profile, overlay)
    base_url = (overlay.base_url_override if overlay is not None else "") or profile.base_url
    is_agg = overlay.is_aggregator if overlay is not None else profile.name in {"openrouter", "opencode-zen", "opencode-go"}
    return ProviderDef(
        id=profile.name,
        name=_provider_label(profile),
        transport=transport,
        api_key_env_vars=env_vars,
        base_url=base_url,
        base_url_env_var=base_url_env_var,
        is_aggregator=is_agg,
        auth_type=auth_type,
        doc=_provider_description(profile),
        source="hermes-overlay" if overlay is not None else "appv231",
    )


def is_aggregator(provider: str | None) -> bool:
    provider_norm = normalize_provider(provider)
    if provider_norm.startswith("custom:"):
        return True
    pdef = get_provider(provider_norm)
    return pdef.is_aggregator if pdef is not None else False


def is_routing_aggregator(provider: str | None) -> bool:
    provider_norm = normalize_provider(provider)
    if provider_norm in _FLAT_NAMESPACE_RESELLERS:
        return False
    if provider_norm.startswith("custom:"):
        return True
    return provider_norm in _ROUTING_AGGREGATORS


def determine_api_mode(provider: str | None, base_url: str = "") -> str:
    if base_url:
        url_lower = base_url.rstrip("/").lower()
        parsed = urlparse(url_lower)
        hostname = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")
        if path.endswith("/anthropic") or path.endswith("/anthropic/v1") or hostname == "api.anthropic.com":
            return "anthropic_messages"
        if hostname == "api.kimi.com" and "/coding" in url_lower:
            return "anthropic_messages"
        if hostname in {"api.openai.com", "api.x.ai"}:
            return "codex_responses"
        if hostname.startswith("bedrock-runtime.") and hostname.endswith(".amazonaws.com"):
            return "bedrock_converse"
    pdef = get_provider(provider)
    return _TRANSPORT_TO_API_MODE.get(pdef.transport, "chat_completions") if pdef is not None else "chat_completions"


def resolve_user_provider(name: str, user_providers: dict[str, Any] | None) -> ProviderDef | None:
    if not isinstance(user_providers, dict):
        return None
    entry = user_providers.get(name)
    if not isinstance(entry, dict):
        return None
    display_name = str(entry.get("name") or name)
    base_url = str(entry.get("api") or entry.get("url") or entry.get("base_url") or "")
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "")
    transport = str(entry.get("transport") or "")
    api_mode = str(entry.get("api_mode") or "")
    if not transport:
        transport = _API_MODE_TO_TRANSPORT.get(api_mode, "openai_chat")
    env_vars = (key_env,) if key_env else ()
    return ProviderDef(
        id=name,
        name=display_name,
        transport=transport,
        api_key_env_vars=env_vars,
        base_url=base_url,
        is_aggregator=False,
        auth_type=str(entry.get("auth_type") or "api_key"),
        source="user-config",
    )


def custom_provider_slug(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(display_name or "").strip().lower()).strip("-")
    return f"custom:{slug or 'provider'}"


def resolve_custom_provider(
    name: str,
    custom_providers: list[dict[str, Any]] | None,
) -> ProviderDef | None:
    if not isinstance(custom_providers, list):
        return None
    requested = str(name or "").strip().lower()
    bare_custom_fallback = requested == "custom"
    requested_slug = custom_provider_slug(requested)
    first_valid: ProviderDef | None = None
    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue
        display_name = str(entry.get("name") or entry.get("label") or "").strip()
        if not display_name:
            continue
        base_url = str(entry.get("base_url") or entry.get("api") or entry.get("url") or "")
        if not base_url:
            continue
        slug = custom_provider_slug(display_name)
        aliases = {display_name.strip().lower(), slug}
        api_mode = str(entry.get("api_mode") or entry.get("mode") or "chat_completions")
        transport = str(entry.get("transport") or _API_MODE_TO_TRANSPORT.get(api_mode, "openai_chat"))
        key_env = str(entry.get("api_key_env") or entry.get("key_env") or "")
        candidate = ProviderDef(
            id=slug,
            name=display_name,
            transport=transport,
            api_key_env_vars=(key_env,) if key_env else (),
            base_url=base_url,
            is_aggregator=True,
            auth_type=str(entry.get("auth_type") or "api_key"),
            source="custom-provider",
        )
        if first_valid is None:
            first_valid = candidate
        if requested not in aliases and requested_slug != slug:
            continue
        return candidate
    return first_valid if bare_custom_fallback else None


def resolve_provider_full(
    name: str,
    user_providers: dict[str, Any] | None = None,
    custom_providers: list[dict[str, Any]] | None = None,
) -> ProviderDef | None:
    raw = str(name or "").strip().lower()
    if not raw:
        return None

    if raw == "custom":
        custom_pdef = resolve_custom_provider(name, custom_providers)
        if custom_pdef is not None:
            return custom_pdef

    user_pdef = resolve_user_provider(raw, user_providers)
    if user_pdef is not None:
        return user_pdef

    canonical = normalize_provider(raw)
    pdef = get_provider(canonical)
    if pdef is not None:
        return pdef

    user_pdef = resolve_user_provider(canonical, user_providers)
    if user_pdef is not None:
        return user_pdef
    user_pdef = resolve_user_provider(raw, user_providers)
    if user_pdef is not None:
        return user_pdef

    return resolve_custom_provider(name, custom_providers)


def _profile_from_provider_def(provider: ProviderDef) -> ProviderProfile:
    api_mode = _TRANSPORT_TO_API_MODE.get(provider.transport, "chat_completions")
    return ProviderProfile(
        name=provider.id,
        api_mode=api_mode,
        display_name=provider.name,
        description=provider.doc,
        env_vars=provider.api_key_env_vars,
        base_url=provider.base_url,
        auth_type=provider.auth_type,
    )


def resolve_provider_runtime(
    provider: str | None,
    *,
    explicit_base_url: str | None = None,
    fallback_base_url: str | None = None,
    user_providers: dict[str, Any] | None = None,
    custom_providers: list[dict[str, Any]] | None = None,
) -> ResolvedProviderRuntime:
    requested = str(provider or "").strip() or "openrouter"
    pdef = resolve_provider_full(requested, user_providers=user_providers, custom_providers=custom_providers)
    if pdef is None:
        profile = get_provider_profile("custom") or ProviderProfile(name="custom")
        pdef = ProviderDef(
            id=normalize_provider(requested) or "custom",
            name=requested,
            transport=_API_MODE_TO_TRANSPORT.get(profile.api_mode, "openai_chat"),
            api_key_env_vars=tuple(profile.env_vars),
            base_url=profile.base_url,
            auth_type=profile.auth_type,
            source="fallback-custom",
        )
    profile = get_provider_profile(pdef.id) or _profile_from_provider_def(pdef)
    base_url_env_var = pdef.base_url_env_var or _BASE_URL_ENV_VARS.get(pdef.id, "")
    env_base_url = os.environ.get(base_url_env_var, "").strip() if base_url_env_var else ""
    base_url = (
        str(explicit_base_url or "").strip()
        or env_base_url
        or pdef.base_url
        or profile.base_url
        or str(fallback_base_url or "").strip()
    )
    api_mode = determine_api_mode(pdef.id, base_url)
    transport = _API_MODE_TO_TRANSPORT.get(api_mode, pdef.transport or "openai_chat")
    from appv231.ai.providers.transports import get_transport

    endpoint_path = getattr(get_transport(api_mode), "endpoint_path", "/chat/completions")
    return ResolvedProviderRuntime(
        provider=pdef.id,
        requested_provider=requested,
        profile=profile,
        api_mode=api_mode,
        transport=transport,
        endpoint_path=endpoint_path,
        base_url=base_url,
        api_key_env_vars=pdef.api_key_env_vars,
        auth_type=pdef.auth_type,
        source=pdef.source,
    )


# Hermes model-provider catalog entries.
register_provider(
    ProviderProfile(
        name="nous",
        aliases=("nous-portal", "nousresearch"),
        env_vars=("NOUS_API_KEY",),
        display_name="Nous Research",
        description="Nous Portal (Everything your agent needs, 300+ models with bundled tool use)",
        signup_url="https://nousresearch.com/",
        fallback_models=("hermes-3-405b", "hermes-3-70b"),
        base_url="https://inference.nousresearch.com/v1",
        auth_type="oauth_device_code",
    )
)
register_provider(
    OpenRouterProfile(
        name="openrouter",
        aliases=("or",),
        env_vars=("OPENROUTER_API_KEY",),
        display_name="OpenRouter",
        description="OpenRouter - unified API for 200+ models",
        signup_url="https://openrouter.ai/keys",
        base_url="https://openrouter.ai/api/v1",
        models_url="https://openrouter.ai/api/v1/models",
        fallback_models=("anthropic/claude-sonnet-4.6", "openai/gpt-5.4", "deepseek/deepseek-chat", "google/gemini-3-flash-preview", "qwen/qwen3-plus"),
    )
)
register_provider(
    ProviderProfile(
        name="moa",
        display_name="Mixture of Agents",
        description="Mixture of Agents (named presets; aggregator acts after reference models)",
        base_url="moa://local",
        auth_type="virtual",
    )
)
register_provider(ProviderProfile(name="novita", aliases=("novita-ai", "novitaai"), display_name="NovitaAI", description="NovitaAI - AI-native cloud for builders and agents", signup_url="https://novita.ai/settings/key-management", env_vars=("NOVITA_API_KEY", "NOVITA_BASE_URL"), base_url="https://api.novita.ai/openai/v1", default_aux_model="deepseek/deepseek-v3-0324", fallback_models=("moonshotai/kimi-k2.5", "minimax/minimax-m2.7", "zai-org/glm-5", "deepseek/deepseek-v3-0324", "deepseek/deepseek-r1-0528", "qwen/qwen3-235b-a22b-fp8")))
register_provider(
    ProviderProfile(
        name="lmstudio",
        aliases=("lm-studio", "lm_studio"),
        display_name="LM Studio",
        description="LM Studio (Local desktop app with built-in model server)",
        env_vars=("LM_API_KEY", "LM_BASE_URL"),
        base_url="http://127.0.0.1:1234/v1",
    )
)
register_provider(
    ProviderProfile(
        name="openai-codex",
        aliases=("codex", "openai_codex"),
        api_mode="codex_responses",
        display_name="OpenAI Codex",
        description="OpenAI Codex (Codex CLI via ChatGPT subscription or API key)",
        base_url="https://chatgpt.com/backend-api/codex",
        auth_type="oauth_external",
    )
)
register_provider(
    ProviderProfile(
        name="openai-api",
        aliases=("openai_api",),
        api_mode="codex_responses",
        display_name="OpenAI API",
        description="OpenAI API (api.openai.com, API key)",
        signup_url="https://platform.openai.com/api-keys",
        env_vars=("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        base_url="https://api.openai.com/v1",
        models_url="https://api.openai.com/v1/models",
        fallback_models=("gpt-5.4", "gpt-5.4-mini", "gpt-4.1"),
    )
)
register_provider(
    ProviderProfile(
        name="alibaba-coding-plan",
        aliases=("alibaba_coding", "alibaba-coding", "dashscope-coding"),
        display_name="Alibaba Cloud (Coding Plan)",
        description="Alibaba Cloud Coding Plan (Dedicated coding tier)",
        signup_url="https://help.aliyun.com/zh/model-studio/",
        env_vars=("ALIBABA_CODING_PLAN_API_KEY", "DASHSCOPE_API_KEY", "ALIBABA_CODING_PLAN_BASE_URL"),
        base_url="https://coding-intl.dashscope.aliyuncs.com/v1",
    )
)
register_provider(
    ProviderProfile(
        name="alibaba",
        aliases=("dashscope", "alibaba-cloud", "qwen-dashscope"),
        env_vars=("DASHSCOPE_API_KEY",),
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )
)
register_provider(
    ProviderProfile(
        name="xai-oauth",
        aliases=("grok-oauth", "xai-grok-oauth", "x-ai-oauth"),
        api_mode="codex_responses",
        display_name="xAI Grok OAuth (SuperGrok / Premium+)",
        description="xAI Grok OAuth (SuperGrok / Premium+ subscription)",
        base_url="https://api.x.ai/v1",
        auth_type="oauth_external",
    )
)
register_provider(
    ProviderProfile(
        name="anthropic",
        aliases=("claude", "claude-oauth", "claude-code"),
        api_mode="anthropic_messages",
        env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
        base_url="https://api.anthropic.com",
        default_aux_model="claude-haiku-4-5-20251001",
    )
)
register_provider(ProviderProfile(name="arcee", aliases=("arcee-ai", "arceeai"), env_vars=("ARCEEAI_API_KEY",), base_url="https://api.arcee.ai/api/v1"))
register_provider(
    ProviderProfile(
        name="azure-foundry",
        aliases=("azure", "azure-ai-foundry", "azure-ai"),
        display_name="Azure Foundry",
        description="Microsoft Foundry - OpenAI-compatible endpoint (user-supplied base URL)",
        signup_url="https://ai.azure.com/",
        env_vars=("AZURE_FOUNDRY_API_KEY", "AZURE_FOUNDRY_BASE_URL"),
    )
)
register_provider(
    ProviderProfile(
        name="bedrock",
        aliases=("aws", "aws-bedrock", "amazon-bedrock", "amazon"),
        api_mode="bedrock_converse",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        auth_type="aws_sdk",
    )
)
register_provider(
    ProviderProfile(
        name="copilot-acp",
        aliases=("github-copilot-acp", "copilot-acp-agent"),
        base_url="acp://copilot",
        auth_type="external_process",
    )
)
register_provider(
    ProviderProfile(
        name="copilot",
        aliases=("github-copilot", "github-models", "github-model", "github"),
        display_name="GitHub Copilot",
        description="GitHub Copilot (Uses GITHUB_TOKEN or gh auth token)",
        env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        base_url="https://api.githubcopilot.com",
        auth_type="api_key",
    )
)
register_provider(CustomProfile(name="custom", aliases=("ollama", "local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"), default_max_tokens=65536))
register_provider(
    DeepSeekProfile(
        name="deepseek",
        aliases=("deepseek-chat",),
        env_vars=("DEEPSEEK_API_KEY",),
        display_name="DeepSeek",
        description="DeepSeek - native DeepSeek API",
        signup_url="https://platform.deepseek.com/",
        fallback_models=("deepseek-chat", "deepseek-reasoner"),
        base_url="https://api.deepseek.com/v1",
        default_aux_model="deepseek-chat",
    )
)
register_provider(GeminiProfile(name="gemini", aliases=("google", "google-gemini", "google-ai-studio"), env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"), base_url="https://generativelanguage.googleapis.com/v1beta", default_aux_model="gemini-3.5-flash"))
register_provider(
    ProviderProfile(
        name="gmi",
        aliases=("gmi-cloud", "gmicloud"),
        display_name="GMI Cloud",
        description="GMI Cloud - multi-model direct API (slash-form model IDs)",
        signup_url="https://www.gmicloud.ai/",
        env_vars=("GMI_API_KEY", "GMI_BASE_URL"),
        base_url="https://api.gmi-serving.com/v1",
        default_headers={"User-Agent": "HermesAgent/appv231"},
        default_aux_model="google/gemini-3.1-flash-lite-preview",
        fallback_models=("zai-org/GLM-5.1-FP8", "deepseek-ai/DeepSeek-V3.2", "moonshotai/Kimi-K2.5", "google/gemini-3.1-flash-lite-preview", "anthropic/claude-sonnet-4.6", "openai/gpt-5.4"),
    )
)
register_provider(ProviderProfile(name="huggingface", aliases=("hf", "hugging-face", "huggingface-hub"), env_vars=("HF_TOKEN",), display_name="HuggingFace", description="HuggingFace Inference API", signup_url="https://huggingface.co/settings/tokens", fallback_models=("Qwen/Qwen3.5-72B-Instruct", "deepseek-ai/DeepSeek-V3.2"), base_url="https://router.huggingface.co/v1"))
register_provider(ProviderProfile(name="kilocode", aliases=("kilo-code", "kilo", "kilo-gateway"), env_vars=("KILOCODE_API_KEY",), base_url="https://api.kilo.ai/api/gateway", default_aux_model="google/gemini-3-flash-preview"))
register_provider(KimiProfile(name="kimi-coding", aliases=("kimi", "moonshot", "kimi-for-coding"), env_vars=("KIMI_API_KEY", "KIMI_CODING_API_KEY"), base_url="https://api.moonshot.ai/v1", fixed_temperature=OMIT_TEMPERATURE, default_max_tokens=32000, default_headers={"User-Agent": "hermes-agent/1.0"}, default_aux_model="kimi-k2-turbo-preview"))
register_provider(KimiProfile(name="kimi-coding-cn", aliases=("kimi-cn", "moonshot-cn"), env_vars=("KIMI_CN_API_KEY",), base_url="https://api.moonshot.cn/v1", fixed_temperature=OMIT_TEMPERATURE, default_max_tokens=32000, default_headers={"User-Agent": "hermes-agent/1.0"}, default_aux_model="kimi-k2-turbo-preview"))
register_provider(MiniMaxProfile(name="minimax", aliases=("mini-max",), api_mode="anthropic_messages", env_vars=("MINIMAX_API_KEY",), base_url="https://api.minimax.io/anthropic", default_aux_model="MiniMax-M3"))
register_provider(MiniMaxProfile(name="minimax-cn", aliases=("minimax-china", "minimax_cn"), api_mode="anthropic_messages", env_vars=("MINIMAX_CN_API_KEY",), base_url="https://api.minimaxi.com/anthropic", default_aux_model="MiniMax-M3"))
register_provider(MiniMaxProfile(name="minimax-oauth", aliases=("minimax_oauth", "minimax-oauth-io"), api_mode="anthropic_messages", display_name="MiniMax (OAuth)", description="MiniMax via OAuth browser flow - no API key required", signup_url="https://api.minimax.io/", base_url="https://api.minimax.io/anthropic", auth_type="oauth_minimax", default_aux_model="MiniMax-M2.7"))
register_provider(ProviderProfile(name="nvidia", aliases=("nvidia-nim", "nim"), env_vars=("NVIDIA_API_KEY",), display_name="NVIDIA NIM", description="NVIDIA NIM", signup_url="https://build.nvidia.com/", base_url="https://integrate.api.nvidia.com/v1", fallback_models=("nvidia/llama-3.3-nemotron-super-49b-v1",)))
register_provider(ProviderProfile(name="tencent-tokenhub", aliases=("tencent", "tokenhub", "tencent-cloud", "tencentmaas"), env_vars=("TENCENT_TOKENHUB_API_KEY", "TOKENHUB_API_KEY"), display_name="Tencent TokenHub", description="Tencent TokenHub (Hy3 Preview via tokenhub.tencentmaas.com)", base_url="https://api.lkeap.cloud.tencent.com/v1"))
register_provider(OllamaCloudProfile(name="ollama-cloud", aliases=("ollama_cloud",), default_aux_model="nemotron-3-nano:30b", env_vars=("OLLAMA_API_KEY",), base_url="https://ollama.com/v1"))
register_provider(ProviderProfile(name="opencode-zen", aliases=("opencode", "opencode_zen", "zen"), env_vars=("OPENCODE_ZEN_API_KEY",), base_url="https://opencode.ai/zen/v1", default_aux_model="gemini-3-flash"))
register_provider(OpenCodeGoProfile(name="opencode-go", aliases=("opencode_go", "go", "opencode-go-sub"), env_vars=("OPENCODE_GO_API_KEY",), base_url="https://opencode.ai/zen/go/v1", default_aux_model="glm-5"))
register_provider(QwenProfile(name="qwen-oauth", aliases=("qwen", "qwen-portal", "qwen-cli"), env_vars=("QWEN_API_KEY",), base_url="https://portal.qwen.ai/v1", auth_type="oauth_external", default_max_tokens=65536))
register_provider(ProviderProfile(name="stepfun", aliases=("step", "stepfun-coding-plan"), default_aux_model="step-3.5-flash", env_vars=("STEPFUN_API_KEY",), base_url="https://api.stepfun.ai/step_plan/v1"))
register_provider(ProviderProfile(name="xai", aliases=("grok", "x-ai", "x.ai"), api_mode="codex_responses", env_vars=("XAI_API_KEY",), base_url="https://api.x.ai/v1"))
register_provider(ProviderProfile(name="xiaomi", aliases=("mimo", "xiaomi-mimo"), env_vars=("XIAOMI_API_KEY",), base_url="https://api.xiaomimimo.com/v1", supports_health_check=False, supports_vision=True, supports_vision_tool_messages=False))
register_provider(ProviderProfile(name="zai", aliases=("glm", "z-ai", "z.ai", "zhipu"), env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), display_name="Z.AI (GLM)", description="Z.AI / GLM - Zhipu AI models", signup_url="https://z.ai/", fallback_models=("glm-5.2", "glm-5", "glm-4-9b"), base_url="https://api.z.ai/api/paas/v4", default_aux_model="glm-4.5-flash"))
