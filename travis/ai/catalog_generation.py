"""Pure helpers used to refresh the generated model catalog."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from typing import Any
from typing import Literal
import warnings


CatalogDriftKind = Literal[
    "provider_missing_from_travis",
    "provider_missing_from_pi",
    "model_missing_from_travis",
    "model_missing_from_pi",
    "field_difference",
    "unsupported_api",
    "unsupported_compatibility",
]

COMPARABLE_MODEL_FIELDS = (
    "id",
    "name",
    "api",
    "provider",
    "baseUrl",
    "reasoning",
    "thinkingLevelMap",
    "input",
    "cost",
    "contextWindow",
    "maxTokens",
    "compat",
)

_RUNTIME_SUPPORTED_COMPATIBILITY_KEYS = {
    # Anthropic transport: preserves thinking blocks for providers that accept
    # an explicitly empty signature.
    "allowEmptySignature",
    # OpenAI transports: controls the provider-specific affinity value shape.
    "sessionAffinityFormat",
}


@dataclass(frozen=True)
class CatalogDrift:
    kind: CatalogDriftKind
    provider: str
    model: str | None = None
    field: str | None = None
    current: Any = None
    reference: Any = None

    def sort_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.provider,
            self.model or "",
            self.kind,
            self.field or "",
            _stable_json(self.current),
            _stable_json(self.reference),
        )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_catalog(payload: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("catalog must contain a provider object")
    for provider, models in payload.items():
        if not isinstance(provider, str) or not isinstance(models, dict):
            raise ValueError("catalog provider entries must contain model objects")
        for model_id, record in models.items():
            if not isinstance(model_id, str) or not isinstance(record, dict):
                raise ValueError("catalog model entries must be objects")
            if record.get("id") != model_id:
                raise ValueError(f"catalog record id mismatch for {provider}/{model_id}")
            if record.get("provider") != provider:
                raise ValueError(f"catalog record provider mismatch for {provider}/{model_id}")
            if not isinstance(record.get("api"), str) or not record["api"]:
                raise ValueError(f"catalog record api is invalid for {provider}/{model_id}")
            for numeric_field in ("contextWindow", "maxTokens"):
                value = record.get(numeric_field)
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    raise ValueError(
                        f"catalog record {numeric_field} is invalid for {provider}/{model_id}"
                    )
            if "input" in record and not isinstance(record["input"], list):
                raise ValueError(f"catalog record input is invalid for {provider}/{model_id}")
            if "cost" in record and not isinstance(record["cost"], dict):
                raise ValueError(f"catalog record cost is invalid for {provider}/{model_id}")
            if "compat" in record and not isinstance(record["compat"], dict):
                raise ValueError(f"catalog record compat is invalid for {provider}/{model_id}")
    return payload


def compare_pi_catalogs(
    current: dict[str, Any],
    reference: dict[str, Any],
) -> tuple[CatalogDrift, ...]:
    current = validate_catalog(current)
    reference = validate_catalog(reference)
    current_apis = {
        str(record.get("api"))
        for models in current.values()
        for record in models.values()
        if record.get("api")
    }
    current_compat = _RUNTIME_SUPPORTED_COMPATIBILITY_KEYS | {
        str(key)
        for models in current.values()
        for record in models.values()
        for key in (
            record.get("compat", {}).keys()
            if isinstance(record.get("compat"), dict)
            else ()
        )
    }
    drift: list[CatalogDrift] = []

    for provider in sorted(set(current) | set(reference)):
        if provider not in current:
            drift.append(CatalogDrift("provider_missing_from_travis", provider))
            continue
        if provider not in reference:
            drift.append(CatalogDrift("provider_missing_from_pi", provider))
            continue
        current_models = current[provider]
        reference_models = reference[provider]
        for model_id in sorted(set(current_models) | set(reference_models)):
            if model_id not in current_models:
                drift.append(CatalogDrift("model_missing_from_travis", provider, model_id))
                reference_record = reference_models[model_id]
            elif model_id not in reference_models:
                drift.append(CatalogDrift("model_missing_from_pi", provider, model_id))
                continue
            else:
                reference_record = reference_models[model_id]

            reference_api = reference_record.get("api")
            if reference_api not in current_apis:
                drift.append(
                    CatalogDrift(
                        "unsupported_api",
                        provider,
                        model_id,
                        "api",
                        reference=reference_api,
                    )
                )
            reference_compat = reference_record.get("compat")
            if isinstance(reference_compat, dict):
                for key in sorted(set(reference_compat) - current_compat):
                    drift.append(
                        CatalogDrift(
                            "unsupported_compatibility",
                            provider,
                            model_id,
                            f"compat.{key}",
                            reference=reference_compat[key],
                        )
                    )
            if model_id not in current_models:
                continue
            current_record = current_models[model_id]
            for field in COMPARABLE_MODEL_FIELDS:
                if current_record.get(field) != reference_record.get(field):
                    drift.append(
                        CatalogDrift(
                            "field_difference",
                            provider,
                            model_id,
                            field,
                            current_record.get(field),
                            reference_record.get(field),
                        )
                    )

    return tuple(sorted(drift, key=CatalogDrift.sort_key))


def catalog_drift_to_dict(item: CatalogDrift) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "provider": item.provider,
        "model": item.model,
        "field": item.field,
        "current": item.current,
        "reference": item.reference,
    }


@dataclass(frozen=True)
class CatalogPromotion:
    action: Literal["add", "update", "retire"]
    provider: str
    model: str
    reason: str
    evidence: str


@dataclass(frozen=True)
class PromotionSet:
    pi_commit: str
    promotions: tuple[CatalogPromotion, ...]


def load_promotion_set(payload: Any) -> PromotionSet:
    if not isinstance(payload, dict):
        raise ValueError("promotion manifest must contain an object")
    pi_commit = payload.get("piCommit")
    if (
        not isinstance(pi_commit, str)
        or len(pi_commit) != 40
        or any(char not in "0123456789abcdef" for char in pi_commit.lower())
    ):
        raise ValueError("promotion manifest requires a full Pi commit")
    raw_promotions = payload.get("promotions")
    if not isinstance(raw_promotions, list) or not raw_promotions:
        raise ValueError("promotion manifest requires at least one promotion")
    promotions: list[CatalogPromotion] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_promotions:
        if not isinstance(raw, dict):
            raise ValueError("promotion entries must be objects")
        action = raw.get("action")
        provider = raw.get("provider")
        model = raw.get("model")
        reason = raw.get("reason")
        evidence = raw.get("evidence")
        if action not in {"add", "update", "retire"}:
            raise ValueError("promotion action must be add, update, or retire")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (provider, model, reason)
        ):
            raise ValueError("promotion provider, model, and reason are required")
        if not isinstance(evidence, str) or not evidence.startswith("https://"):
            raise ValueError("promotion evidence must be an https URL")
        key = (provider, model)
        if key in seen:
            raise ValueError(f"duplicate promotion scope: {provider}/{model}")
        seen.add(key)
        promotions.append(CatalogPromotion(action, provider, model, reason, evidence))
    return PromotionSet(pi_commit.lower(), tuple(promotions))


def apply_pi_promotions(
    current: dict[str, Any],
    reference: dict[str, Any],
    promotion_set: PromotionSet,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    current = validate_catalog(current)
    reference = validate_catalog(reference)
    current_apis = {
        str(record.get("api"))
        for models in current.values()
        for record in models.values()
        if record.get("api")
    }
    current_compat = _RUNTIME_SUPPORTED_COMPATIBILITY_KEYS | {
        str(key)
        for models in current.values()
        for record in models.values()
        for key in (
            record.get("compat", {}).keys()
            if isinstance(record.get("compat"), dict)
            else ()
        )
    }
    updated = copy.deepcopy(current)
    changed: list[str] = []
    for promotion in promotion_set.promotions:
        if promotion.provider not in current:
            raise ValueError(f"unsupported provider: {promotion.provider}")
        provider_models = updated[promotion.provider]
        if promotion.action == "retire":
            if promotion.model not in provider_models:
                raise ValueError(
                    f"retirement target is absent: {promotion.provider}/{promotion.model}"
                )
            del provider_models[promotion.model]
        else:
            reference_record = reference.get(promotion.provider, {}).get(promotion.model)
            if not isinstance(reference_record, dict):
                raise ValueError(
                    f"Pi promotion target is absent: {promotion.provider}/{promotion.model}"
                )
            if reference_record.get("api") not in current_apis:
                raise ValueError(
                    f"unsupported api for {promotion.provider}/{promotion.model}: "
                    f"{reference_record.get('api')}"
                )
            compat = reference_record.get("compat")
            unknown_compat = (
                sorted(set(compat) - current_compat) if isinstance(compat, dict) else []
            )
            if unknown_compat:
                raise ValueError(
                    f"unsupported compatibility for {promotion.provider}/{promotion.model}: "
                    f"{unknown_compat}"
                )
            exists = promotion.model in provider_models
            if promotion.action == "add" and exists:
                raise ValueError("add promotion target already exists")
            if promotion.action == "update" and not exists:
                raise ValueError("update promotion target is absent")
            provider_models[promotion.model] = copy.deepcopy(reference_record)
        changed.append(f"{promotion.action}:{promotion.provider}/{promotion.model}")
    validate_catalog(updated)
    return updated, tuple(sorted(changed))


def apply_openrouter_capabilities(
    catalog: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Merge live model-level OpenRouter limits into an existing catalog."""

    provider_models = catalog.get("openrouter")
    items = payload.get("data")
    if not isinstance(provider_models, dict) or not isinstance(items, list):
        return catalog, 0

    live_by_id = {
        str(item["id"]): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    next_provider = dict(provider_models)
    changed = 0

    for model_id, raw_model in provider_models.items():
        item = live_by_id.get(str(model_id))
        if not isinstance(raw_model, dict) or item is None:
            continue
        top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
        context_window = _positive_int(
            top_provider.get("context_length", top_provider.get("contextLength"))
        ) or _positive_int(item.get("context_length", item.get("contextLength")))
        max_tokens = _positive_int(
            top_provider.get("max_completion_tokens", top_provider.get("maxCompletionTokens"))
        )
        if max_tokens is not None and context_window is not None and max_tokens >= context_window:
            warnings.warn(
                f"OpenRouter model {model_id} advertised output limit {max_tokens} "
                f"at or above route context window {context_window}; retaining catalog maxTokens",
                RuntimeWarning,
                stacklevel=2,
            )
            existing_max_tokens = _positive_int(raw_model.get("maxTokens"))
            max_tokens = (
                None
                if existing_max_tokens is not None and existing_max_tokens < context_window
                else min(4_096, context_window - 1)
            )
        next_model = dict(raw_model)
        if context_window is not None:
            next_model["contextWindow"] = context_window
        if max_tokens is not None:
            next_model["maxTokens"] = max_tokens
        if next_model != raw_model:
            next_provider[model_id] = next_model
            changed += 1

    if not changed:
        return catalog, 0
    next_catalog = dict(catalog)
    next_catalog["openrouter"] = next_provider
    return next_catalog, changed


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "CatalogDrift",
    "CatalogPromotion",
    "PromotionSet",
    "apply_openrouter_capabilities",
    "apply_pi_promotions",
    "catalog_drift_to_dict",
    "compare_pi_catalogs",
    "load_promotion_set",
    "validate_catalog",
]
