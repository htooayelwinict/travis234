"""Model pricing metadata conversion for Travis provider registrations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from travis.ai.types import Cost, CostTier


def cost_from_mapping(value: object, fallback: Cost | None = None) -> Cost:
    source = value if isinstance(value, Mapping) else {}
    base = fallback or Cost()
    tiers_value = source.get("tiers") if "tiers" in source else None
    tiers = _tiers_from_value(tiers_value) if tiers_value is not None else list(base.tiers)
    return Cost(
        input=_number(source, "input", fallback=base.input),
        output=_number(source, "output", fallback=base.output),
        cache_read=_number(source, "cacheRead", "cache_read", fallback=base.cache_read),
        cache_write=_number(source, "cacheWrite", "cache_write", fallback=base.cache_write),
        tiers=tiers,
    )


def _tiers_from_value(value: object) -> list[CostTier]:
    if not isinstance(value, list):
        return []
    tiers: list[CostTier] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        tiers.append(
            CostTier(
                input_tokens_above=int(_number(item, "inputTokensAbove", "input_tokens_above")),
                input=_number(item, "input"),
                output=_number(item, "output"),
                cache_read=_number(item, "cacheRead", "cache_read"),
                cache_write=_number(item, "cacheWrite", "cache_write"),
            )
        )
    return tiers


def _number(
    source: Mapping[str, Any],
    primary: str,
    alternate: str | None = None,
    *,
    fallback: float = 0.0,
) -> float:
    if primary in source:
        value = source[primary]
    elif alternate is not None and alternate in source:
        value = source[alternate]
    else:
        return float(fallback)
    return float(value or 0)


__all__ = ["cost_from_mapping"]
