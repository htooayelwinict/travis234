"""coding-agent model registry service."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import threading
from typing import Any, Callable, Iterable

import travis.ai.models as ai_models
from travis.ai.stream import ApiProvider, ApiProviderRegistry, _DEFAULT_API_PROVIDER_REGISTRY
from travis.ai.types import Cost, Model
from travis.coding_agent.auth_storage import AuthStorage


def _agent_models_path() -> Path:
    return Path.home() / ".travis234" / "agent" / "models.json"


class ModelRegistry:
    def __init__(
        self,
        auth_storage: AuthStorage,
        models_json_path: str | os.PathLike[str] | None,
        api_providers: ApiProviderRegistry | None = None,
    ) -> None:
        self.authStorage = auth_storage
        self.auth_storage = auth_storage
        self._models_json_path = Path(models_json_path).expanduser().resolve() if models_json_path else None
        self.api_providers = api_providers or _DEFAULT_API_PROVIDER_REGISTRY
        self._lock = threading.RLock()
        self._models: list[Model] = []
        self._provider_request_configs: dict[str, dict[str, object]] = {}
        self._model_request_headers: dict[str, dict[str, str]] = {}
        self._registered_providers: dict[str, dict[str, Any]] = {}
        self._load_error: str | None = None
        self.authStorage.setFallbackResolver(self._fallback_api_key)
        self.loadModels()

    def _fallback_api_key(self, provider: str) -> str | None:
        value = self._provider_request_configs.get(provider, {}).get("apiKey")
        if not isinstance(value, str):
            return None
        return ai_models._resolve_config_value(value)  # noqa: SLF001

    def resolve_fallback_api_key(self, provider: str) -> str | None:
        with self._lock:
            return self._fallback_api_key(provider)

    @staticmethod
    def create(authStorage: AuthStorage, modelsJsonPath: str | os.PathLike[str] | None = None) -> "ModelRegistry":
        return ModelRegistry(authStorage, modelsJsonPath or _agent_models_path())

    @staticmethod
    def inMemory(authStorage: AuthStorage) -> "ModelRegistry":
        return ModelRegistry(authStorage, None)

    in_memory = inMemory

    def refresh(self) -> None:
        with self._lock:
            for provider_name in list(self._registered_providers):
                self.api_providers.unregister_source(f"provider:{provider_name}")
            self._provider_request_configs.clear()
            self._model_request_headers.clear()
            self._load_error = None
            self.loadModels()
            for provider_name, config in list(self._registered_providers.items()):
                self._apply_provider_config(provider_name, config)

    def getError(self) -> str | None:
        return self._load_error

    get_error = getError

    def loadModels(self) -> None:
        with self._lock:
            custom_models: list[Model] = []
            overrides: dict[str, dict[str, object]] = {}
            model_overrides: dict[str, dict[str, dict[str, object]]] = {}
            if self._models_json_path is not None:
                custom_models, overrides, model_overrides, self._load_error = self._load_custom_models(self._models_json_path)

            builtins = self._load_builtin_models(overrides, model_overrides)
            self._models = self._merge_custom_models(builtins, custom_models)

    load_models = loadModels

    def _load_builtin_models(
        self,
        overrides: dict[str, dict[str, object]],
        model_overrides: dict[str, dict[str, dict[str, object]]],
    ) -> list[Model]:
        models: list[Model] = []
        for provider in ai_models.get_providers():
            provider_override = overrides.get(provider) or {}
            per_model_overrides = model_overrides.get(provider) or {}
            for model in ai_models.get_models(provider):
                next_model = model
                if isinstance(provider_override.get("baseUrl"), str):
                    next_model = replace(next_model)
                    next_model.base_url = str(provider_override["baseUrl"])
                compat_headers = provider_override.get("headers")
                if isinstance(compat_headers, dict):
                    if next_model is model:
                        next_model = replace(next_model)
                    next_model.headers = {str(k): str(v) for k, v in compat_headers.items()}
                model_override = per_model_overrides.get(model.id)
                if model_override:
                    if next_model is model:
                        next_model = replace(next_model)
                    next_model = self._apply_model_override(next_model, model_override)
                models.append(next_model)
        return models

    def _merge_custom_models(self, builtins: list[Model], custom_models: list[Model]) -> list[Model]:
        merged = list(builtins)
        for custom_model in custom_models:
            for index, existing in enumerate(merged):
                if existing.provider == custom_model.provider and existing.id == custom_model.id:
                    merged[index] = custom_model
                    break
            else:
                merged.append(custom_model)
        return merged

    def _load_custom_models(
        self,
        models_json_path: Path,
    ) -> tuple[list[Model], dict[str, dict[str, object]], dict[str, dict[str, dict[str, object]]], str | None]:
        if not models_json_path.exists():
            return [], {}, {}, None
        try:
            parsed = json.loads(_strip_json_comments(models_json_path.read_text(encoding="utf-8")))
            if not isinstance(parsed, dict):
                raise RuntimeError("models.json root must be an object")
            providers = parsed.get("providers")
            if providers is None:
                providers = {}
            if not isinstance(providers, dict):
                raise RuntimeError('models.json "providers" must be an object')

            overrides: dict[str, dict[str, object]] = {}
            model_overrides: dict[str, dict[str, dict[str, object]]] = {}
            custom_models: list[Model] = []
            built_in_providers = set(ai_models.get_providers())

            for provider_name, raw_config in providers.items():
                if not isinstance(raw_config, dict):
                    continue
                provider = str(provider_name)
                config = dict(raw_config)
                models_config = config.get("models") or []
                if not isinstance(models_config, list):
                    raise RuntimeError(f"Provider {provider}: models must be an array.")
                model_overrides_config = config.get("modelOverrides", config.get("model_overrides"))
                if model_overrides_config and isinstance(model_overrides_config, dict):
                    model_overrides[provider] = {
                        str(model_id): dict(override)
                        for model_id, override in model_overrides_config.items()
                        if isinstance(override, dict)
                    }
                    for model_id, override in model_overrides[provider].items():
                        self._store_model_headers(provider, model_id, override.get("headers"))

                if config.get("baseUrl") or config.get("headers") or config.get("compat") or model_overrides_config:
                    overrides[provider] = config

                self._store_provider_request_config(provider, config)

                if not models_config:
                    if not (config.get("baseUrl") or config.get("headers") or config.get("compat") or model_overrides_config):
                        raise RuntimeError(
                            f'Provider {provider}: must specify "baseUrl", "headers", "compat", "modelOverrides", or "models".'
                        )
                    continue

                if provider not in built_in_providers:
                    if not config.get("baseUrl"):
                        raise RuntimeError(f'Provider {provider}: "baseUrl" is required when defining custom models.')
                    if not config.get("apiKey"):
                        raise RuntimeError(f'Provider {provider}: "apiKey" is required when defining custom models.')

                for raw_model in models_config:
                    if not isinstance(raw_model, dict):
                        continue
                    custom_models.append(self._model_from_config(provider, config, raw_model, built_in_providers))

            return custom_models, overrides, model_overrides, None
        except json.JSONDecodeError as error:
            return [], {}, {}, f"Failed to parse models.json: {error}\n\nFile: {models_json_path}"
        except Exception as error:  # noqa: BLE001 - Travis preserves built-ins and exposes load error.
            return [], {}, {}, f"Failed to load models.json: {error}\n\nFile: {models_json_path}"

    def _model_from_config(
        self,
        provider: str,
        provider_config: dict[str, object],
        model_config: dict[str, object],
        built_in_providers: set[str],
    ) -> Model:
        model_id = str(model_config.get("id") or "")
        if not model_id:
            raise RuntimeError(f"Provider {provider}: model missing \"id\"")
        api = model_config.get("api") or provider_config.get("api")
        base_url = model_config.get("baseUrl") or provider_config.get("baseUrl")
        if provider in built_in_providers and (api is None or base_url is None):
            builtins = ai_models.get_models(provider)
            if builtins:
                api = api or builtins[0].api
                base_url = base_url or builtins[0].base_url
        if not api:
            raise RuntimeError(f'Provider {provider}, model {model_id}: no "api" specified.')
        if not base_url:
            raise RuntimeError(f'Provider {provider}, model {model_id}: no "baseUrl" specified.')

        self._store_model_headers(provider, model_id, model_config.get("headers"))
        return Model(
            id=model_id,
            name=str(model_config.get("name") or model_id),
            api=str(api),
            provider=provider,
            base_url=str(base_url),
            reasoning=bool(model_config.get("reasoning", False)),
            thinking_level_map=_dict_or_none(model_config.get("thinkingLevelMap", model_config.get("thinking_level_map"))),
            input=_string_list(model_config.get("input"), ["text"]),
            cost=_cost_from_config(model_config.get("cost")),
            context_window=int(model_config.get("contextWindow", model_config.get("context_window", 128000))),
            max_tokens=int(model_config.get("maxTokens", model_config.get("max_tokens", 16384))),
            headers=None,
        )

    def _apply_model_override(self, model: Model, override: dict[str, object]) -> Model:
        next_model = replace(model)
        if "name" in override:
            next_model.name = str(override["name"])
        if "baseUrl" in override:
            next_model.base_url = str(override["baseUrl"])
        if "reasoning" in override:
            next_model.reasoning = bool(override["reasoning"])
        if "contextWindow" in override:
            next_model.context_window = int(override["contextWindow"])
        if "maxTokens" in override:
            next_model.max_tokens = int(override["maxTokens"])
        if isinstance(override.get("headers"), dict):
            next_model.headers = {str(k): str(v) for k, v in override["headers"].items()}  # type: ignore[index]
        return next_model

    def getAll(self) -> list[Model]:
        return list(self.snapshot())

    def snapshot(self) -> tuple[Model, ...]:
        with self._lock:
            return tuple(self._models)

    def register_model(self, model: Model) -> bool:
        with self._lock:
            if self.find(model.provider, model.id) is not None:
                return False
            self._models.append(model)
            return True

    def replace_model(self, model: Model) -> Model | None:
        with self._lock:
            for index, existing in enumerate(self._models):
                if (existing.provider, existing.id) == (model.provider, model.id):
                    self._models[index] = model
                    return existing
            self._models.append(model)
            return None

    def remove_model(self, provider: str, model_id: str) -> Model | None:
        with self._lock:
            for index, existing in enumerate(self._models):
                if (existing.provider, existing.id) == (provider, model_id):
                    return self._models.pop(index)
        return None

    def remove_provider_models(self, provider: str) -> tuple[Model, ...]:
        with self._lock:
            removed = tuple(model for model in self._models if model.provider == provider)
            self._models[:] = [model for model in self._models if model.provider != provider]
            return removed

    def replace_all(self, models: Iterable[Model]) -> None:
        with self._lock:
            self._models[:] = list(models)

    def getProviders(self) -> list[str]:
        with self._lock:
            return list(
                dict.fromkeys(
                    [model.provider for model in self._models]
                    + list(self._registered_providers)
                    + self.authStorage.list()
                )
            )

    def getApiKeyProviders(self) -> list[str]:
        from travis.ai.providers.catalog import get_provider_profile

        providers: list[str] = []
        for provider in self.getProviders():
            profile = get_provider_profile(provider)
            if (
                provider in self._registered_providers
                or provider in self._provider_request_configs
                or provider in self.authStorage.list()
                or (profile is not None and profile.auth_type not in {"virtual", "none"})
            ):
                providers.append(provider)
        return providers

    def getAvailable(self) -> list[Model]:
        return self.getSelectable()

    def isSelectable(self, model: Model) -> bool:
        from travis.ai.providers.catalog import get_provider_profile

        profile = get_provider_profile(model.provider)
        transport_available = self.api_providers.get(model.api) is not None or (
            profile is not None and profile.transport_available
        )
        if not transport_available:
            return False
        auth_free = profile is not None and profile.auth_type in {"virtual", "none"}
        local = model.base_url.startswith(("http://127.0.0.1", "http://localhost"))
        return auth_free or local or self.hasConfiguredAuth(model)

    def getSelectable(self, active: Model | None = None) -> list[Model]:
        selectable = [model for model in self.snapshot() if self.isSelectable(model)]
        if active is not None and not any(
            model.provider == active.provider and model.id == active.id for model in selectable
        ):
            selectable.append(active)
        return selectable

    def find(self, provider: str, model_id: str) -> Model | None:
        with self._lock:
            for model in self._models:
                if model.provider == provider and model.id == model_id:
                    return model
        return None

    def hasConfiguredAuth(self, model: Model) -> bool:
        provider_api_key = self._provider_request_configs.get(model.provider, {}).get("apiKey")
        return self.authStorage.hasAuth(model.provider) or (
            isinstance(provider_api_key, str) and ai_models._is_config_value_configured(provider_api_key)  # noqa: SLF001
        )

    get_all = getAll
    get_providers = getProviders
    get_api_key_providers = getApiKeyProviders
    get_available = getAvailable
    get_selectable = getSelectable
    is_selectable = isSelectable
    has_configured_auth = hasConfiguredAuth

    def _request_key(self, provider: str, model_id: str) -> str:
        return f"{provider}:{model_id}"

    def _store_provider_request_config(self, provider: str, config: dict[str, object]) -> None:
        api_key = config.get("apiKey", config.get("api_key"))
        headers = config.get("headers")
        auth_header = config.get("authHeader", config.get("auth_header"))
        if api_key is None and headers is None and auth_header is None:
            return
        self._provider_request_configs[provider] = {
            "apiKey": api_key,
            "headers": headers,
            "authHeader": auth_header,
        }

    def _store_model_headers(self, provider: str, model_id: str, headers: object) -> None:
        key = self._request_key(provider, model_id)
        if not isinstance(headers, dict) or not headers:
            self._model_request_headers.pop(key, None)
            return
        self._model_request_headers[key] = {str(header): str(value) for header, value in headers.items()}

    def getApiKeyAndHeaders(self, model: Model) -> dict[str, object]:
        try:
            provider_config = self._provider_request_configs.get(model.provider, {})
            api_key = self.authStorage.getApiKey(model.provider, {"includeFallback": False})
            provider_api_key = provider_config.get("apiKey")
            if api_key is None and isinstance(provider_api_key, str):
                api_key = ai_models._resolve_config_value_or_throw(  # noqa: SLF001
                    provider_api_key,
                    f'API key for provider "{model.provider}"',
                )

            headers: dict[str, str] = {}
            for source in (
                model.headers,
                provider_config.get("headers"),
                self._model_request_headers.get(self._request_key(model.provider, model.id)),
            ):
                resolved = ai_models._resolve_headers_or_throw(source, f'model "{model.provider}/{model.id}"')  # noqa: SLF001
                if resolved:
                    headers.update(resolved)

            if provider_config.get("authHeader"):
                if not api_key:
                    return {"ok": False, "error": f'No API key found for "{model.provider}"'}
                headers["Authorization"] = f"Bearer {api_key}"

            return {"ok": True, "apiKey": api_key, "headers": headers or None}
        except Exception as error:  # noqa: BLE001 - Travis returns request-auth resolution errors as values.
            return {"ok": False, "error": str(error)}

    get_api_key_and_headers = getApiKeyAndHeaders

    def getProviderAuthStatus(self, provider: str) -> dict[str, object]:
        auth_status = self.authStorage.getAuthStatus(provider)
        if auth_status.get("source") and auth_status.get("source") != "fallback":
            return auth_status

        provider_api_key = self._provider_request_configs.get(provider, {}).get("apiKey")
        if not isinstance(provider_api_key, str):
            return auth_status
        if provider_api_key.startswith("!"):
            return {"configured": True, "source": "models_json_command"}
        env_var_names = ai_models._config_value_env_var_names(provider_api_key)  # noqa: SLF001
        if env_var_names:
            if ai_models._is_config_value_configured(provider_api_key):  # noqa: SLF001
                return {"configured": True, "source": "environment", "label": ", ".join(env_var_names)}
            return {"configured": False}
        return {"configured": True, "source": "models_json_key"}

    get_provider_auth_status = getProviderAuthStatus

    def getProviderDisplayName(self, provider: str) -> str:
        registered = self._registered_providers.get(provider, {})
        if registered.get("name"):
            return str(registered["name"])
        oauth = registered.get("oauth")
        if isinstance(oauth, dict) and oauth.get("name"):
            return str(oauth["name"])
        return ai_models.get_provider_display_name(provider)

    get_provider_display_name = getProviderDisplayName

    def getApiKeyForProvider(self, provider: str) -> str | None:
        api_key = self.authStorage.getApiKey(provider, {"includeFallback": False})
        if api_key is not None:
            return api_key
        provider_api_key = self._provider_request_configs.get(provider, {}).get("apiKey")
        return (
            ai_models._resolve_config_value(str(provider_api_key), uncached=True)  # noqa: SLF001
            if isinstance(provider_api_key, str)
            else None
        )

    get_api_key_for_provider = getApiKeyForProvider

    def isUsingOAuth(self, model: Model) -> bool:
        credential = self.authStorage.get(model.provider)
        return credential is not None and credential.get("type") == "oauth"

    is_using_oauth = isUsingOAuth

    def setAuthCredential(self, provider: str, credential: dict[str, object]) -> None:
        self.authStorage.set(provider, credential)

    set_auth_credential = setAuthCredential

    def loginOAuthProvider(self, provider: str, callbacks: dict[str, object]) -> None:
        config = self._registered_providers.get(provider, {})
        oauth = config.get("oauth")
        if not isinstance(oauth, dict):
            raise RuntimeError(f"Unknown OAuth provider: {provider}")
        login = oauth.get("login")
        if not callable(login):
            raise RuntimeError(f"OAuth provider {provider} does not support login")
        credential = ai_models._settle_oauth_result(login(callbacks))  # noqa: SLF001
        if not isinstance(credential, dict):
            raise RuntimeError(f"OAuth provider {provider} returned invalid credentials")
        self.authStorage.set(provider, {"type": "oauth", **credential})

    login_oauth_provider = loginOAuthProvider

    def logoutProvider(self, provider: str) -> None:
        self.authStorage.remove(provider)

    logout_provider = logoutProvider

    def getOAuthProviders(self) -> list[dict[str, object]]:
        providers: list[dict[str, object]] = []
        for provider, config in self._registered_providers.items():
            oauth = config.get("oauth")
            if isinstance(oauth, dict):
                providers.append({"id": provider, "name": str(oauth.get("name") or provider)})
        return providers

    get_oauth_providers = getOAuthProviders

    def registerProvider(self, provider: str, config: dict[str, Any]) -> None:
        with self._lock:
            self._validate_provider_config(provider, config)
            self._apply_provider_config(provider, config)
            existing = self._registered_providers.get(provider)
            if existing is None:
                self._registered_providers[provider] = dict(config)
            else:
                existing.update({key: value for key, value in config.items() if value is not None})

    register_provider = registerProvider

    def has_registered_provider(self, provider: str) -> bool:
        with self._lock:
            return provider in self._registered_providers

    def unregisterProvider(self, provider: str) -> bool:
        with self._lock:
            if provider not in self._registered_providers:
                return False
            self.api_providers.unregister_source(f"provider:{provider}")
            self._registered_providers.pop(provider, None)
            self.refresh()
            return True

    unregister_provider = unregisterProvider

    def _validate_provider_config(self, provider: str, config: dict[str, object]) -> None:
        if (callable(config.get("streamSimple")) or callable(config.get("stream_simple"))) and not config.get("api"):
            raise RuntimeError(f'Provider {provider}: "api" is required when registering streamSimple.')
        models = config.get("models")
        if not isinstance(models, list) or not models:
            return
        if not config.get("baseUrl"):
            raise RuntimeError(f'Provider {provider}: "baseUrl" is required when defining models.')
        if not config.get("apiKey") and not isinstance(config.get("oauth"), dict):
            raise RuntimeError(f'Provider {provider}: "apiKey" or "oauth" is required when defining models.')
        for model in models:
            if isinstance(model, dict) and not (model.get("api") or config.get("api")):
                raise RuntimeError(f'Provider {provider}, model {model.get("id")}: no "api" specified.')

    def _apply_provider_config(self, provider: str, config: dict[str, Any]) -> None:
        stream_simple = config.get("streamSimple") or config.get("stream_simple")
        if callable(stream_simple):
            api = str(config.get("api") or provider)
            self.api_providers.unregister_source(f"provider:{provider}")
            self.api_providers.register(
                ApiProvider(api=api, stream=stream_simple, stream_simple=stream_simple),
                f"provider:{provider}",
            )
        self._store_provider_request_config(provider, config)
        if isinstance(config.get("models"), list) and config["models"]:
            self._models = [model for model in self._models if model.provider != provider]
            for raw_model in config["models"]:
                if isinstance(raw_model, dict):
                    self._models.append(self._model_from_config(provider, config, raw_model, set(ai_models.get_providers())))
            return
        if config.get("baseUrl") or config.get("headers"):
            self._models = [
                replace(model, base_url=str(config.get("baseUrl") or model.base_url))
                if model.provider == provider
                else model
                for model in self._models
            ]


def _cost_from_config(value: object) -> Cost:
    if not isinstance(value, dict):
        return Cost()
    return Cost(
        input=float(value.get("input", 0)),
        output=float(value.get("output", 0)),
        cache_read=float(value.get("cacheRead", value.get("cache_read", 0))),
        cache_write=float(value.get("cacheWrite", value.get("cache_write", 0))),
    )


def _string_list(value: object, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    return [str(item) for item in value]


def _dict_or_none(value: object) -> dict[str, str | None] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): None if item is None else str(item) for key, item in value.items()}


def _strip_json_comments(text: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        nxt = text[index + 1 : index + 2]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and nxt == "/":
            index = text.find("\n", index)
            if index == -1:
                break
            out.append("\n")
            index += 1
            continue
        if char == "/" and nxt == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                break
            index = end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)
