from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple


_PARAM_FIELDS = (
    "temperature",
    "top_p",
    "max_tokens",
    "timeout_seconds",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "parallel_tool_calls",
    "tool_choice",
    "stop",
    "provider_sort",
)
GENERATION_PARAM_FIELDS = _PARAM_FIELDS
_UNSET_STRINGS = {"", "none", "null"}
_TRUE_STRINGS = {"1", "true", "yes", "y", "on"}
_FALSE_STRINGS = {"0", "false", "no", "n", "off"}
_SECRET_MARKERS = ("sk-", "api_key", "authorization", "bearer ", "secret", "token")


@dataclass(frozen=True)
class GenerationParams:
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    seed: Optional[int] = None
    parallel_tool_calls: Optional[bool] = None
    tool_choice: Optional[str] = None
    stop: Tuple[str, ...] = ()
    provider_sort: Optional[str] = None
    provider_preferences: Optional[Mapping[str, Any]] = None
    sources: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.stop is None:
            object.__setattr__(self, "stop", ())
        elif isinstance(self.stop, str):
            object.__setattr__(self, "stop", (self.stop,))
        else:
            object.__setattr__(self, "stop", _coerce_stop_sequence(self.stop))
        if self.provider_preferences is not None:
            object.__setattr__(self, "provider_preferences", MappingProxyType(dict(self.provider_preferences)))

        cleaned_sources = {}
        for key, value in self.sources.items():
            source_label = _clean_source_label(value)
            if key in _PARAM_FIELDS and source_label:
                cleaned_sources[str(key)] = source_label
        object.__setattr__(self, "sources", MappingProxyType(cleaned_sources))


def params_from_mapping(
    values: Mapping[str, Any],
    *,
    source: Optional[str] = None,
) -> GenerationParams:
    parsed: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    source_label = _clean_source_label(source)

    for field_name in _PARAM_FIELDS:
        if field_name not in values:
            continue

        raw_value = values[field_name]
        if _is_unset(raw_value):
            continue

        parsed_value = _parse_value(field_name, raw_value)
        if parsed_value is None:
            continue
        if field_name == "stop" and parsed_value == ():
            continue

        parsed[field_name] = parsed_value
        if source_label:
            sources[field_name] = source_label

    return GenerationParams(**parsed, sources=sources)


def merge_generation_params(*items: Optional[GenerationParams]) -> GenerationParams:
    merged: Dict[str, Any] = {}
    sources: Dict[str, str] = {}

    for item in items:
        if item is None:
            continue

        for field_name in _PARAM_FIELDS:
            value = getattr(item, field_name)
            if value is None:
                continue
            if field_name == "stop" and value == ():
                continue

            merged[field_name] = value
            source = item.sources.get(field_name)
            if source:
                sources[field_name] = source
            else:
                sources.pop(field_name, None)

        if item.provider_preferences:
            merged["provider_preferences"] = dict(item.provider_preferences)

    return GenerationParams(**merged, sources=sources)


def compact_generation_params_display(params: GenerationParams) -> str:
    parts = []

    for field_name in _PARAM_FIELDS:
        value = getattr(params, field_name)
        if value is None:
            continue

        if field_name == "stop":
            if not value:
                continue
            formatted = _format_stop(value)
        elif field_name == "parallel_tool_calls":
            formatted = "true" if value else "false"
        else:
            formatted = _format_scalar(value)

        source = params.sources.get(field_name)
        if source:
            formatted = f"{formatted} ({source})"
        parts.append(f"{field_name}={formatted}")

    return ", ".join(parts) if parts else "default generation parameters"


def generation_params_to_mapping(params: GenerationParams) -> dict[str, object]:
    """Return the safe, normalized fields explicitly set on ``params``."""

    values: dict[str, object] = {}
    for field_name in GENERATION_PARAM_FIELDS:
        value = getattr(params, field_name)
        if value is None:
            continue
        if field_name == "stop":
            if not value:
                continue
            values[field_name] = list(value)
            continue
        values[field_name] = value
    return values


def generation_params_from_session_mapping(values: object) -> GenerationParams | None:
    """Validate a persisted override snapshot without accepting extra fields."""

    if not isinstance(values, dict):
        return None
    if any(not isinstance(key, str) or key not in GENERATION_PARAM_FIELDS for key in values):
        return None
    if any(_is_unset(value) for value in values.values()):
        return None
    try:
        parsed = params_from_mapping(values, source="session")
    except (TypeError, ValueError):
        return None
    if set(generation_params_to_mapping(parsed)) != set(values):
        return None
    return parsed


