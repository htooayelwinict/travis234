"""Fresh built-in provider instances for an isolated Travis runtime."""

from __future__ import annotations

from collections.abc import Mapping

from travis.ai.auth import (
    ApiKeyAuth,
    AuthResult,
    CredentialStore,
    ModelAuth,
    OAuthAuth,
    ProviderAuth,
    env_api_key_auth,
    oauth_auth_from_mapping,
)
from travis.ai.builtin_models import load_builtin_models_by_provider
from travis.ai.env_config import ModelConfig, load_model_config
from travis.ai.models import Models, Provider, ProviderStreams, create_models
from travis.ai.provider_metadata import PROVIDER_METADATA, ProviderMetadata
from travis.ai.providers.github_copilot_oauth import github_copilot_oauth_config
from travis.ai.providers.subscription_oauth import anthropic_oauth_config, openai_codex_oauth_config
from travis.ai.providers.travis_env import TravisProvider


def builtin_providers(*, config: ModelConfig | None = None) -> tuple[Provider, ...]:
    runtime_config = config or load_model_config("TRAVIS234_WORKER_LLM")
    streams = default_provider_streams(config=runtime_config)
    models_by_provider = load_builtin_models_by_provider()
    return tuple(
        Provider(
            id=metadata.id,
            name=metadata.name,
            base_url=metadata.base_url or None,
            auth=_provider_auth(metadata),
            models=models_by_provider.get(metadata.id, ()),
            api=streams,
        )
        for metadata in PROVIDER_METADATA
    )


def builtin_models(
    *,
    credentials: CredentialStore | None = None,
    auth_context=None,
    config: ModelConfig | None = None,
) -> Models:
    models = create_models(credentials=credentials, auth_context=auth_context)
    for provider in builtin_providers(config=config):
        models.set_provider(provider)
    return models


def default_provider_streams(*, config: ModelConfig | None = None) -> ProviderStreams:
    api_runtime = TravisProvider(config or load_model_config("TRAVIS234_WORKER_LLM"))
    return ProviderStreams(
        stream=api_runtime.stream,
        stream_simple=api_runtime.stream_simple,
    )


def _provider_auth(metadata: ProviderMetadata) -> ProviderAuth:
    if metadata.id == "amazon-bedrock":
        return ProviderAuth(api_key=_bedrock_auth())
    if metadata.id == "google-vertex":
        return ProviderAuth(api_key=_vertex_auth())
    if metadata.id in {"cloudflare-workers-ai", "cloudflare-ai-gateway"}:
        return ProviderAuth(api_key=_cloudflare_auth(metadata.id))

    oauth_config = _oauth_config(metadata.id)
    oauth = oauth_auth_from_mapping(oauth_config) if oauth_config is not None else None
    api_key = None
    if metadata.id != "openai-codex":
        api_key = (
            _optional_auth(metadata.name)
            if metadata.auth_type == "optional"
            else env_api_key_auth(f"{metadata.name} API key", metadata.api_key_env_vars)
        )
    return ProviderAuth(api_key=api_key, oauth=oauth)


def _oauth_config(provider_id: str) -> dict[str, object] | None:
    if provider_id == "anthropic":
        return anthropic_oauth_config()
    if provider_id == "openai-codex":
        return openai_codex_oauth_config()
    if provider_id == "github-copilot":
        return github_copilot_oauth_config()
    return None


def _bedrock_auth() -> ApiKeyAuth:
    def resolve(model, context, credential):
        del model
        if credential and credential.get("key"):
            return AuthResult(auth=ModelAuth(api_key=str(credential["key"])), source="stored credential")
        ambient = (
            ("AWS_BEARER_TOKEN_BEDROCK",),
            ("AWS_PROFILE",),
            ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
            ("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",),
            ("AWS_CONTAINER_CREDENTIALS_FULL_URI",),
            ("AWS_WEB_IDENTITY_TOKEN_FILE",),
        )
        for names in ambient:
            if all(context.env(name) for name in names):
                return AuthResult(auth=ModelAuth(), source=" + ".join(names))
        return None

    return ApiKeyAuth(name="Bedrock API key or AWS credentials", resolve=resolve)


def _vertex_auth() -> ApiKeyAuth:
    def resolve(model, context, credential):
        del model
        key = str(credential.get("key")) if credential and credential.get("key") else context.env("GOOGLE_CLOUD_API_KEY")
        if key:
            return AuthResult(auth=ModelAuth(api_key=key), source="stored credential" if credential else "GOOGLE_CLOUD_API_KEY")
        adc_path = context.env("GOOGLE_APPLICATION_CREDENTIALS") or "~/.config/gcloud/application_default_credentials.json"
        project = context.env("GOOGLE_CLOUD_PROJECT") or context.env("GCLOUD_PROJECT")
        location = context.env("GOOGLE_CLOUD_LOCATION")
        if context.file_exists(adc_path) and project and location:
            return AuthResult(auth=ModelAuth(), source="gcloud application default credentials")
        return None

    return ApiKeyAuth(name="Google Cloud credentials", resolve=resolve)


def _cloudflare_auth(provider_id: str) -> ApiKeyAuth:
    gateway = provider_id == "cloudflare-ai-gateway"

    def resolve(model, context, credential):
        credential_env = credential.get("env") if credential and isinstance(credential.get("env"), dict) else {}
        key = str(credential.get("key")) if credential and credential.get("key") else context.env("CLOUDFLARE_API_KEY")
        account = str(credential_env.get("CLOUDFLARE_ACCOUNT_ID") or context.env("CLOUDFLARE_ACCOUNT_ID") or "")
        gateway_id = str(credential_env.get("CLOUDFLARE_GATEWAY_ID") or context.env("CLOUDFLARE_GATEWAY_ID") or "")
        if not key or not account or (gateway and not gateway_id):
            return None
        env = {"CLOUDFLARE_ACCOUNT_ID": account}
        if gateway_id:
            env["CLOUDFLARE_GATEWAY_ID"] = gateway_id
        base_url = model.base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account).replace(
            "{CLOUDFLARE_GATEWAY_ID}", gateway_id
        )
        auth = (
            ModelAuth(headers={"cf-aig-authorization": f"Bearer {key}"}, base_url=base_url)
            if gateway
            else ModelAuth(api_key=key, base_url=base_url)
        )
        return AuthResult(auth=auth, env=env, source="stored credential" if credential else "CLOUDFLARE_API_KEY")

    return ApiKeyAuth(name="Cloudflare API key", resolve=resolve)


def _optional_auth(name: str) -> ApiKeyAuth:
    def resolve(model, context, credential):
        del context
        key = str(credential.get("key")) if credential and credential.get("key") else None
        if key:
            return AuthResult(auth=ModelAuth(api_key=key), source="stored credential")
        if model.base_url:
            return AuthResult(auth=ModelAuth(), source="keyless provider")
        return None

    return ApiKeyAuth(name=f"{name} credentials", resolve=resolve)


__all__ = ["builtin_models", "builtin_providers", "default_provider_streams"]
