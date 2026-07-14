"""Load the generated model catalog shipped with Travis234.

The catalog is data rather than provider logic. It preserves per-model context,
output, modality, cost, and compatibility contracts.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from travis.ai.model_cost import cost_from_mapping
from travis.ai.provider_metadata import string_mapping
from travis.ai.types import Model


def load_builtin_models() -> tuple[Model, ...]:
    payload = json.loads(files("travis.ai").joinpath("builtin_models.json").read_text(encoding="utf-8"))
    models: list[Model] = []
    for provider_models in payload.values():
        if not isinstance(provider_models, dict):
            continue
        for raw in provider_models.values():
            if isinstance(raw, dict):
                models.append(_model_from_catalog(raw))
    return tuple(models)


def load_builtin_models_by_provider() -> dict[str, tuple[Model, ...]]:
    grouped: dict[str, list[Model]] = {}
    for model in load_builtin_models():
        grouped.setdefault(model.provider, []).append(model)
    return {provider: tuple(models) for provider, models in grouped.items()}


def _model_from_catalog(raw: dict[str, Any]) -> Model:
    raw_cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
    return Model(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        api=str(raw["api"]),
        provider=str(raw["provider"]),
        base_url=str(raw.get("baseUrl") or ""),
        reasoning=bool(raw.get("reasoning", False)),
        thinking_level_map=string_mapping(raw.get("thinkingLevelMap")),
        input=[value for value in raw.get("input", ["text"]) if value in {"text", "image"}],
        cost=cost_from_mapping(raw_cost),
        context_window=int(raw.get("contextWindow") or 0),
        max_tokens=int(raw.get("maxTokens") or 0),
        headers=string_mapping(raw.get("headers")),
        compat=dict(raw["compat"]) if isinstance(raw.get("compat"), dict) else None,
    )


__all__ = ["load_builtin_models", "load_builtin_models_by_provider"]
