"""Register built-in api providers. Port of providers/register-builtins.ts."""

from __future__ import annotations

from appv231.ai.providers.appv2_env import create_appv2_env_provider
from appv231.ai.stream import register_api_provider


def register_builtin_providers(prefix: str = "APPV2_WORKER_LLM", dotenv_path: str = ".env") -> None:
    register_api_provider(create_appv2_env_provider(prefix, dotenv_path))
