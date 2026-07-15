"""Coding-agent model configuration over one injected provider collection."""

from __future__ import annotations

import inspect
import json
import os
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from travis.agent.async_utils import run_sync
from travis.ai.auth import (
    ApiKeyAuth,
    AuthResult,
    ModelAuth,
    ProviderAuth,
    oauth_auth_from_mapping,
)
from travis.ai.env_config import ModelConfig
from travis.ai.model_cost import cost_from_mapping
from travis.ai.models import Models, Provider, ProviderStreams
from travis.ai.provider_metadata import string_mapping
from travis.ai.providers.all import builtin_models, default_provider_streams
from travis.ai.types import Context, Cost, Model, SimpleStreamOptions
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.resolve_config_value import (
    get_config_value_env_var_names,
    is_command_config_value,
    is_config_value_configured,
    resolve_config_value,
    resolve_config_value_or_throw,
    resolve_headers_or_throw,
)


def _agent_models_path() -> Path:
    return Path.home() / ".travis234" / "agent" / "models.json"


class ProviderRegistration:
    def __init__(self, close: Callable[[], None]) -> None:
        self._close = close
        self._closed = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close()


class ModelRegistry:
    """Loads app configuration without becoming a second provider authority."""

    def __init__(
        self,
        auth_storage: AuthStorage,
        models_json_path: str | os.PathLike[str] | None,
        *,
        runtime: Models | None = None,
        provider_config: ModelConfig | None = None,
    ) -> None:
        self.auth_storage = auth_storage
        self.runtime = runtime or builtin_models(
            credentials=auth_storage,
            config=provider_config,
        )
        if self.runtime.credentials is not auth_storage:
            raise ValueError("model runtime and registry must share one credential store")
        self._models_json_path = (
            Path(models_json_path).expanduser().resolve()
            if models_json_path
            else None
        )
        self._provider_config = provider_config
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._disk_provider_configs: dict[str, dict[str, Any]] = {}
        self._registered_provider_configs: dict[str, dict[str, Any]] = {}
        self._registered_provider_bases: dict[str, Provider | None] = {}
        self._extension_stacks: dict[
            str,
            list[tuple[object, str, dict[str, Any]]],
        ] = {}
        self._extension_bases: dict[str, Provider | None] = {}
        self._runtime_provider_overrides: dict[str, str] = {}
        self.load_models()

    @staticmethod
    def create(
        auth_storage: AuthStorage,
        models_json_path: str | os.PathLike[str] | None = None,
        *,
        provider_config: ModelConfig | None = None,
    ) -> "ModelRegistry":
        return ModelRegistry(
            auth_storage,
            models_json_path or _agent_models_path(),
            provider_config=provider_config,
        )

    @staticmethod
    def in_memory(
        auth_storage: AuthStorage | None = None,
        *,
        provider_config: ModelConfig | None = None,
    ) -> "ModelRegistry":
        return ModelRegistry(
            auth_storage or AuthStorage.in_memory(),
            None,
            provider_config=provider_config,
        )

    def refresh(self) -> None:
        self.auth_storage.reload()
        self.load_models()
        self.runtime.refresh()

    def set_offline(self, enabled: bool) -> None:
        self.runtime.offline = bool(enabled)

    @property
    def offline(self) -> bool:
        return self.runtime.offline

    def load_models(self) -> None:
        with self._lock:
            fresh = builtin_models(
                credentials=self.auth_storage,
                config=self._provider_config,
            )
            self.runtime.clear_providers()
            for provider in fresh.get_providers():
                self.runtime.set_provider(provider)

            self._load_error = None
            self._disk_provider_configs = self._read_models_config()
            if self._load_error is None:
                for provider_id, config in self._disk_provider_configs.items():
                    self._apply_provider_config(provider_id, config)
            self._registered_provider_bases.clear()
            for provider_id, config in self._registered_provider_configs.items():
                self._registered_provider_bases[provider_id] = self.runtime.get_provider(provider_id)
                self._apply_provider_config(provider_id, config)
            for provider_id, base_url in self._runtime_provider_overrides.items():
                self._apply_base_url(provider_id, base_url)
            self._extension_bases.clear()
            for provider_id, stack in self._extension_stacks.items():
                if stack:
                    self._extension_bases[provider_id] = self.runtime.get_provider(provider_id)
                    self._apply_provider_config(provider_id, stack[-1][2])

    def get_error(self) -> str | None:
        return self._load_error

    def get_all(self) -> list[Model]:
        return list(self.runtime.get_models())

    def snapshot(self) -> tuple[Model, ...]:
        return self.runtime.get_models()

    def find(self, provider: str, model_id: str) -> Model | None:
        return self.runtime.get_model(provider, model_id)

    def get_providers(self) -> list[str]:
        return [provider.id for provider in self.runtime.get_providers()]

    def ensure_model(self, model: Model) -> None:
        if self.find(model.provider, model.id) is None:
            self.register_model(model)

    def register_model(self, model: Model) -> bool:
        with self._lock:
            provider = self.runtime.get_provider(model.provider)
            if provider is not None:
                if any(existing.id == model.id for existing in provider.get_models()):
                    return False
                self.runtime.set_provider(provider.with_models((*provider.get_models(), model)))
                return True
            self.runtime.set_provider(self._provider_for_unowned_model(model))
            return True

    def replace_model(self, model: Model) -> Model | None:
        with self._lock:
            provider = self.runtime.get_provider(model.provider)
            if provider is None:
                self.runtime.set_provider(self._provider_for_unowned_model(model))
                return None
            previous = self.find(model.provider, model.id)
            next_models = [
                model if existing.id == model.id else existing
                for existing in provider.get_models()
            ]
            if previous is None:
                next_models.append(model)
            self.runtime.set_provider(provider.with_models(next_models))
            return previous

    def remove_model(self, provider_id: str, model_id: str) -> Model | None:
        with self._lock:
            provider = self.runtime.get_provider(provider_id)
            if provider is None:
                return None
            previous = self.find(provider_id, model_id)
            if previous is not None:
                self.runtime.set_provider(
                    provider.with_models(
                        model for model in provider.get_models() if model.id != model_id
                    )
                )
            return previous

    def remove_provider_models(self, provider_id: str) -> tuple[Model, ...]:
        with self._lock:
            provider = self.runtime.get_provider(provider_id)
            if provider is None:
                return ()
            removed = provider.get_models()
            self.runtime.set_provider(provider.with_models(()))
            return removed

    def replace_all(self, models: Iterable[Model]) -> None:
        grouped: dict[str, list[Model]] = {}
        for model in models:
            grouped.setdefault(model.provider, []).append(model)
        with self._lock:
            for provider in self.runtime.get_providers():
                self.runtime.set_provider(
                    provider.with_models(grouped.pop(provider.id, ()))
                )
            for provider_models in grouped.values():
                first = provider_models[0]
                provider = self._provider_for_unowned_model(first)
                self.runtime.set_provider(provider.with_models(provider_models))

    def merge_discovered_models(self, models: Iterable[Model]) -> None:
        for model in models:
            self.replace_model(model)

    def get_available(self) -> list[Model]:
        return self.get_selectable()

    def get_selectable(self, active: Model | None = None) -> list[Model]:
        selectable = [model for model in self.snapshot() if self.is_selectable(model)]
        if active is not None and not any(
            model.provider == active.provider and model.id == active.id
            for model in selectable
        ):
            selectable.append(active)
        return selectable

    def is_selectable(self, model: Model) -> bool:
        if self.runtime.get_provider(model.provider) is None:
            return False
        try:
            return self.runtime.get_auth(model) is not None
        except Exception:  # noqa: BLE001 - status UI treats auth failures as unavailable.
            return False

    def has_configured_auth(self, model: Model) -> bool:
        return self.is_selectable(model)

    def get_api_key_and_headers(self, model: Model) -> dict[str, object]:
        try:
            resolution = self.runtime.get_auth(model)
            if resolution is None:
                return {"ok": True, "apiKey": None, "headers": None}
            result: dict[str, object] = {
                "ok": True,
                "apiKey": resolution.auth.api_key,
                "headers": dict(resolution.auth.headers or {}) or None,
            }
            if resolution.auth.base_url is not None:
                result["baseUrl"] = resolution.auth.base_url
            if resolution.env:
                result["env"] = dict(resolution.env)
            return result
        except Exception as error:  # noqa: BLE001 - UI callers consume value errors.
            return {"ok": False, "error": str(error)}

    def get_provider_auth_status(self, provider_id: str) -> dict[str, object]:
        status = self.auth_storage.get_auth_status(provider_id)
        if status.get("configured"):
            return status
        config = self._effective_provider_config(provider_id)
        api_key = config.get("apiKey", config.get("api_key")) if config else None
        if not isinstance(api_key, str):
            return status
        if is_command_config_value(api_key):
            return {"configured": True, "source": "models_json_command"}
        env_names = get_config_value_env_var_names(api_key)
        if env_names:
            return (
                {"configured": True, "source": "environment", "label": ", ".join(env_names)}
                if is_config_value_configured(api_key)
                else {"configured": False}
            )
        return {"configured": True, "source": "models_json_key"}

    def get_provider_display_name(self, provider_id: str) -> str:
        provider = self.runtime.get_provider(provider_id)
        return provider.name if provider is not None else provider_id

    def get_api_key_for_provider(self, provider_id: str) -> str | None:
        direct = self.auth_storage.get_api_key(provider_id)
        if direct is not None:
            return direct
        credential = self.auth_storage.get(provider_id)
        model = next(iter(self.runtime.get_models(provider_id)), None)
        if credential and credential.get("type") == "oauth" and model is not None:
            resolution = self.runtime.get_auth(model)
            return resolution.auth.api_key if resolution is not None else None
        config = self._effective_provider_config(provider_id)
        configured_key = config.get("apiKey", config.get("api_key")) if config else None
        if isinstance(configured_key, str):
            return resolve_config_value(
                configured_key,
                self.auth_storage.get_provider_env(provider_id),
                uncached=True,
            )
        return None

    def get_api_key_providers(self) -> list[str]:
        return [provider.id for provider in self.runtime.get_providers()]

    def is_using_oauth(self, model: Model) -> bool:
        credential = self.auth_storage.get(model.provider)
        return bool(credential and credential.get("type") == "oauth")

    def set_auth_credential(self, provider: str, credential: dict[str, object]) -> None:
        self.auth_storage.set(provider, credential)

    def login_oauth_provider(self, provider_id: str, callbacks: dict[str, object]) -> None:
        provider = self.runtime.get_provider(provider_id)
        if provider is None or provider.auth.oauth is None:
            raise RuntimeError(f"Unknown OAuth provider: {provider_id}")
        credential = _settle(provider.auth.oauth.login(callbacks))
        if not isinstance(credential, dict):
            raise RuntimeError(f"OAuth provider {provider_id} returned invalid credentials")
        self.auth_storage.set(provider_id, {"type": "oauth", **credential})

    def logout_provider(self, provider: str) -> None:
        self.auth_storage.remove(provider)

    def get_oauth_providers(self) -> list[dict[str, object]]:
        return [
            {"id": provider.id, "name": provider.auth.oauth.name}
            for provider in self.runtime.get_providers()
            if provider.auth.oauth is not None
        ]

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ):
        return self.runtime.stream_simple(model, context, options)

    def set_runtime_provider_override(self, provider: str, *, base_url: str) -> None:
        normalized = str(base_url or "").strip()
        if not normalized:
            return
        with self._lock:
            self._runtime_provider_overrides[provider] = normalized
            self._apply_base_url(provider, normalized)

    def register_provider(self, provider: str, config: dict[str, Any]) -> None:
        self._validate_provider_config(provider, config)
        with self._lock:
            if provider not in self._registered_provider_configs:
                self._registered_provider_bases[provider] = self.runtime.get_provider(provider)
            self._registered_provider_configs[provider] = {
                **self._registered_provider_configs.get(provider, {}),
                **{key: value for key, value in config.items() if value is not None},
            }
            self._restore_provider(provider, self._registered_provider_bases.get(provider))
            self._apply_provider_config(provider, self._registered_provider_configs[provider])

    def unregister_provider(self, provider: str) -> bool:
        with self._lock:
            if provider not in self._registered_provider_configs:
                return False
            self._registered_provider_configs.pop(provider, None)
            self._restore_provider(provider, self._registered_provider_bases.pop(provider, None))
            return True

    def has_registered_provider(self, provider: str) -> bool:
        return provider in self._registered_provider_configs

    def register_extension(
        self,
        source_id: str,
        provider: str,
        config: Mapping[str, object],
    ) -> ProviderRegistration:
        normalized = dict(config)
        self._validate_provider_config(provider, normalized)
        token = object()
        with self._lock:
            if provider not in self._extension_stacks:
                self._extension_bases[provider] = self.runtime.get_provider(provider)
            self._extension_stacks.setdefault(provider, []).append(
                (token, source_id, normalized)
            )
            self._restore_provider(provider, self._extension_bases.get(provider))
            self._apply_provider_config(provider, normalized)

        def close() -> None:
            with self._lock:
                stack = self._extension_stacks.get(provider, [])
                stack[:] = [entry for entry in stack if entry[0] is not token]
                base = self._extension_bases.get(provider)
                self._restore_provider(provider, base)
                if stack:
                    self._apply_provider_config(provider, stack[-1][2])
                else:
                    self._extension_stacks.pop(provider, None)
                    self._extension_bases.pop(provider, None)

        return ProviderRegistration(close)

    def _read_models_config(self) -> dict[str, dict[str, Any]]:
        path = self._models_json_path
        if path is None or not path.exists():
            return {}
        try:
            parsed = json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))
            if not isinstance(parsed, dict):
                raise RuntimeError("models.json root must be an object")
            providers = parsed.get("providers", {})
            if not isinstance(providers, dict):
                raise RuntimeError('models.json "providers" must be an object')
            result = {
                str(provider): dict(config)
                for provider, config in providers.items()
                if isinstance(config, dict)
            }
            for provider, config in result.items():
                self._validate_provider_config(provider, config, disk=True)
            return result
        except Exception as error:  # noqa: BLE001 - built-ins remain available.
            self._load_error = f"Failed to load models.json: {error}\n\nFile: {path}"
            return {}

    def _apply_provider_config(self, provider_id: str, config: dict[str, Any]) -> None:
        existing = self.runtime.get_provider(provider_id)
        models = list(existing.get_models()) if existing is not None else []
        raw_models = config.get("models")
        if isinstance(raw_models, list) and raw_models:
            models = [
                self._model_from_config(provider_id, config, raw, existing)
                for raw in raw_models
                if isinstance(raw, dict)
            ]
        else:
            models = [self._apply_model_config(model, config) for model in models]

        model_overrides = config.get("modelOverrides", config.get("model_overrides"))
        if isinstance(model_overrides, dict):
            models = [
                self._apply_model_override(model, model_overrides.get(model.id))
                if isinstance(model_overrides.get(model.id), dict)
                else model
                for model in models
            ]

        auth = self._provider_auth(config, existing)
        streams = self._custom_provider_streams(config)
        oauth_config = config.get("oauth")
        oauth_name = oauth_config.get("name") if isinstance(oauth_config, dict) else None
        name = str(config.get("name") or oauth_name or (existing.name if existing else provider_id))
        base_url = str(config.get("baseUrl") or (existing.base_url if existing else "")) or None
        if existing is not None:
            configured = existing.reconfigured(
                name=name,
                base_url=base_url,
                auth=auth,
                models=models,
                api=streams,
            )
        else:
            configured = Provider(
                id=provider_id,
                name=name,
                base_url=base_url,
                auth=auth,
                models=models,
                api=streams or default_provider_streams(config=self._provider_config),
            )
        self.runtime.set_provider(configured)

    def _provider_auth(self, config: dict[str, Any], existing: Provider | None) -> ProviderAuth:
        base_api_key = existing.auth.api_key if existing is not None else None
        oauth_config = config.get("oauth")
        oauth = (
            oauth_auth_from_mapping(oauth_config)
            if isinstance(oauth_config, dict)
            else (existing.auth.oauth if existing is not None else None)
        )
        has_request_config = any(
            key in config
            for key in ("apiKey", "api_key", "headers", "authHeader", "auth_header")
        )
        api_key = (
            self._configured_api_key_auth(config, base_api_key)
            if has_request_config
            else base_api_key
        )
        if api_key is None and oauth is None:
            api_key = _keyless_auth()
        return ProviderAuth(api_key=api_key, oauth=oauth)

    def _configured_api_key_auth(
        self,
        config: dict[str, Any],
        base: ApiKeyAuth | None,
    ) -> ApiKeyAuth:
        configured_key = config.get("apiKey", config.get("api_key"))
        configured_headers = config.get("headers")
        auth_header = bool(config.get("authHeader", config.get("auth_header", False)))

        def resolve(model, context, credential):
            base_result = _settle(base.resolve(model, context, credential)) if base is not None else None
            credential_env = (
                {str(key): str(value) for key, value in credential.get("env", {}).items()}
                if credential and isinstance(credential.get("env"), dict)
                else {}
            )
            api_key = base_result.auth.api_key if isinstance(base_result, AuthResult) else None
            if api_key is None and credential and credential.get("key"):
                api_key = resolve_config_value(str(credential["key"]), credential_env, uncached=True)
            if api_key is None and isinstance(configured_key, str):
                api_key = resolve_config_value_or_throw(
                    configured_key,
                    f'API key for provider "{model.provider}"',
                    credential_env,
                )
            headers = {
                **(dict(base_result.auth.headers or {}) if isinstance(base_result, AuthResult) else {}),
                **(resolve_headers_or_throw(configured_headers, f'provider "{model.provider}"', credential_env) or {}),
                **(resolve_headers_or_throw(model.headers, f'model "{model.provider}/{model.id}"', credential_env) or {}),
            }
            if auth_header:
                if not api_key:
                    raise RuntimeError(f'No API key found for "{model.provider}"')
                headers["Authorization"] = f"Bearer {api_key}"
            if base_result is None and api_key is None and not headers:
                return None
            base_auth = base_result.auth if isinstance(base_result, AuthResult) else ModelAuth()
            return AuthResult(
                auth=ModelAuth(
                    api_key=api_key,
                    headers=headers or None,
                    base_url=base_auth.base_url,
                ),
                source=base_result.source if isinstance(base_result, AuthResult) else "provider config",
                env={
                    **(dict(base_result.env or {}) if isinstance(base_result, AuthResult) else {}),
                    **credential_env,
                } or None,
            )

        return ApiKeyAuth(
            name=str(config.get("name") or (base.name if base else "Provider API key")),
            resolve=resolve,
            login=base.login if base is not None else None,
        )

    def _custom_provider_streams(self, config: dict[str, Any]) -> ProviderStreams | None:
        stream_simple = config.get("streamSimple", config.get("stream_simple"))
        stream = config.get("stream") or stream_simple
        if callable(stream) and callable(stream_simple):
            return ProviderStreams(stream=stream, stream_simple=stream_simple)
        return None

    def _restore_provider(self, provider_id: str, provider: Provider | None) -> None:
        if provider is None:
            self.runtime.delete_provider(provider_id)
        else:
            self.runtime.set_provider(provider)

    def _model_from_config(
        self,
        provider_id: str,
        provider_config: dict[str, Any],
        raw: dict[str, Any],
        existing: Provider | None,
    ) -> Model:
        model_id = str(raw.get("id") or "")
        if not model_id:
            raise RuntimeError(f'Provider {provider_id}: model missing "id"')
        base = next(
            (model for model in existing.get_models() if model.id == model_id),
            None,
        ) if existing is not None else None
        api = raw.get("api") or provider_config.get("api") or (base.api if base else None)
        base_url = raw.get("baseUrl") or provider_config.get("baseUrl") or (base.base_url if base else None)
        if not api:
            raise RuntimeError(f'Provider {provider_id}, model {model_id}: no "api" specified.')
        if not base_url:
            raise RuntimeError(f'Provider {provider_id}, model {model_id}: no "baseUrl" specified.')
        return Model(
            id=model_id,
            name=str(raw.get("name") or (base.name if base else model_id)),
            api=str(api),
            provider=provider_id,
            base_url=str(base_url),
            reasoning=bool(raw.get("reasoning", base.reasoning if base else False)),
            thinking_level_map=string_mapping(
                raw.get("thinkingLevelMap", raw.get("thinking_level_map", base.thinking_level_map if base else None))
            ),
            input=_string_list(raw.get("input"), base.input if base else ["text"]),
            cost=cost_from_mapping(raw.get("cost"), base.cost if base else None),
            context_window=int(raw.get("contextWindow", raw.get("context_window", base.context_window if base else 128_000))),
            max_tokens=int(raw.get("maxTokens", raw.get("max_tokens", base.max_tokens if base else 16_384))),
            headers=_string_mapping(raw.get("headers")),
            compat=_merge_compat(provider_config.get("compat"), raw.get("compat", base.compat if base else None)),
        )

    def _apply_model_config(self, model: Model, config: dict[str, Any]) -> Model:
        return replace(
            model,
            base_url=str(config.get("baseUrl") or model.base_url),
            compat=_merge_compat(model.compat, config.get("compat")),
        )

    def _apply_model_override(self, model: Model, override: dict[str, Any]) -> Model:
        return Model(
            id=model.id,
            name=str(override.get("name", model.name)),
            api=str(override.get("api", model.api)),
            provider=model.provider,
            base_url=str(override.get("baseUrl", model.base_url)),
            reasoning=bool(override.get("reasoning", model.reasoning)),
            thinking_level_map={
                **(model.thinking_level_map or {}),
                **(string_mapping(override.get("thinkingLevelMap")) or {}),
            } or None,
            input=_string_list(override.get("input"), model.input),
            cost=cost_from_mapping(override.get("cost"), model.cost),
            context_window=int(override.get("contextWindow", model.context_window)),
            max_tokens=int(override.get("maxTokens", model.max_tokens)),
            headers={
                **(model.headers or {}),
                **(_string_mapping(override.get("headers")) or {}),
            } or None,
            compat=_merge_compat(model.compat, override.get("compat")),
        )

    def _apply_base_url(self, provider_id: str, base_url: str) -> None:
        provider = self.runtime.get_provider(provider_id)
        if provider is None:
            return
        self.runtime.set_provider(provider.with_base_url(base_url))

    def _provider_for_unowned_model(self, model: Model) -> Provider:
        return Provider(
            id=model.provider,
            name=model.provider,
            base_url=model.base_url or None,
            auth=ProviderAuth(api_key=_credential_or_keyless_auth()),
            models=[model],
            api=default_provider_streams(config=self._provider_config),
        )

    def _effective_provider_config(self, provider: str) -> dict[str, Any]:
        config = {
            **self._disk_provider_configs.get(provider, {}),
            **self._registered_provider_configs.get(provider, {}),
        }
        stack = self._extension_stacks.get(provider)
        if stack:
            config.update(stack[-1][2])
        return config

    def _validate_provider_config(
        self,
        provider: str,
        config: dict[str, Any],
        *,
        disk: bool = False,
    ) -> None:
        stream = config.get("streamSimple", config.get("stream_simple"))
        if callable(stream) and not config.get("api"):
            raise RuntimeError(f'Provider {provider}: "api" is required when registering streamSimple.')
        raw_models = config.get("models")
        if raw_models is not None and not isinstance(raw_models, list):
            raise RuntimeError(f"Provider {provider}: models must be an array.")
        if not raw_models:
            permitted = any(
                config.get(key)
                for key in ("baseUrl", "headers", "compat", "modelOverrides", "model_overrides")
            )
            if disk and not permitted:
                raise RuntimeError(
                    f'Provider {provider}: must specify "baseUrl", "headers", "compat", "modelOverrides", or "models".'
                )
            return
        existing = self.runtime.get_provider(provider)
        if existing is None and not config.get("baseUrl"):
            raise RuntimeError(f'Provider {provider}: "baseUrl" is required when defining models.')
        if existing is None and not config.get("apiKey") and not isinstance(config.get("oauth"), dict):
            raise RuntimeError(f'Provider {provider}: "apiKey" or "oauth" is required when defining models.')
        for raw in raw_models:
            if isinstance(raw, dict) and not (raw.get("api") or config.get("api") or existing):
                raise RuntimeError(f'Provider {provider}, model {raw.get("id")}: no "api" specified.')


