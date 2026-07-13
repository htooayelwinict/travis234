"""Credential scoping for lower-trust tool subprocesses."""

from __future__ import annotations

from collections.abc import Mapping

from travis.ai.providers.catalog import provider_catalog


TOOL_ENV_PASSTHROUGH = "TRAVIS234_TOOL_ENV_PASSTHROUGH"

_INTERNAL_INFERENCE_CREDENTIALS = frozenset(
    {
        "TRAVIS234_WORKER_LLM_API_KEY",
    }
)


def provider_credential_env_names() -> frozenset[str]:
    """Return credential variables owned by inference providers.

    The provider catalog is the authority so adding a built-in provider also
    extends the subprocess boundary. Custom operator variables can be allowed
    deliberately through ``TRAVIS234_TOOL_ENV_PASSTHROUGH``.
    """

    names = set(_INTERNAL_INFERENCE_CREDENTIALS)
    for descriptor in provider_catalog():
        names.update(descriptor.api_key_env_vars)
    return frozenset(names)


def sanitize_tool_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Copy an environment while removing provider credentials by default."""

    sanitized = dict(environment)
    passthrough = _parse_passthrough(sanitized.pop(TOOL_ENV_PASSTHROUGH, ""))
    for name in provider_credential_env_names() - passthrough:
        sanitized.pop(name, None)
    return sanitized


def _parse_passthrough(value: str) -> frozenset[str]:
    return frozenset(name.strip() for name in value.split(",") if name.strip())


__all__ = [
    "TOOL_ENV_PASSTHROUGH",
    "provider_credential_env_names",
    "sanitize_tool_environment",
]
