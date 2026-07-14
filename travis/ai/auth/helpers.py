"""Reusable provider authentication behavior."""

from __future__ import annotations

from collections.abc import Sequence

from travis.ai.auth.types import ApiKeyAuth, AuthResult, ModelAuth, OAuthAuth


def env_api_key_auth(name: str, env_vars: Sequence[str]) -> ApiKeyAuth:
    def resolve(model, context, credential):
        del model
        if credential and credential.get("key"):
            return AuthResult(
                auth=ModelAuth(api_key=str(credential["key"])),
                env=_credential_env(credential),
                source="stored credential",
            )
        for env_var in env_vars:
            value = context.env(env_var)
            if value:
                return AuthResult(auth=ModelAuth(api_key=value), source=env_var)
        return None

    return ApiKeyAuth(name=name, resolve=resolve)


def _credential_env(credential: dict[str, object]) -> dict[str, str] | None:
    value = credential.get("env")
    if not isinstance(value, dict):
        return None
    resolved = {str(key): str(item) for key, item in value.items()}
    return resolved or None


def oauth_auth_from_mapping(config: dict[str, object]) -> OAuthAuth:
    login = config.get("login")
    refresh = config.get("refreshToken", config.get("refresh_token"))
    to_auth = config.get("toAuth", config.get("to_auth"))
    get_api_key = config.get("getApiKey", config.get("get_api_key"))
    if not callable(login) or not callable(refresh):
        raise RuntimeError("OAuth provider requires login and refresh callbacks")
    if not callable(to_auth):
        if not callable(get_api_key):
            raise RuntimeError("OAuth provider requires a request-auth callback")

        def to_auth(credential):
            return ModelAuth(api_key=str(get_api_key(credential)))

    return OAuthAuth(
        name=str(config.get("name") or "OAuth"),
        login=login,
        refresh=refresh,
        to_auth=to_auth,
    )


__all__ = ["env_api_key_auth", "oauth_auth_from_mapping"]