def _keyless_auth() -> ApiKeyAuth:
    return ApiKeyAuth(
        name="Keyless provider",
        resolve=lambda _model, _context, _credential: AuthResult(
            auth=ModelAuth(),
            source="keyless provider",
        ),
    )


def _credential_or_keyless_auth() -> ApiKeyAuth:
    def resolve(model, context, credential):
        del context
        if credential and credential.get("key"):
            return AuthResult(
                auth=ModelAuth(api_key=str(credential["key"])),
                env=(
                    {str(key): str(value) for key, value in credential["env"].items()}
                    if isinstance(credential.get("env"), dict)
                    else None
                ),
                source="stored credential",
            )
        if model.base_url:
            return AuthResult(auth=ModelAuth(), source="keyless provider")
        return None

    return ApiKeyAuth(name="Provider API key", resolve=resolve)


def _settle(value: Any) -> Any:
    return run_sync(value) if inspect.isawaitable(value) else value


def _string_list(value: object, fallback: Iterable[str]) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else list(fallback)


def _string_mapping(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items()}


def _merge_compat(base: object, override: object) -> dict[str, Any] | None:
    base_mapping = dict(base) if isinstance(base, dict) else {}
    override_mapping = dict(override) if isinstance(override, dict) else {}
    if not base_mapping and not override_mapping:
        return None
    merged: dict[str, Any] = {**base_mapping, **override_mapping}
    for key in ("openRouterRouting", "vercelGatewayRouting", "chatTemplateKwargs"):
        left = base_mapping.get(key)
        right = override_mapping.get(key)
        if isinstance(left, dict) or isinstance(right, dict):
            merged[key] = {
                **(left if isinstance(left, dict) else {}),
                **(right if isinstance(right, dict) else {}),
            }
    return merged


def _strip_json_comments(text: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index + 1 : index + 2]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            index = text.find("\n", index)
            if index < 0:
                break
            output.append("\n")
            index += 1
            continue
        if character == "/" and following == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                break
            index = end + 2
            continue
        output.append(character)
        index += 1
    return "".join(output)


__all__ = ["ModelRegistry", "ProviderRegistration"]
