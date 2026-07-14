"""Provider-scoped request authentication behavior."""

from __future__ import annotations

import os
from collections.abc import Mapping


def _env_value(name: str, request_env: Mapping[str, object] | None) -> str | None:
    value = request_env.get(name) if request_env is not None else None
    if value is None:
        value = os.environ.get(name)
    text = str(value or "").strip()
    return text or None


def resolve_provider_base_url(
    provider: str,
    base_url: str,
    request_env: Mapping[str, object] | None,
) -> str:
    if provider not in {"cloudflare-workers-ai", "cloudflare-ai-gateway"}:
        return base_url
    account_id = _env_value("CLOUDFLARE_ACCOUNT_ID", request_env)
    gateway_id = _env_value("CLOUDFLARE_GATEWAY_ID", request_env)
    if not account_id:
        raise RuntimeError(f"{provider} requires CLOUDFLARE_ACCOUNT_ID")
    if provider == "cloudflare-ai-gateway" and not gateway_id:
        raise RuntimeError("cloudflare-ai-gateway requires CLOUDFLARE_GATEWAY_ID")
    return (
        base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)
        .replace("{CLOUDFLARE_GATEWAY_ID}", gateway_id or "")
    )


def apply_provider_auth_headers(
    provider: str,
    headers: dict[str, str],
    api_key: str | None,
) -> None:
    if provider != "cloudflare-ai-gateway" or not api_key:
        return
    for key in tuple(headers):
        if key.lower() in {"authorization", "x-api-key", "cf-aig-authorization"}:
            del headers[key]
    headers["cf-aig-authorization"] = f"Bearer {api_key}"


__all__ = ["apply_provider_auth_headers", "resolve_provider_base_url"]
