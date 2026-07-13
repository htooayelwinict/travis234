"""Register the built-in API providers."""

from __future__ import annotations

from travis.ai.env_config import ModelConfig
from travis.ai.providers.travis_env import create_travis_provider
from travis.ai.stream import register_api_provider


def register_builtin_providers(
    prefix: str = "TRAVIS234_WORKER_LLM",
    dotenv_path: str = ".env",
    *,
    config: ModelConfig | None = None,
) -> None:
    register_api_provider(create_travis_provider(prefix, dotenv_path, config=config))
