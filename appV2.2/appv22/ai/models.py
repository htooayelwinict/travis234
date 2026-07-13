"""Model registry + cost. Port of pi/packages/ai/src/models.ts (minimal subset)."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import subprocess
import time

from appv22.ai.env_config import find_env_keys, get_env_api_key
from appv22.ai.types import Cost, Model

_MODELS: dict[str, dict[str, Model]] = {}
_REGISTERED_PROVIDER_CONFIGS: dict[str, dict[str, object]] = {}
_PROVIDER_REQUEST_CONFIGS: dict[str, dict[str, object]] = {}
_MODEL_REQUEST_HEADERS: dict[str, dict[str, str]] = {}
_OAUTH_PROVIDERS: dict[str, dict[str, object]] = {}
_AUTH_CREDENTIALS: dict[str, dict[str, object]] = {}
_RUNTIME_API_KEYS: dict[str, str] = {}
_AUTH_ERRORS: list[Exception] = []
_CONFIG_VALUE_COMMAND_CACHE: dict[str, str | None] = {}
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_VAR_NAME_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")
_EXTENDED_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")
_BUILT_IN_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "amazon-bedrock": "Amazon Bedrock",
    "ant-ling": "Ant Ling",
    "azure-openai-responses": "Azure OpenAI Responses",
    "cerebras": "Cerebras",
    "cloudflare-ai-gateway": "Cloudflare AI Gateway",
    "cloudflare-workers-ai": "Cloudflare Workers AI",
    "deepseek": "DeepSeek",
    "fireworks": "Fireworks",
    "google": "Google Gemini",
    "google-vertex": "Google Vertex AI",
    "groq": "Groq",
    "huggingface": "Hugging Face",
    "kimi-coding": "Kimi For Coding",
    "mistral": "Mistral",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax (China)",
    "moonshotai": "Moonshot AI",
    "moonshotai-cn": "Moonshot AI (China)",
    "nvidia": "NVIDIA NIM",
    "opencode": "OpenCode Zen",
    "opencode-go": "OpenCode Go",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "together": "Together AI",
    "vercel-ai-gateway": "Vercel AI Gateway",
    "xai": "xAI",
    "zai": "ZAI",
    "zai-coding-cn": "ZAI Coding Plan (China)",
    "xiaomi": "Xiaomi MiMo",
    "xiaomi-token-plan-cn": "Xiaomi MiMo Token Plan (China)",
    "xiaomi-token-plan-ams": "Xiaomi MiMo Token Plan (Amsterdam)",
    "xiaomi-token-plan-sgp": "Xiaomi MiMo Token Plan (Singapore)",
}
_BUILT_IN_OAUTH_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "github-copilot": "GitHub Copilot",
}


def register_model(model: Model) -> None:
    _MODELS.setdefault(model.provider, {})[model.id] = model


def unregister_provider_models(provider: str) -> None:
    _MODELS.pop(provider, None)


def set_provider_models(provider: str, models: list[Model]) -> None:
    if models:
        _MODELS[provider] = {model.id: model for model in models}
    else:
        _MODELS.pop(provider, None)


def get_model(provider: str, model_id: str) -> Model | None:
    return _MODELS.get(provider, {}).get(model_id)


def get_models(provider: str) -> list[Model]:
    return list(_MODELS.get(provider, {}).values())


def get_providers() -> list[str]:
    return list(_MODELS.keys())


def reset_models() -> None:
    _MODELS.clear()
    _REGISTERED_PROVIDER_CONFIGS.clear()
    _PROVIDER_REQUEST_CONFIGS.clear()
    _MODEL_REQUEST_HEADERS.clear()
    _OAUTH_PROVIDERS.clear()
    _AUTH_CREDENTIALS.clear()
    _RUNTIME_API_KEYS.clear()
    _AUTH_ERRORS.clear()
    _CONFIG_VALUE_COMMAND_CACHE.clear()


def register_provider_auth_config(provider: str, config: dict[str, object]) -> None:
    _REGISTERED_PROVIDER_CONFIGS[provider] = dict(config)
    api_key = config.get("apiKey", config.get("api_key"))
    headers = config.get("headers")
    auth_header = config.get("authHeader", config.get("auth_header"))
    if api_key is not None or headers is not None or auth_header is not None:
        _PROVIDER_REQUEST_CONFIGS[provider] = {
            "apiKey": api_key,
            "headers": headers,
            "authHeader": auth_header,
        }
    oauth = config.get("oauth")
    if isinstance(oauth, dict):
        provider_info = dict(oauth)
        provider_info["id"] = provider
        _OAUTH_PROVIDERS[provider] = provider_info


def unregister_provider_auth_config(provider: str) -> None:
    _REGISTERED_PROVIDER_CONFIGS.pop(provider, None)
    _PROVIDER_REQUEST_CONFIGS.pop(provider, None)
    for key in list(_MODEL_REQUEST_HEADERS):
        if key.startswith(f"{provider}:"):
            _MODEL_REQUEST_HEADERS.pop(key, None)
    _OAUTH_PROVIDERS.pop(provider, None)


def register_model_request_headers(provider: str, model_id: str, headers: object | None) -> None:
    key = _model_request_key(provider, model_id)
    if not isinstance(headers, dict) or not headers:
        _MODEL_REQUEST_HEADERS.pop(key, None)
        return
    _MODEL_REQUEST_HEADERS[key] = {str(header): str(value) for header, value in headers.items()}


def set_runtime_api_key(provider: str, api_key: str) -> None:
    _RUNTIME_API_KEYS[provider] = api_key


def remove_runtime_api_key(provider: str) -> None:
    _RUNTIME_API_KEYS.pop(provider, None)


def set_auth_credential(provider: str, credential: dict[str, object]) -> None:
    _AUTH_CREDENTIALS[provider] = dict(credential)


def remove_auth_credential(provider: str) -> None:
    _AUTH_CREDENTIALS.pop(provider, None)


def get_auth_credential(provider: str) -> dict[str, object] | None:
    credential = _AUTH_CREDENTIALS.get(provider)
    return dict(credential) if credential is not None else None


def list_auth_providers() -> list[str]:
    return list(_AUTH_CREDENTIALS.keys())


def login_oauth_provider(provider: str, callbacks: dict[str, object]) -> None:
    oauth_provider = _OAUTH_PROVIDERS.get(provider)
    if oauth_provider is None:
        raise RuntimeError(f"Unknown OAuth provider: {provider}")
    login = oauth_provider.get("login")
    if not callable(login):
        raise RuntimeError(f"OAuth provider {provider} does not support login")
    credentials = _settle_oauth_result(login(callbacks))
    if not isinstance(credentials, dict):
        raise RuntimeError(f"OAuth provider {provider} returned invalid credentials")
    set_auth_credential(provider, {"type": "oauth", **credentials})


def logout_provider(provider: str) -> None:
    remove_auth_credential(provider)


def drain_auth_errors() -> list[Exception]:
    errors = list(_AUTH_ERRORS)
    _AUTH_ERRORS.clear()
    return errors


def has_auth(provider: str) -> bool:
    if provider in _RUNTIME_API_KEYS:
        return True
    if provider in _AUTH_CREDENTIALS:
        return True
    if _env_api_key(provider):
        return True
    return False


def has_configured_auth(model: Model) -> bool:
    provider_api_key = _PROVIDER_REQUEST_CONFIGS.get(model.provider, {}).get("apiKey")
    return has_auth(model.provider) or (
        isinstance(provider_api_key, str) and _is_config_value_configured(provider_api_key)
    )


def get_provider_auth_status(provider: str) -> dict[str, object]:
    if provider in _AUTH_CREDENTIALS:
        return {"configured": True, "source": "stored"}
    if provider in _RUNTIME_API_KEYS:
        return {"configured": False, "source": "runtime", "label": "--api-key"}

    provider_api_key = _PROVIDER_REQUEST_CONFIGS.get(provider, {}).get("apiKey")
    if isinstance(provider_api_key, str):
        if provider_api_key.startswith("!"):
            return {"configured": True, "source": "models_json_command"}
        env_var_names = _config_value_env_var_names(provider_api_key)
        if env_var_names:
            if _is_config_value_configured(provider_api_key):
                return {"configured": True, "source": "environment", "label": ", ".join(env_var_names)}
            return {"configured": False}
        return {"configured": True, "source": "models_json_key"}

    env_key = _env_api_key_name(provider)
    if env_key:
        return {"configured": False, "source": "environment", "label": env_key}
    return {"configured": False}


def get_provider_display_name(provider: str) -> str:
    registered_provider = _REGISTERED_PROVIDER_CONFIGS.get(provider, {})
    registered_name = registered_provider.get("name")
    if registered_name:
        return str(registered_name)

    registered_oauth = registered_provider.get("oauth")
    if isinstance(registered_oauth, dict) and registered_oauth.get("name"):
        return str(registered_oauth["name"])

    oauth_provider = _OAUTH_PROVIDERS.get(provider)
    if isinstance(oauth_provider, dict) and oauth_provider.get("name"):
        return str(oauth_provider["name"])

    return (
        _BUILT_IN_PROVIDER_DISPLAY_NAMES.get(provider)
        or _BUILT_IN_OAUTH_PROVIDER_DISPLAY_NAMES.get(provider)
        or provider
    )


def get_api_key_for_provider(provider: str) -> str | None:
    if provider in _RUNTIME_API_KEYS:
        return _RUNTIME_API_KEYS[provider]
    credential = _AUTH_CREDENTIALS.get(provider)
    if credential:
        if credential.get("type") == "api_key":
            return _resolve_config_value(str(credential.get("key", "")))
        if credential.get("type") == "oauth":
            return _get_oauth_api_key(provider, credential)
    env_key = _env_api_key(provider)
    if env_key:
        return env_key
    provider_api_key = _PROVIDER_REQUEST_CONFIGS.get(provider, {}).get("apiKey")
    if isinstance(provider_api_key, str):
        return _resolve_config_value(provider_api_key)
    return None


def get_api_key_and_headers(model: Model) -> dict[str, object]:
    try:
        provider_config = _PROVIDER_REQUEST_CONFIGS.get(model.provider, {})
        api_key = _get_stored_or_env_api_key(model.provider)
        provider_api_key = provider_config.get("apiKey")
        if api_key is None and isinstance(provider_api_key, str):
            api_key = _resolve_config_value_or_throw(
                provider_api_key,
                f'API key for provider "{model.provider}"',
            )

        model_headers = _resolve_headers_or_throw(model.headers, f'model "{model.provider}/{model.id}"')
        provider_headers = _resolve_headers_or_throw(provider_config.get("headers"), f'provider "{model.provider}"')
        request_headers = _resolve_headers_or_throw(
            _MODEL_REQUEST_HEADERS.get(_model_request_key(model.provider, model.id)),
            f'model "{model.provider}/{model.id}"',
        )

        headers: dict[str, str] = {}
        if model_headers:
            headers.update(model_headers)
        if provider_headers:
            headers.update(provider_headers)
        if request_headers:
            headers.update(request_headers)

        if provider_config.get("authHeader"):
            if not api_key:
                return {"ok": False, "error": f'No API key found for "{model.provider}"'}
            headers["Authorization"] = f"Bearer {api_key}"

        return {
            "ok": True,
            "apiKey": api_key,
            "headers": headers or None,
        }
    except Exception as error:  # noqa: BLE001 - Pi returns auth resolution errors as values.
        return {"ok": False, "error": str(error)}


def get_oauth_providers() -> list[dict[str, object]]:
    providers = []
    for provider, config in _OAUTH_PROVIDERS.items():
        entry = {"id": provider, "name": str(config.get("name") or provider)}
        providers.append(entry)
    return providers


def _get_oauth_api_key(provider: str, credential: dict[str, object]) -> str | None:
    oauth_provider = _OAUTH_PROVIDERS.get(provider)
    if oauth_provider is None:
        return None

    current_credential = credential
    if _oauth_is_expired(current_credential):
        refresh_token = _oauth_callable(oauth_provider, "refreshToken", "refresh_token")
        if not callable(refresh_token):
            return None
        try:
            refreshed = _settle_oauth_result(refresh_token(current_credential))
        except Exception as error:  # noqa: BLE001 - Pi records auth errors and skips provider auth
            _AUTH_ERRORS.append(error if isinstance(error, Exception) else RuntimeError(str(error)))
            return None
        if not isinstance(refreshed, dict):
            _AUTH_ERRORS.append(RuntimeError(f"OAuth provider {provider} returned invalid refreshed credentials"))
            return None
        current_credential = {"type": "oauth", **refreshed}
        _AUTH_CREDENTIALS[provider] = current_credential

    get_api_key = _oauth_callable(oauth_provider, "getApiKey", "get_api_key")
    if callable(get_api_key):
        return str(get_api_key(current_credential))
    access = current_credential.get("access")
    return str(access) if access is not None else None


def _oauth_is_expired(credential: dict[str, object]) -> bool:
    expires = credential.get("expires")
    if expires is None:
        return False
    try:
        return int(expires) <= int(time.time() * 1000)
    except (TypeError, ValueError):
        return False


def _oauth_callable(config: dict[str, object], camel_name: str, snake_name: str):
    callback = config.get(camel_name)
    if not callable(callback):
        callback = config.get(snake_name)
    return callback


def _settle_oauth_result(result: object) -> object:
    if not inspect.isawaitable(result):
        return result
    return asyncio.run(result)


def calculate_cost(model: Model, usage_tokens: dict[str, int]) -> Cost:
    """Cost from per-million-token pricing on the model."""

    def per_million(tokens: int, rate: float) -> float:
        return (tokens / 1_000_000.0) * rate

    cost = Cost(
        input=per_million(usage_tokens.get("input", 0), model.cost.input),
        output=per_million(usage_tokens.get("output", 0), model.cost.output),
        cache_read=per_million(usage_tokens.get("cache_read", 0), model.cost.cache_read),
        cache_write=per_million(usage_tokens.get("cache_write", 0), model.cost.cache_write),
    )
    cost.total = cost.input + cost.output + cost.cache_read + cost.cache_write
    return cost


def get_supported_thinking_levels(model: Model) -> list[str]:
    if not model.reasoning:
        return ["off"]

    thinking_level_map = model.thinking_level_map or {}
    levels: list[str] = []
    for level in _EXTENDED_THINKING_LEVELS:
        if level in thinking_level_map and thinking_level_map[level] is None:
            continue
        if level == "xhigh" and level not in thinking_level_map:
            continue
        levels.append(level)
    return levels


def clamp_thinking_level(model: Model, level: str) -> str:
    available_levels = get_supported_thinking_levels(model)
    if level in available_levels:
        return level

    try:
        requested_index = _EXTENDED_THINKING_LEVELS.index(level)
    except ValueError:
        return available_levels[0] if available_levels else "off"

    for candidate in _EXTENDED_THINKING_LEVELS[requested_index:]:
        if candidate in available_levels:
            return candidate
    for candidate in reversed(_EXTENDED_THINKING_LEVELS[:requested_index]):
        if candidate in available_levels:
            return candidate
    return available_levels[0] if available_levels else "off"


def models_are_equal(left: Model | None, right: Model | None) -> bool:
    return bool(left and right and left.id == right.id and left.provider == right.provider)


registerModel = register_model
unregisterProviderModels = unregister_provider_models
setProviderModels = set_provider_models
getModel = get_model
getModels = get_models
getProviders = get_providers
resetModels = reset_models
registerProviderAuthConfig = register_provider_auth_config
unregisterProviderAuthConfig = unregister_provider_auth_config
registerModelRequestHeaders = register_model_request_headers
setRuntimeApiKey = set_runtime_api_key
removeRuntimeApiKey = remove_runtime_api_key
setAuthCredential = set_auth_credential
removeAuthCredential = remove_auth_credential
getAuthCredential = get_auth_credential
listAuthProviders = list_auth_providers
loginOAuthProvider = login_oauth_provider
logoutProvider = logout_provider
drainAuthErrors = drain_auth_errors
hasAuth = has_auth
hasConfiguredAuth = has_configured_auth
getProviderAuthStatus = get_provider_auth_status
getProviderDisplayName = get_provider_display_name
getApiKeyForProvider = get_api_key_for_provider
getApiKeyAndHeaders = get_api_key_and_headers
getOAuthProviders = get_oauth_providers
calculateCost = calculate_cost
getSupportedThinkingLevels = get_supported_thinking_levels
clampThinkingLevel = clamp_thinking_level
modelsAreEqual = models_are_equal


def _env_api_key(provider: str) -> str | None:
    return get_env_api_key(provider)


def _env_api_key_name(provider: str) -> str | None:
    keys = find_env_keys(provider)
    return keys[0] if keys else None


def _model_request_key(provider: str, model_id: str) -> str:
    return f"{provider}:{model_id}"


def _get_stored_or_env_api_key(provider: str) -> str | None:
    if provider in _RUNTIME_API_KEYS:
        return _RUNTIME_API_KEYS[provider]
    credential = _AUTH_CREDENTIALS.get(provider)
    if credential:
        if credential.get("type") == "api_key":
            return _resolve_config_value(str(credential.get("key", "")), uncached=True)
        if credential.get("type") == "oauth":
            return _get_oauth_api_key(provider, credential)
    env_key = _env_api_key(provider)
    if env_key:
        return env_key
    return None


def _config_value_env_var_names(value: str) -> list[str]:
    if value.startswith("!"):
        return []
    names: list[str] = []
    for kind, name in _parse_config_value_template(value):
        if kind == "env" and name not in names:
            names.append(name)
    return names


def _missing_config_value_env_var_names(value: str) -> list[str]:
    return [name for name in _config_value_env_var_names(value) if not os.environ.get(name)]


def _is_config_value_configured(value: str) -> bool:
    return not _missing_config_value_env_var_names(value)


def _resolve_config_value(value: str, *, uncached: bool = False) -> str | None:
    if value.startswith("!"):
        return _execute_command_config_value(value, uncached=uncached)
    resolved = ""
    for kind, part in _parse_config_value_template(value):
        if kind == "literal":
            resolved += part
            continue
        env_value = os.environ.get(part)
        if not env_value:
            return None
        resolved += env_value
    return resolved


def _resolve_config_value_or_throw(value: str, description: str) -> str:
    resolved = _resolve_config_value(value, uncached=True)
    if resolved is not None:
        return resolved

    if value.startswith("!"):
        raise RuntimeError(f"Failed to resolve {description} from shell command: {value[1:]}")

    missing_env_vars = _missing_config_value_env_var_names(value)
    if len(missing_env_vars) == 1:
        raise RuntimeError(f"Failed to resolve {description} from environment variable: {missing_env_vars[0]}")
    if len(missing_env_vars) > 1:
        raise RuntimeError(
            f"Failed to resolve {description} from environment variables: {', '.join(missing_env_vars)}"
        )
    raise RuntimeError(f"Failed to resolve {description}")


def _resolve_headers_or_throw(headers: object, description: str) -> dict[str, str] | None:
    if not isinstance(headers, dict):
        return None
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved[str(key)] = _resolve_config_value_or_throw(str(value), f'{description} header "{key}"')
    return resolved or None


def _execute_command_config_value(value: str, *, uncached: bool = False) -> str | None:
    if not uncached and value in _CONFIG_VALUE_COMMAND_CACHE:
        return _CONFIG_VALUE_COMMAND_CACHE[value]
    result = _execute_command_config_value_uncached(value)
    if not uncached:
        _CONFIG_VALUE_COMMAND_CACHE[value] = result
    return result


def _execute_command_config_value_uncached(value: str) -> str | None:
    try:
        completed = subprocess.run(
            value[1:],
            shell=True,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip()
    return output or None


def _parse_config_value_template(value: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    index = 0

    while index < len(value):
        dollar_index = value.find("$", index)
        if dollar_index < 0:
            _append_literal_part(parts, value[index:])
            break

        _append_literal_part(parts, value[index:dollar_index])
        next_index = dollar_index + 1
        next_char = value[next_index : next_index + 1]

        if next_char in {"$", "!"}:
            _append_literal_part(parts, next_char)
            index = dollar_index + 2
            continue

        if next_char == "{":
            end_index = value.find("}", dollar_index + 2)
            if end_index < 0:
                _append_literal_part(parts, "$")
                index = dollar_index + 1
                continue
            name = value[dollar_index + 2 : end_index]
            if _ENV_VAR_NAME_RE.fullmatch(name):
                parts.append(("env", name))
            else:
                _append_literal_part(parts, value[dollar_index : end_index + 1])
            index = end_index + 1
            continue

        match = _ENV_VAR_NAME_PREFIX_RE.match(value[next_index:])
        if match:
            name = match.group(0)
            parts.append(("env", name))
            index = next_index + len(name)
            continue

        _append_literal_part(parts, "$")
        index = dollar_index + 1

    return parts


def _append_literal_part(parts: list[tuple[str, str]], value: str) -> None:
    if not value:
        return
    if parts and parts[-1][0] == "literal":
        parts[-1] = ("literal", parts[-1][1] + value)
        return
    parts.append(("literal", value))
