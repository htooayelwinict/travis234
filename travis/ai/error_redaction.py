"""Secret redaction for provider-controlled diagnostic text."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_SENSITIVE_NAMES = {
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "proxyauthorization",
    "secret",
    "token",
}


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def is_sensitive_name(value: object) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in _SENSITIVE_NAMES
        or "authorization" in normalized
        or normalized.endswith(
            (
                "apikey",
                "cookie",
                "credential",
                "credentials",
                "password",
                "privatekey",
                "secret",
                "token",
            )
        )
    )


def redact_sensitive_data(value: Any, secrets: Iterable[str] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if is_sensitive_name(key)
                else redact_sensitive_data(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value, secrets)
    return value


def redact_sensitive_text(value: object, secrets: Iterable[str] = ()) -> str:
    redacted = str(value)
    for secret in sorted(
        {str(secret) for secret in secrets if secret is not None and str(secret)},
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(r"(?i)Bearer\s+[^\s\",}]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    return redacted


__all__ = ["is_sensitive_name", "redact_sensitive_data", "redact_sensitive_text"]
