"""Provider authentication contracts and resolution."""

from travis.ai.auth.context import AuthContext, default_auth_context
from travis.ai.auth.credential_store import CredentialStore, InMemoryCredentialStore
from travis.ai.auth.helpers import env_api_key_auth, oauth_auth_from_mapping
from travis.ai.auth.resolve import ModelsError, resolve_provider_auth
from travis.ai.auth.types import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthResult,
    Credential,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)

__all__ = [
    "ApiKeyAuth",
    "ApiKeyCredential",
    "AuthContext",
    "AuthResult",
    "Credential",
    "CredentialStore",
    "InMemoryCredentialStore",
    "ModelAuth",
    "ModelsError",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
    "default_auth_context",
    "env_api_key_auth",
    "oauth_auth_from_mapping",
    "resolve_provider_auth",
]