def replace_generation_param(
    params: GenerationParams,
    name: str,
    raw_value: object,
    *,
    source: str = "session",
) -> GenerationParams:
    """Return an override snapshot with one validated field replaced."""

    if name not in GENERATION_PARAM_FIELDS:
        raise ValueError(f"unsupported generation parameter: {name}")
    if _is_unset(raw_value):
        raise ValueError(f"{name} requires a value; use /params reset {name}")
    parsed = params_from_mapping({name: raw_value}, source=source)
    parsed_values = generation_params_to_mapping(parsed)
    if name not in parsed_values:
        raise ValueError(f"{name} requires a value; use /params reset {name}")
    candidate = generation_params_to_mapping(params)
    candidate[name] = parsed_values[name]
    return params_from_mapping(candidate, source=source)


def remove_generation_param(
    params: GenerationParams,
    name: str,
    *,
    source: str = "session",
) -> GenerationParams:
    """Return an override snapshot without ``name``."""

    if name not in GENERATION_PARAM_FIELDS:
        raise ValueError(f"unsupported generation parameter: {name}")
    candidate = generation_params_to_mapping(params)
    candidate.pop(name, None)
    return params_from_mapping(candidate, source=source)


def _parse_value(field_name: str, value: Any) -> Any:
    if field_name == "temperature":
        return _parse_float_range(field_name, value, minimum=0.0, maximum=2.0)
    if field_name == "top_p":
        return _parse_float_range(field_name, value, minimum=0.0, maximum=1.0)
    if field_name == "max_tokens":
        return _parse_positive_int(field_name, value)
    if field_name == "timeout_seconds":
        return _parse_positive_float(field_name, value)
    if field_name == "frequency_penalty":
        return _parse_float_range(field_name, value, minimum=-2.0, maximum=2.0)
    if field_name == "presence_penalty":
        return _parse_float_range(field_name, value, minimum=-2.0, maximum=2.0)
    if field_name == "seed":
        return _parse_int(field_name, value)
    if field_name == "parallel_tool_calls":
        return _parse_bool(field_name, value)
    if field_name in {"tool_choice", "provider_sort"}:
        return _parse_text(field_name, value)
    if field_name == "stop":
        return _parse_stop(value)

    raise ValueError(f"unsupported generation parameter: {field_name}")


def _is_unset(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _UNSET_STRINGS
    return False


def _parse_float_range(
    field_name: str,
    value: Any,
    *,
    minimum: float,
    maximum: float,
) -> float:
    parsed = _parse_float(field_name, value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field_name} must be between {_format_scalar(minimum)} and {_format_scalar(maximum)}")
    return parsed


def _parse_positive_float(field_name: str, value: Any) -> float:
    parsed = _parse_float(field_name, value)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _parse_float(field_name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")

    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc

    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _parse_positive_int(field_name: str, value: Any) -> int:
    parsed = _parse_int(field_name, value)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _parse_int(field_name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{field_name} must be an integer")

    text = str(value).strip()
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _parse_bool(field_name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _parse_text(field_name: str, value: Any) -> Optional[str]:
    text = str(value).strip()
    if text.lower() in _UNSET_STRINGS:
        return None
    if not text:
        return None
    return text


def _parse_stop(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _UNSET_STRINGS:
            return ()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("stop must be a JSON array or comma-separated list") from exc
            return _coerce_stop_sequence(decoded)

        return _coerce_stop_sequence([part.strip() for part in text.split(",") if part.strip()])

    return _coerce_stop_sequence(value)


def _coerce_stop_sequence(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("stop must be a JSON array or comma-separated list")

    sequences = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("stop entries must be strings")
        sequences.append(item)

    return tuple(sequences)


def _format_stop(value: Tuple[str, ...]) -> str:
    count = len(value)
    noun = "sequence" if count == 1 else "sequences"
    return f"{count} {noun}"


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _clean_source_label(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return ""
    return text


__all__ = [
    "GENERATION_PARAM_FIELDS",
    "GenerationParams",
    "compact_generation_params_display",
    "generation_params_from_session_mapping",
    "generation_params_to_mapping",
    "merge_generation_params",
    "params_from_mapping",
    "remove_generation_param",
    "replace_generation_param",
]
