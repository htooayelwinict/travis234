"""Provider and protocol aware request authentication headers."""

from __future__ import annotations

from travis.ai.provider_metadata import normalize_provider_id


_ANTHROPIC_API_MODES = {"anthropic-messages", "anthropic_messages"}
_GOOGLE_API_MODES = {
    "google-generative-ai",
    "google_generative_ai",
    "google-vertex",
    "google_vertex",
}
_ANTHROPIC_API_KEY_PROVIDERS = {
    "anthropic",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "opencode",
    "opencode-go",
}
_GOOGLE_API_KEY_PROVIDERS = {"google", "google-vertex", "opencode", "opencode-go"}


def build_request_auth_headers(
    provider: str,
    api: str,
    credential: str,
    *,
    credential_kind: str = "api_key",
) -> dict[str, str]:
    """Return wire headers without changing legacy custom-provider defaults."""

    normalized_provider = normalize_provider_id(provider)
    normalized_api = str(api or "").strip().lower()
    if (
        normalized_provider == "anthropic"
        and (credential_kind != "api_key" or "sk-ant-oat" in credential)
    ):
        return {
            "Authorization": f"Bearer {credential}",
            "anthropic-version": "2023-06-01",
        }
    if (
        credential_kind == "api_key"
        and normalized_api in _ANTHROPIC_API_MODES
        and normalized_provider in _ANTHROPIC_API_KEY_PROVIDERS
    ):
        return {
            "x-api-key": credential,
            "anthropic-version": "2023-06-01",
        }
    if (
        credential_kind == "api_key"
        and normalized_api in _GOOGLE_API_MODES
        and normalized_provider in _GOOGLE_API_KEY_PROVIDERS
    ):
        return {"x-goog-api-key": credential}
    return {"Authorization": f"Bearer {credential}"}


__all__ = ["build_request_auth_headers"]
