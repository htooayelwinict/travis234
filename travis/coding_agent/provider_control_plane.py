"""Single injected authority for coding-agent provider services."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping
import threading

from travis.ai.event_stream import AssistantMessageEventStream
from travis.ai.stream import (
    ApiProviderRegistry,
    ProviderRegistration,
    clone_default_api_provider_registry,
)
from travis.ai.types import Context, Model, SimpleStreamOptions
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.config import get_auth_path, get_models_path
from travis.coding_agent.model_registry import ModelRegistry


class ProviderControlPlane:
    def __init__(
        self,
        *,
        auth: AuthStorage,
        models: ModelRegistry,
        api_providers: ApiProviderRegistry,
        capabilities: Mapping[str, object] | None = None,
    ) -> None:
        self.auth = auth
        self.models = models
        self.api_providers = api_providers
        self.capabilities = dict(capabilities or {})
        self._fallback_counts: dict[str, int] = {}
        self._extension_stacks: dict[str, list[tuple[object, str, dict[str, object]]]] = {}
        self._extension_lock = threading.RLock()
        self.auth.set_fallback_resolver(self._fallback_resolver)

    @classmethod
    def in_memory(cls) -> "ProviderControlPlane":
        auth = AuthStorage.in_memory()
        api_providers = ApiProviderRegistry()
        return cls(
            auth=auth,
            models=ModelRegistry(auth, None, api_providers),
            api_providers=api_providers,
        )

    @classmethod
    def create_default(
        cls,
        paths: Mapping[str, str | Path] | None = None,
    ) -> "ProviderControlPlane":
        paths = paths or {}
        api_providers = clone_default_api_provider_registry()
        auth = AuthStorage.create(paths.get("auth") or get_auth_path())
        models = ModelRegistry(
            auth,
            paths.get("models") or get_models_path(),
            api_providers,
        )
        return cls(
            auth=auth,
            models=models,
            api_providers=api_providers,
        )

    def _fallback_resolver(self, provider: str) -> str | None:
        self._fallback_counts[provider] = self._fallback_counts.get(provider, 0) + 1
        return self.models.resolve_fallback_api_key(provider)

    def fallback_resolution_count(self, provider: str) -> int:
        return self._fallback_counts.get(provider, 0)

    def refresh(self) -> None:
        self.auth.reload()
        self.models.refresh()

    def ensure_model(self, model: Model) -> None:
        self.models.register_model(model)

    def merge_discovered_models(self, models: list[Model]) -> None:
        for model in models:
            self.models.replace_model(model)

    def register_extension(
        self,
        source_id: str,
        provider_config: Mapping[str, object],
    ) -> ProviderRegistration:
        config = dict(provider_config)
        provider = str(config.pop("provider", "") or config.get("id") or config.get("name") or "")
        if not provider:
            raise ValueError("extension provider registration requires a provider id")
        token = object()
        with self._extension_lock:
            self._extension_stacks.setdefault(provider, []).append((token, source_id, config))
            self._apply_extension_top(provider)

        def close() -> None:
            with self._extension_lock:
                stack = self._extension_stacks.get(provider, [])
                stack[:] = [entry for entry in stack if entry[0] is not token]
                if not stack:
                    self._extension_stacks.pop(provider, None)
                self._apply_extension_top(provider)

        return ProviderRegistration(close)

    def _apply_extension_top(self, provider: str) -> None:
        if self.models.has_registered_provider(provider):
            self.models.unregister_provider(provider)
        self.auth.unregister_oauth_provider(provider)
        stack = self._extension_stacks.get(provider)
        if not stack:
            return
        config = stack[-1][2]
        self.models.register_provider(provider, config)
        oauth = config.get("oauth")
        if isinstance(oauth, dict):
            self.auth.register_oauth_provider(provider, oauth)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        auth = self.models.get_api_key_and_headers(model)
        if auth.get("ok") is False:
            raise RuntimeError(str(auth.get("error") or "Failed to resolve request auth"))
        explicit_api_key = getattr(options, "api_key", None)
        api_key = explicit_api_key or auth.get("apiKey")
        headers = dict(auth.get("headers") or {})
        headers.update(getattr(options, "headers", None) or {})
        next_options = replace(
            options or SimpleStreamOptions(),
            api_key=str(api_key) if api_key is not None else None,
            headers=headers or None,
        )
        return self.api_providers.require(model.api).stream_simple(model, context, next_options)


__all__ = ["ProviderControlPlane", "ProviderRegistration"]
