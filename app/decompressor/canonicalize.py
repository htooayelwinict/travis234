"""Final Envelope boundary canonicalization for decompressor output."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.schemas import Envelope, ExactLiteral, extract_literal_contract


FORBIDDEN_ENVELOPE_FIELDS = frozenset(
    {
        "planner_hint",
        "planner_confidence",
        "planner_alternatives",
        "execution_hints",
        "budget_hint",
        "steps",
        "strategy",
        "worker_type",
        "max_tool_calls",
        "max_model_calls",
    }
)

PLANNER_LEAK_INTENTS = frozenset({"observe_first"})
COMPLEXITY_HINTS = frozenset({"low", "medium", "high"})
UNDERSPECIFIED_INPUT_TYPE = "ambiguous_pronoun_reference_request"
GENERATED_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9_]{1,}\]")


def canonicalize_envelope(envelope: Envelope) -> Envelope:
    """Deduplicate, canonicalize, and validate the final Envelope boundary.

    The decompressor boundary is descriptive-only. This function is the final
    guard that strips any planner/kernel-shaped keys from intermediate dicts and
    merges semantically equivalent descriptive entries. It must not inject
    scenario-specific facts that the prompt chain did not emit.
    """

    data = _strip_forbidden_keys(envelope.model_dump())
    data["input_type"] = _clean_text(data.get("input_type")) or "unspecified_input"
    data["normalized_input"] = _clean_text(data.get("normalized_input"))
    data["user_goal"] = _clean_nullable_text(data.get("user_goal"))
    data["intents"] = [
        intent
        for intent in _unique_strings(data.get("intents", []))
        if intent not in PLANNER_LEAK_INTENTS
    ]
    data["domains"] = _unique_strings(data.get("domains", []))
    data["risks"] = _unique_strings(data.get("risks", []))
    data["context_needed"] = _unique_strings(data.get("context_needed", []))
    data["constraints"] = _unique_strings(data.get("constraints", []))
    data["ambiguity"] = [_ensure_period(value) for value in _unique_strings(data.get("ambiguity", []))]
    data["assumptions"] = _canonical_assumptions(data.get("assumptions", []))
    data["literal_contract"] = _merge_literal_contract(
        data.get("literal_contract", []),
        extract_literal_contract(data.get("raw_input", "")),
        raw_input=str(data.get("raw_input") or ""),
    )
    metadata = dict(data.get("metadata") or {})
    placeholders = _generated_placeholders(data, raw_input=str(data.get("raw_input") or ""))
    if placeholders:
        data = _replace_generated_placeholders(data, set(placeholders))
        metadata["invalid_generated_placeholders"] = placeholders
    data["metadata"] = metadata
    data["confidence"] = _clamp_confidence(data.get("confidence", 0.0))
    data["complexity_hint"] = _canonical_complexity(data.get("complexity_hint"))
    data = _apply_underspecified_input_guard(data)

    return Envelope.model_validate(data)


def _strip_forbidden_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_forbidden_keys(child)
            for key, child in value.items()
            if key not in FORBIDDEN_ENVELOPE_FIELDS
        }
    if isinstance(value, list):
        return [_strip_forbidden_keys(child) for child in value]
    return value


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean_text(raw)
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_nullable_text(value: Any) -> str | None:
    text = _clean_text(value)
    return text or None


def _canonical_assumptions(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = " ".join(str(raw).strip().split())
        if not text or _is_unsafe_assumption(text):
            continue
        text = _ensure_period(text)
        key = _meaning_key(text)
        if key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _canonical_complexity(value: Any) -> str:
    complexity = _clean_text(value).lower()
    return complexity if complexity in COMPLEXITY_HINTS else "medium"


def _is_unsafe_assumption(text: str) -> bool:
    lowered = text.lower()
    unsafe_phrases = (
        "will resolve",
        "will fix",
        "definitely",
        "is available",
        "are available",
        "caused by",
        "root cause is",
    )
    return any(phrase in lowered for phrase in unsafe_phrases)


def _meaning_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _ensure_period(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else f"{text}."


def _apply_underspecified_input_guard(data: dict[str, Any]) -> dict[str, Any]:
    text = str(data.get("normalized_input") or data.get("raw_input") or "").strip().lower()
    if text not in {"", "it", "this", "that", "these", "those"}:
        return data

    data["input_type"] = UNDERSPECIFIED_INPUT_TYPE
    data["confidence"] = min(float(data.get("confidence") or 0.0), 0.5)
    data["risks"] = _merge_ordered(data.get("risks", []), ["ambiguous_scope"])
    data["context_needed"] = _merge_ordered(data.get("context_needed", []), ["scope_clarification"])
    data["constraints"] = _merge_ordered(
        data.get("constraints", []),
        ["target_scope_must_be_identified_before_mutation"],
    )
    data["ambiguity"] = _merge_ordered(
        data.get("ambiguity", []),
        ["The request is underspecified and has no clear referent."],
    )
    return data


def _merge_ordered(existing: Iterable[str], required: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *required]:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _merge_literal_contract(
    existing: Iterable[Any],
    required: Iterable[ExactLiteral],
    *,
    raw_input: str,
) -> list[dict[str, Any]]:
    result: list[ExactLiteral] = []
    seen: set[tuple[str, str]] = set()
    raw_placeholders = set(GENERATED_PLACEHOLDER_RE.findall(raw_input))
    for raw in [*list(existing or []), *list(required or [])]:
        try:
            literal = raw if isinstance(raw, ExactLiteral) else ExactLiteral.model_validate(raw)
        except Exception:
            continue
        if (
            GENERATED_PLACEHOLDER_RE.fullmatch(literal.value)
            and literal.value not in raw_placeholders
            and literal.value != "[REDACTED]"
        ):
            continue
        key = (literal.kind, literal.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(literal)
    return [literal.model_dump(mode="json") for literal in result]


def _generated_placeholders(data: dict[str, Any], *, raw_input: str) -> list[str]:
    raw_placeholders = set(GENERATED_PLACEHOLDER_RE.findall(raw_input))
    found = sorted(
        {
            placeholder
            for placeholder in GENERATED_PLACEHOLDER_RE.findall(str(_strip_forbidden_keys(data)))
            if placeholder not in raw_placeholders and placeholder != "[REDACTED]"
        }
    )
    return found


def _replace_generated_placeholders(value: Any, placeholders: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_generated_placeholders(child, placeholders)
            for key, child in value.items()
            if key not in {"raw_input", "metadata"}
        } | {key: value[key] for key in ("raw_input", "metadata") if key in value}
    if isinstance(value, list):
        cleaned = [_replace_generated_placeholders(child, placeholders) for child in value]
        return [
            child
            for child in cleaned
            if child not in ("", None, [], {})
        ]
    if isinstance(value, str):
        text = value
        for placeholder in placeholders:
            text = text.replace(placeholder, "the repository root")
        return _clean_text(text)
    return value
