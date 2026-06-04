"""Environment loading for LLM-backed worker runtime wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.decompressor.model_client import OpenAICompatibleJSONClient


TRUE_VALUES = {"1", "true", "yes", "on"}
CONFIG_KEYS = (
    "WORKER_LLM_ENABLED",
    "WORKER_LLM_API_KEY",
    "WORKER_LLM_MODEL",
    "WORKER_LLM_BASE_URL",
    "WORKER_LLM_TIMEOUT_SECONDS",
    "WORKER_LLM_TEMPERATURE",
    "WORKER_LLM_RESPONSE_FORMAT",
    "WORKER_LLM_PROVIDER_SORT",
    "WORKER_LLM_MAX_TOKENS",
    "WORKER_MAX_PARALLEL_INSTANCES",
    "WORKER_TOOL_TIMEOUT_SECONDS",
    "WORKER_MAX_FILE_BYTES",
    "WORKER_WEB_SEARCH_PROVIDER",
    "WORKER_WEB_SEARCH_API_KEY",
    "WORKER_WEB_SEARCH_MAX_RESULTS",
    "WORKER_RETRY_ADVISOR_ENABLED",
    "WORKER_RETRY_ADVISOR_MODEL",
    "WORKER_RETRY_ADVISOR_MAX_TOKENS",
    "WORKER_RETRY_ADVISOR_TIMEOUT_SECONDS",
    "BRAVE_SEARCH_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_PROVIDER_SORT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


@dataclass(frozen=True)
class WorkerRuntimeConfig:
    llm_enabled: bool
    model: str | None = None
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    response_format: str = "json_schema"
    provider_sort: str | None = "latency"
    max_tokens: int | None = None
    max_parallel_instances: int = 3
    tool_timeout_seconds: float = 15.0
    max_file_bytes: int = 200_000
    web_search_provider: str = "brave"
    web_search_api_key: str | None = None
    web_search_max_results: int = 5
    retry_advisor_enabled: bool = False
    retry_advisor_model: str | None = None
    retry_advisor_max_tokens: int = 500
    retry_advisor_timeout_seconds: float = 20.0


def load_dotenv_values(path: str | Path = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_worker_runtime_config(dotenv_path: str | Path = ".env") -> WorkerRuntimeConfig:
    config = load_dotenv_values(dotenv_path)
    for key in CONFIG_KEYS:
        if key in os.environ:
            config[key] = os.environ[key]

    enabled = config.get("WORKER_LLM_ENABLED", "").lower() in TRUE_VALUES
    api_key = config.get("WORKER_LLM_API_KEY") or config.get("OPENROUTER_API_KEY") or config.get("OPENAI_API_KEY")
    model = config.get("WORKER_LLM_MODEL") or config.get("OPENROUTER_MODEL") or config.get("OPENAI_MODEL")

    return WorkerRuntimeConfig(
        llm_enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=config.get("WORKER_LLM_BASE_URL") or config.get("OPENROUTER_BASE_URL") or "https://api.openai.com/v1",
        timeout_seconds=float(config.get("WORKER_LLM_TIMEOUT_SECONDS", "60")),
        temperature=float(config.get("WORKER_LLM_TEMPERATURE", "0")),
        response_format=config.get("WORKER_LLM_RESPONSE_FORMAT", "json_schema"),
        provider_sort=config.get("WORKER_LLM_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT") or "latency",
        max_tokens=_optional_int(config.get("WORKER_LLM_MAX_TOKENS"), default=None),
        max_parallel_instances=_positive_int(config.get("WORKER_MAX_PARALLEL_INSTANCES"), default=3),
        tool_timeout_seconds=float(config.get("WORKER_TOOL_TIMEOUT_SECONDS", "15")),
        max_file_bytes=_positive_int(config.get("WORKER_MAX_FILE_BYTES"), default=200_000),
        web_search_provider=(config.get("WORKER_WEB_SEARCH_PROVIDER") or "brave").strip().lower(),
        web_search_api_key=config.get("WORKER_WEB_SEARCH_API_KEY") or config.get("BRAVE_SEARCH_API_KEY"),
        web_search_max_results=_positive_int(config.get("WORKER_WEB_SEARCH_MAX_RESULTS"), default=5),
        retry_advisor_enabled=config.get("WORKER_RETRY_ADVISOR_ENABLED", "").lower() in TRUE_VALUES,
        retry_advisor_model=config.get("WORKER_RETRY_ADVISOR_MODEL") or None,
        retry_advisor_max_tokens=_positive_int(config.get("WORKER_RETRY_ADVISOR_MAX_TOKENS"), default=500),
        retry_advisor_timeout_seconds=float(config.get("WORKER_RETRY_ADVISOR_TIMEOUT_SECONDS", "20")),
    )


def build_worker_model_client(
    dotenv_path: str | Path = ".env",
    *,
    client_factory: type[OpenAICompatibleJSONClient] = OpenAICompatibleJSONClient,
) -> Any | None:
    config = load_worker_runtime_config(dotenv_path)
    if not config.llm_enabled:
        return None
    if not config.api_key:
        raise ValueError("WORKER_LLM_ENABLED=true requires WORKER_LLM_API_KEY, OPENROUTER_API_KEY, or OPENAI_API_KEY.")
    if not config.model:
        raise ValueError("WORKER_LLM_ENABLED=true requires WORKER_LLM_MODEL, OPENROUTER_MODEL, or OPENAI_MODEL.")

    return client_factory(
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
        timeout_seconds=config.timeout_seconds,
        temperature=config.temperature,
        response_format=config.response_format,
        provider_sort=config.provider_sort,
        max_tokens=config.max_tokens,
    )


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].strip()
    return value


def _optional_int(value: str | None, *, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    if value.lower() in {"none", "null"}:
        return None
    return _positive_int(value, default=default or 1)


def _positive_int(value: str | None, *, default: int) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("Worker runtime integer settings must be positive.")
    return parsed
