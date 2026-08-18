"""Provider-facing Travis234 client identity."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from travis.ai.provider_metadata import normalize_provider_id


def travis_user_agent() -> str:
    try:
        package_version = version("travis234")
    except PackageNotFoundError:
        package_version = "development"
    return f"Travis234/{package_version}"


def apply_provider_identity_headers(provider: str, headers: dict[str, str]) -> None:
    if normalize_provider_id(provider) != "kimi-coding":
        return
    for key in tuple(headers):
        if key.lower() == "user-agent":
            del headers[key]
    headers["User-Agent"] = travis_user_agent()


__all__ = ["apply_provider_identity_headers", "travis_user_agent"]
