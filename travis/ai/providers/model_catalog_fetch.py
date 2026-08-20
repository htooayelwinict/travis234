"""Bounded, policy-checked remote provider model catalog reads."""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

MAX_MODEL_CATALOG_BYTES = 2 * 1024 * 1024
MODEL_CATALOG_READ_CHUNK_BYTES = 64 * 1024

logger = logging.getLogger(__name__)


class ModelCatalogURLPolicyError(ValueError):
    """Raised when a catalog URL is outside the explicit remote-read policy."""


class ModelCatalogTooLargeError(ValueError):
    """Raised when a catalog response exceeds the bounded reader limit."""


def _is_loopback_hostname(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_model_catalog_url(url: str) -> str:
    """Return a URL allowed for model discovery or raise before request I/O."""

    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    allowed = parsed.scheme == "https" and bool(hostname)
    if parsed.scheme == "http" and hostname:
        allowed = _is_loopback_hostname(hostname)
    if not allowed or parsed.username is not None or parsed.password is not None:
        raise ModelCatalogURLPolicyError("model catalog URL must use HTTPS or loopback HTTP")
    return url


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_model_catalog_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_request(request: urllib.request.Request, *, timeout: float) -> Any:
    opener = urllib.request.build_opener(_ValidatedRedirectHandler())
    return opener.open(request, timeout=timeout)


def _read_bounded(response: Any) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining_with_probe = MAX_MODEL_CATALOG_BYTES - total + 1
        chunk = response.read(min(MODEL_CATALOG_READ_CHUNK_BYTES, remaining_with_probe))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_MODEL_CATALOG_BYTES:
            raise ModelCatalogTooLargeError("model catalog response exceeded size limit")
        chunks.append(chunk)


def _model_ids(payload: object) -> list[str]:
    items: object
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = payload.get("data")
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError("model catalog payload must be a list or contain a data list")
    return [
        item["id"]
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    ]


def fetch_model_catalog(
    *,
    provider_name: str,
    url: str,
    api_key: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 8.0,
) -> list[str] | None:
    """Fetch current model IDs while preserving the best-effort compatibility contract."""

    try:
        validate_model_catalog_url(url)
        request = urllib.request.Request(url)
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "travis")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with _open_request(request, timeout=timeout) as response:
            effective_url = str(response.geturl())
            validate_model_catalog_url(effective_url)
            raw = _read_bounded(response)
        return _model_ids(json.loads(raw.decode("utf-8")))
    except Exception as exc:
        logger.debug(
            "model catalog fetch failed for %s (%s)",
            provider_name,
            type(exc).__name__,
        )
        return None


__all__ = [
    "MAX_MODEL_CATALOG_BYTES",
    "MODEL_CATALOG_READ_CHUNK_BYTES",
    "ModelCatalogTooLargeError",
    "ModelCatalogURLPolicyError",
    "fetch_model_catalog",
    "validate_model_catalog_url",
]
