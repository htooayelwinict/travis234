"""Pure helpers used to refresh the generated model catalog."""

from __future__ import annotations

from typing import Any
import warnings


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


__all__ = ["apply_openrouter_capabilities"]
