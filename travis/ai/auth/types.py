"""Authentication types owned by the Travis provider runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

from travis.ai.types import Model


@dataclass(frozen=True)
class ModelAuth:
    api_key: str | None = None
    headers: Mapping[str, str] | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class AuthResult:
    auth: ModelAuth
    source: str | None = None
    env: Mapping[str, str] | None = None


ApiKeyCredential = dict[str, object]
OAuthCredential = dict[str, object]
Credential = dict[str, object]


class AuthContext(Protocol):
    def env(self, name: str) -> str | None: ...

    def file_exists(self, path: str) -> bool: ...


class CredentialStore(Protocol):
    def read(self, provider_id: str) -> Credential | None: ...

    def modify(
        self,
        provider_id: str,
        callback: Callable[[Credential | None], Credential | None],
    ) -> Credential | None: ...

    def delete(self, provider_id: str) -> None: ...


ApiKeyResolver = Callable[[Model, AuthContext, ApiKeyCredential | None], AuthResult | None]
LoginHandler = Callable[[dict[str, object]], Credential]
RefreshHandler = Callable[[OAuthCredential], OAuthCredential]
ToAuthHandler = Callable[[OAuthCredential], ModelAuth]


@dataclass(frozen=True)
class ApiKeyAuth:
    name: str
    resolve: ApiKeyResolver
    login: LoginHandler | None = None


@dataclass(frozen=True)
class OAuthAuth:
    name: str
    login: LoginHandler
    refresh: RefreshHandler
    to_auth: ToAuthHandler


@dataclass(frozen=True)
class ProviderAuth:
    api_key: ApiKeyAuth | None = None
    oauth: OAuthAuth | None = None

    def __post_init__(self) -> None:
        if self.api_key is None and self.oauth is None:
            raise ValueError("provider auth requires api-key or OAuth behavior")


__all__ = [
    "ApiKeyAuth",
    "ApiKeyCredential",
    "AuthContext",
    "AuthResult",
    "Credential",
    "CredentialStore",
    "ModelAuth",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
]
