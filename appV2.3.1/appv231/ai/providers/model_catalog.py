"""Hermes-style remote model catalog for appv231 providers.

The provider picker needs current model lists, but production agents must keep
working when the catalog endpoint is unavailable. This module mirrors Hermes'
catalog design: validate the manifest, cache it on disk, fetch primary then
fallback URLs, and use stale disk cache on network failure.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from appv231.ai.types import Cost, Model

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_URL = "https://hermes-agent.nousresearch.com/docs/api/model-catalog.json"
DEFAULT_CATALOG_FALLBACK_URLS: tuple[str, ...] = (
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/website/static/api/model-catalog.json",
)
DEFAULT_TTL_HOURS = 1
DEFAULT_FETCH_TIMEOUT = 8.0
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_STARTUP_FETCH_TIMEOUT_SECONDS = 3.0
OPENROUTER_LIVE_MODEL_CACHE_TTL_SECONDS = 300.0
OPENROUTER_FULL_CONTEXT_OUTPUT_FALLBACK_TOKENS = 16_384
OPENROUTER_CONTEXT_RESERVE_TOKENS = 4_096
SUPPORTED_SCHEMA_VERSION = 1
_USER_AGENT = "appv231"
_FALSE_VALUES = {"0", "false", "no", "off"}

_catalog_cache: dict[str, Any] | None = None
_catalog_cache_source_mtime: float = 0.0
_openrouter_live_model_cache: tuple[float, dict[str, Any] | None] | None = None
_openrouter_live_model_metadata: dict[tuple[str, str], dict[str, Any]] = {}
_openrouter_live_model_last_error: BaseException | None = None


def _appv231_home() -> Path:
    configured = os.environ.get("APPV231_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".appv231"


def _cache_path() -> Path:
    return _appv231_home() / "cache" / "model_catalog.json"


def _openrouter_live_cache_path() -> Path:
    return _appv231_home() / "cache" / "openrouter_models.json"


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


def openrouter_live_catalog_item_to_model(item: dict[str, Any], base_model: Model) -> Model | None:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None

    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    modalities = architecture.get("input_modalities", architecture.get("inputModalities", []))
    input_types = ["text"]
    if isinstance(modalities, list) and any(str(value).lower() == "image" for value in modalities):
        input_types.append("image")

    supported_parameters = item.get("supported_parameters", item.get("supportedParameters", []))
    reasoning = bool(item.get("reasoning"))
    if isinstance(supported_parameters, list) and any("reason" in str(value).lower() for value in supported_parameters):
        reasoning = True

    context_window = _positive_int_or(item.get("context_length", item.get("contextLength")), base_model.context_window)
    max_completion_tokens = _safe_openrouter_completion_tokens(
        top_provider.get("max_completion_tokens", top_provider.get("maxCompletionTokens")),
        context_window,
        base_model.max_tokens,
    )

    model = replace(
        base_model,
        id=model_id,
        name=str(item.get("name") or model_id),
        provider="openrouter",
        cost=_openrouter_pricing_to_cost(item.get("pricing"), base_model.cost),
        context_window=context_window,
        max_tokens=max_completion_tokens,
        reasoning=reasoning,
        input=input_types,
    )
    _record_openrouter_live_model_metadata(model, item)
    return model


def get_live_openrouter_model_metadata(model: Model) -> dict[str, Any]:
    metadata = _openrouter_live_model_metadata.get((model.provider, model.id))
    if metadata is None:
        return {}
    return {
        "supported_parameters": list(metadata.get("supported_parameters", [])),
        "pricing": dict(metadata.get("pricing", {})),
        "top_provider": dict(metadata.get("top_provider", {})),
        "architecture": dict(metadata.get("architecture", {})),
    }


def get_last_openrouter_live_catalog_error() -> BaseException | None:
    return _openrouter_live_model_last_error


def get_live_openrouter_models(
    *,
    base_model: Model,
    force_refresh: bool = False,
    timeout: float = OPENROUTER_STARTUP_FETCH_TIMEOUT_SECONDS,
) -> list[Model]:
    global _openrouter_live_model_cache, _openrouter_live_model_last_error

    _openrouter_live_model_last_error = None
    if not _live_catalog_enabled():
        return []
    now = time.time()
    if (
        not force_refresh
        and _openrouter_live_model_cache is not None
        and now - _openrouter_live_model_cache[0] < OPENROUTER_LIVE_MODEL_CACHE_TTL_SECONDS
    ):
        cached_payload = _openrouter_live_model_cache[1]
        return _openrouter_live_payload_to_models(cached_payload, base_model) if cached_payload is not None else []

    if not force_refresh:
        disk_payload, disk_mtime = _read_openrouter_live_cache_with_mtime()
        if disk_payload is not None and now - disk_mtime < OPENROUTER_LIVE_MODEL_CACHE_TTL_SECONDS:
            _openrouter_live_model_cache = (now, disk_payload)
            return _openrouter_live_payload_to_models(disk_payload, base_model)

    payload = _fetch_openrouter_live_payload(timeout=timeout)
    if payload is not None:
        _write_openrouter_live_cache(payload)
    else:
        payload = _read_openrouter_live_cache()

    models = _openrouter_live_payload_to_models(payload, base_model) if payload is not None else []
    _openrouter_live_model_cache = (now, payload) if payload is not None else None
    return list(models)


def find_live_openrouter_model(model_id: str, *, base_model: Model, force_refresh: bool = False) -> Model | None:
    wanted = model_id.strip().lower()
    for model in get_live_openrouter_models(base_model=base_model, force_refresh=force_refresh):
        if model.id.lower() == wanted:
            return model
    return None


def _safe_openrouter_completion_tokens(value: object, context_window: int, fallback: int) -> int:
    parsed = _positive_int_or(value, fallback)
    if parsed <= 0:
        return fallback
    if context_window <= 0:
        return parsed
    if context_window > OPENROUTER_CONTEXT_RESERVE_TOKENS:
        max_safe = max(1, context_window - OPENROUTER_CONTEXT_RESERVE_TOKENS)
    else:
        max_safe = max(1, context_window // 2)
    if parsed >= max_safe:
        return min(parsed, OPENROUTER_FULL_CONTEXT_OUTPUT_FALLBACK_TOKENS, max_safe)
    return min(parsed, max_safe)


def _positive_int_or(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _openrouter_pricing_to_cost(pricing: object, fallback: Cost) -> Cost:
    if not isinstance(pricing, dict):
        return fallback
    cost = Cost(
        input=_price_per_million(pricing.get("prompt"), fallback.input),
        output=_price_per_million(pricing.get("completion"), fallback.output),
        cache_read=_price_per_million(pricing.get("input_cache_read"), fallback.cache_read),
        cache_write=_price_per_million(
            pricing.get("input_cache_write", pricing.get("cache_write")),
            fallback.cache_write,
        ),
    )
    return cost


def _price_per_million(value: object, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(parsed) or parsed < 0:
        return fallback
    return parsed * 1_000_000.0


def _record_openrouter_live_model_metadata(model: Model, item: dict[str, Any]) -> None:
    supported_parameters = item.get("supported_parameters", item.get("supportedParameters", []))
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    _openrouter_live_model_metadata[(model.provider, model.id)] = {
        "supported_parameters": [str(value) for value in supported_parameters]
        if isinstance(supported_parameters, list)
        else [],
        "pricing": dict(pricing),
        "top_provider": dict(top_provider),
        "architecture": dict(architecture),
    }


def _clear_openrouter_live_model_metadata() -> None:
    for key in list(_openrouter_live_model_metadata):
        if key[0] == "openrouter":
            del _openrouter_live_model_metadata[key]


def _live_catalog_enabled() -> bool:
    enabled_raw = os.environ.get("APPV231_MODEL_CATALOG_ENABLED", "true").strip().lower()
    return enabled_raw not in _FALSE_VALUES


def _fetch_openrouter_live_payload(*, timeout: float) -> dict[str, Any] | None:
    global _openrouter_live_model_last_error
    try:
        request = urllib.request.Request(
            OPENROUTER_MODELS_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        _openrouter_live_model_last_error = exc
        logger.info("openrouter live model catalog fetch failed: %s", exc)
        return None
    return payload if _validate_openrouter_live_payload(payload) else None


def _validate_openrouter_live_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, list):
        return False
    return any(isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id").strip() for item in data)


def _openrouter_live_payload_to_models(payload: dict[str, Any], base_model: Model) -> list[Model]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    _clear_openrouter_live_model_metadata()
    models: dict[tuple[str, str], Model] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        model = openrouter_live_catalog_item_to_model(item, base_model)
        if model is not None:
            models[(model.provider, model.id)] = model
    return list(models.values())


def _read_openrouter_live_cache_with_mtime() -> tuple[dict[str, Any] | None, float]:
    path = _openrouter_live_cache_path()
    try:
        mtime = path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None, 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, 0.0
    if not _validate_openrouter_live_payload(payload):
        return None, 0.0
    return payload, mtime


def _read_openrouter_live_cache() -> dict[str, Any] | None:
    payload, _mtime = _read_openrouter_live_cache_with_mtime()
    return payload


def _write_openrouter_live_cache(payload: dict[str, Any]) -> None:
    path = _openrouter_live_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.info("openrouter live model catalog cache write failed: %s", exc)


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
    global _catalog_cache, _catalog_cache_source_mtime, _openrouter_live_model_cache, _openrouter_live_model_metadata
    global _openrouter_live_model_last_error
    _catalog_cache = None
    _catalog_cache_source_mtime = 0.0
    _openrouter_live_model_cache = None
    _openrouter_live_model_metadata = {}
    _openrouter_live_model_last_error = None
