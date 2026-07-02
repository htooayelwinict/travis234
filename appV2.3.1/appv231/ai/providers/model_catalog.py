"""Hermes-style remote model catalog for appv231 providers.

The provider picker needs current model lists, but production agents must keep
working when the catalog endpoint is unavailable. This module mirrors Hermes'
catalog design: validate the manifest, cache it on disk, fetch primary then
fallback URLs, and use stale disk cache on network failure.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_URL = "https://hermes-agent.nousresearch.com/docs/api/model-catalog.json"
DEFAULT_CATALOG_FALLBACK_URLS: tuple[str, ...] = (
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/website/static/api/model-catalog.json",
)
DEFAULT_TTL_HOURS = 1
DEFAULT_FETCH_TIMEOUT = 8.0
SUPPORTED_SCHEMA_VERSION = 1
_USER_AGENT = "appv231"
_FALSE_VALUES = {"0", "false", "no", "off"}

_catalog_cache: dict[str, Any] | None = None
_catalog_cache_source_mtime: float = 0.0


def _appv231_home() -> Path:
    configured = os.environ.get("APPV231_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".appv231"


def _cache_path() -> Path:
    return _appv231_home() / "cache" / "model_catalog.json"


def _load_catalog_config() -> dict[str, Any]:
    """Load the model catalog config with Hermes-compatible defaults."""
    enabled_raw = os.environ.get("APPV231_MODEL_CATALOG_ENABLED", "true").strip().lower()
    providers: dict[str, Any] = {}
    raw_providers = os.environ.get("APPV231_MODEL_CATALOG_PROVIDERS")
    if raw_providers:
        try:
            parsed = json.loads(raw_providers)
            if isinstance(parsed, dict):
                providers = parsed
        except json.JSONDecodeError:
            providers = {}
    return {
        "enabled": enabled_raw not in _FALSE_VALUES,
        "url": os.environ.get("APPV231_MODEL_CATALOG_URL") or DEFAULT_CATALOG_URL,
        "ttl_hours": float(os.environ.get("APPV231_MODEL_CATALOG_TTL_HOURS") or DEFAULT_TTL_HOURS),
        "providers": providers,
    }


def _validate_manifest(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    version = data.get("version")
    if not isinstance(version, int) or version > SUPPORTED_SCHEMA_VERSION:
        return False
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return False
    for provider_name, provider_block in providers.items():
        if not isinstance(provider_name, str) or not isinstance(provider_block, dict):
            return False
        models = provider_block.get("models")
        if not isinstance(models, list):
            return False
        for model in models:
            if not isinstance(model, dict):
                return False
            model_id = model.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                return False
    return True


def _fetch_manifest(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("model catalog fetch failed (%s): %s", url, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.info("model catalog fetch errored (%s): %s", url, exc)
        return None
    return data if _validate_manifest(data) else None


def _fetch_manifest_with_fallback(
    primary_url: str,
    timeout: float,
    fallback_urls: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    if fallback_urls is None:
        fallback_urls = DEFAULT_CATALOG_FALLBACK_URLS
    data = _fetch_manifest(primary_url, timeout)
    if data is not None:
        return data
    for url in fallback_urls:
        if not url or url == primary_url:
            continue
        data = _fetch_manifest(url, timeout)
        if data is not None:
            return data
    return None


def _read_disk_cache() -> tuple[dict[str, Any] | None, float]:
    path = _cache_path()
    try:
        mtime = path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None, 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, 0.0
    if not _validate_manifest(data):
        return None, 0.0
    return data, mtime


def _write_disk_cache(data: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.info("model catalog cache write failed: %s", exc)


def get_catalog(*, force_refresh: bool = False) -> dict[str, Any]:
    global _catalog_cache, _catalog_cache_source_mtime

    config = _load_catalog_config()
    if not config["enabled"]:
        return {}

    ttl_seconds = max(0.0, float(config["ttl_hours"]) * 3600.0)
    disk_data, disk_mtime = _read_disk_cache()
    now = time.time()
    disk_fresh = disk_data is not None and (now - disk_mtime) < ttl_seconds

    if (
        not force_refresh
        and _catalog_cache is not None
        and disk_data is not None
        and disk_mtime == _catalog_cache_source_mtime
        and disk_fresh
    ):
        return _catalog_cache

    if not force_refresh and disk_fresh and disk_data is not None:
        _catalog_cache = disk_data
        _catalog_cache_source_mtime = disk_mtime
        return disk_data

    fetched = _fetch_manifest_with_fallback(str(config["url"]), DEFAULT_FETCH_TIMEOUT)
    if fetched is not None:
        _write_disk_cache(fetched)
        new_disk_data, new_mtime = _read_disk_cache()
        if new_disk_data is not None:
            _catalog_cache = new_disk_data
            _catalog_cache_source_mtime = new_mtime
            return new_disk_data
        _catalog_cache = fetched
        _catalog_cache_source_mtime = now
        return fetched

    if disk_data is not None:
        _catalog_cache = disk_data
        _catalog_cache_source_mtime = disk_mtime
        return disk_data
    return {}


def _fetch_provider_override(provider: str) -> dict[str, Any] | None:
    config = _load_catalog_config()
    if not config["enabled"]:
        return None
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return None
    provider_config = providers.get(provider)
    if not isinstance(provider_config, dict):
        return None
    override_url = provider_config.get("url")
    if not isinstance(override_url, str) or not override_url.strip():
        return None
    return _fetch_manifest(override_url.strip(), DEFAULT_FETCH_TIMEOUT)


def _get_provider_block(provider: str, *, force_refresh: bool = False) -> dict[str, Any] | None:
    override = _fetch_provider_override(provider)
    if override is not None:
        block = override.get("providers", {}).get(provider)
        if isinstance(block, dict):
            return block

    catalog = get_catalog(force_refresh=force_refresh)
    block = catalog.get("providers", {}).get(provider)
    return block if isinstance(block, dict) else None


def get_curated_openrouter_models(*, force_refresh: bool = False) -> list[tuple[str, str]] | None:
    block = _get_provider_block("openrouter", force_refresh=force_refresh)
    if not block:
        return None
    models: list[tuple[str, str]] = []
    for item in block.get("models", []):
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        models.append((model_id, str(item.get("description") or "")))
    return models or None


def get_curated_nous_models(*, force_refresh: bool = False) -> list[str] | None:
    block = _get_provider_block("nous", force_refresh=force_refresh)
    if not block:
        return None
    models = [str(item.get("id") or "").strip() for item in block.get("models", [])]
    models = [model for model in models if model]
    return models or None


def seed_cache_from_checkout(project_root: "Path | str") -> bool:
    src = Path(project_root) / "website" / "static" / "api" / "model-catalog.json"
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("model catalog seed from checkout skipped (%s): %s", src, exc)
        return False
    if not _validate_manifest(data):
        logger.debug("model catalog seed from checkout skipped: invalid manifest at %s", src)
        return False
    _write_disk_cache(data)
    reset_cache()
    return True


def reset_cache() -> None:
    global _catalog_cache, _catalog_cache_source_mtime
    _catalog_cache = None
    _catalog_cache_source_mtime = 0.0
