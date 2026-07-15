"""Resolve request authentication for one provider-owned model."""

from __future__ import annotations

import inspect
import time
from dataclasses import replace
from typing import Any

from travis.agent.async_utils import run_sync
from travis.ai.auth.types import (
    AuthContext,
    AuthResult,
    Credential,
    CredentialStore,
    ModelAuth,
    ProviderAuth,
)
from travis.ai.types import Model


class ModelsError(RuntimeError):
    def __init__(self, code: str, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause


def resolve_provider_auth(
    provider_id: str,
    provider_auth: ProviderAuth,
    model: Model,
    credentials: CredentialStore,
    context: AuthContext,
    *,
    api_key: str | None = None,
    env: dict[str, str] | None = None,
    offline: bool = False,
) -> AuthResult | None:
    request_context = _OverlayAuthContext(context, env or {})
    if api_key is not None and provider_auth.api_key is not None:
        return _resolve_api_key(
            provider_id,
            provider_auth,
            model,
            request_context,
            {"type": "api_key", "key": api_key, "env": env or {}},
        )

    try:
        stored = credentials.read(provider_id)
    except Exception as error:  # noqa: BLE001 - normalized at the provider boundary.
        raise ModelsError("auth", f"Credential store read failed for {provider_id}", cause=error) from error

    if stored is not None:
        credential_type = stored.get("type")
        if credential_type == "oauth" and provider_auth.oauth is not None:
            return _resolve_oauth(
                provider_id,
                provider_auth,
                credentials,
                stored,
                offline=offline,
            )
        if credential_type == "api_key" and provider_auth.api_key is not None:
            credential = dict(stored)
            if env:
                credential["env"] = {**_mapping(stored.get("env")), **env}
            return _resolve_api_key(
                provider_id,
                provider_auth,
                model,
                request_context,
                credential,
            )
        # A stored credential owns the provider. Never silently fall back to
        # ambient auth when its type has no matching provider behavior.
        return None

    if provider_auth.api_key is None:
        return None
    return _resolve_api_key(provider_id, provider_auth, model, request_context, None)


def _resolve_oauth(
    provider_id: str,
    provider_auth: ProviderAuth,
    credentials: CredentialStore,
    stored: Credential,
    *,
    offline: bool = False,
) -> AuthResult | None:
    oauth = provider_auth.oauth
    assert oauth is not None
    credential = dict(stored)
    expires = _expiry_ms(credential)
    if expires is not None and int(time.time() * 1000) >= expires:
        if offline:
            raise ModelsError(
                "offline",
                f"OAuth credentials for {provider_id} are expired and cannot refresh in offline mode",
            )
        try:
            committed = credentials.modify(
                provider_id,
                lambda current: _refresh_if_expired(oauth, current),
            )
        except ModelsError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ModelsError("auth", f"Credential store modify failed for {provider_id}", cause=error) from error
        if committed is None or committed.get("type") != "oauth":
            return None
        credential = committed
    try:
        model_auth = _settle(oauth.to_auth(dict(credential)))
    except Exception as error:  # noqa: BLE001
        raise ModelsError("oauth", f"OAuth auth derivation failed for {provider_id}", cause=error) from error
    if not isinstance(model_auth, ModelAuth):
        model_auth = _model_auth_from_mapping(model_auth)
    return AuthResult(auth=model_auth, source="OAuth")


def _refresh_if_expired(oauth, current: Credential | None) -> Credential | None:
    if current is None or current.get("type") != "oauth":
        return None
    expires = _expiry_ms(current)
    if expires is None or int(time.time() * 1000) < expires:
        return None
    try:
        refreshed = _settle(oauth.refresh(dict(current)))
    except Exception as error:  # noqa: BLE001
        raise ModelsError("oauth", "OAuth refresh failed", cause=error) from error
    if not isinstance(refreshed, dict):
        raise ModelsError("oauth", "OAuth refresh returned invalid credentials")
    return {"type": "oauth", **refreshed}


def _resolve_api_key(provider_id, provider_auth, model, context, credential):
    api_key_auth = provider_auth.api_key
    assert api_key_auth is not None
    try:
        result = _settle(api_key_auth.resolve(model, context, credential))
    except Exception as error:  # noqa: BLE001
        raise ModelsError("auth", f"API key auth failed for provider {provider_id}", cause=error) from error
    if result is None or isinstance(result, AuthResult):
        return result
    if isinstance(result, dict):
        auth_value = result.get("auth")
        auth = auth_value if isinstance(auth_value, ModelAuth) else _model_auth_from_mapping(auth_value)
        return AuthResult(auth=auth, source=_optional_str(result.get("source")), env=_mapping(result.get("env")) or None)
    raise ModelsError("auth", f"API key auth returned invalid data for provider {provider_id}")


class _OverlayAuthContext:
    def __init__(self, base: AuthContext, env: dict[str, str]) -> None:
        self._base = base
        self._env = env

    def env(self, name: str) -> str | None:
        return self._env.get(name) or self._base.env(name)

    def file_exists(self, path: str) -> bool:
        return self._base.file_exists(path)


def _expiry_ms(credential: Credential) -> int | None:
    value = credential.get("expires", credential.get("expires_at"))
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is not None and parsed < 10_000_000_000:
        return parsed * 1000
    return parsed


def _model_auth_from_mapping(value: object) -> ModelAuth:
    if not isinstance(value, dict):
        raise ModelsError("auth", "Provider auth returned invalid request data")
    return ModelAuth(
        api_key=_optional_str(value.get("apiKey", value.get("api_key"))),
        headers=_mapping(value.get("headers")) or None,
        base_url=_optional_str(value.get("baseUrl", value.get("base_url"))),
    )


def _mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _settle(value: Any) -> Any:
    return run_sync(value) if inspect.isawaitable(value) else value


__all__ = ["ModelsError", "resolve_provider_auth"]
