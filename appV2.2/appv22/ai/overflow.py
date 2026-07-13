"""Context-overflow detection. Port of pi overflow.ts + appv22 provider_errors.py."""

from __future__ import annotations

import re
from typing import Any

_OVERFLOW_PATTERNS = (
    re.compile(r"prompt is too long", re.I),
    re.compile(r"request_too_large", re.I),
    re.compile(r"input is too long for requested model", re.I),
    re.compile(r"exceeds the context window", re.I),
    re.compile(r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d,]+ tokens?|\s*\([\d,]+\))?", re.I),
    re.compile(r"input token count.*exceeds the maximum", re.I),
    re.compile(r"maximum prompt length is \d+", re.I),
    re.compile(r"reduce the length of the messages", re.I),
    re.compile(r"maximum context length is \d+ tokens", re.I),
    re.compile(r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?", re.I),
    re.compile(r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)", re.I),
    re.compile(r"exceeds the limit of \d+", re.I),
    re.compile(r"exceeds the available context size", re.I),
    re.compile(r"greater than the context length", re.I),
    re.compile(r"context window exceeds limit", re.I),
    re.compile(r"exceeded model token limit", re.I),
    re.compile(r"too large for model with \d+ maximum context length", re.I),
    re.compile(r"model_context_window_exceeded", re.I),
    re.compile(r"prompt too long; exceeded (?:max )?context length", re.I),
    re.compile(r"context[_ ]length[_ ]exceeded", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"token limit exceeded", re.I),
    re.compile(r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.I),
)

_NON_OVERFLOW_PATTERNS = (
    re.compile(r"^(Throttling error|Service unavailable):", re.I),
    re.compile(r"rate limit", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"(?:unsupported|unknown) parameter.*max_tokens", re.I),
)


def is_context_overflow(error: "BaseException | Any") -> bool:
    text = _error_text(error)
    if not text:
        return False
    if parse_available_output_tokens_from_error(text) is not None:
        return False
    if any(pattern.search(text) for pattern in _NON_OVERFLOW_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in _OVERFLOW_PATTERNS)


def parse_available_output_tokens_from_error(error_msg: str) -> int | None:
    """Parse Hermes-style output-cap errors without treating them as prompt overflow."""
    error_lower = (error_msg or "").lower()
    is_output_cap_error = (
        "max_tokens" in error_lower
        and ("available_tokens" in error_lower or "available tokens" in error_lower)
    ) or (
        "in the output" in error_lower
        and "maximum context length" in error_lower
    ) or (
        "maximum context length" in error_lower
        and "requested" in error_lower
        and "output tokens" in error_lower
    )
    if not is_output_cap_error:
        return None

    for pattern in (
        r"available_tokens[:\s]+([\d,]+)",
        r"available\s+tokens[:\s]+([\d,]+)",
        r"=\s*([\d,]+)\s*$",
    ):
        match = re.search(pattern, error_lower)
        if match:
            tokens = _parse_positive_int(match.group(1))
            if tokens is not None:
                return tokens

    context_match = re.search(r"maximum context length is ([\d,]+)", error_lower)
    parts_match = re.search(
        r"\(([\d,]+)\s+of text input,\s*([\d,]+)\s+of tool input,\s*([\d,]+)\s+in the output\)",
        error_lower,
    )
    if context_match and parts_match:
        context_tokens = _parse_positive_int(context_match.group(1))
        text_tokens = _parse_positive_int(parts_match.group(1))
        tool_tokens = _parse_positive_int(parts_match.group(2))
        if context_tokens is not None and text_tokens is not None and tool_tokens is not None:
            available = context_tokens - text_tokens - tool_tokens
            return available if available >= 1 else None

    context_token_match = re.search(r"maximum context length is ([\d,]+)\s*token", error_lower)
    chars_match = re.search(r"prompt contains ([\d,]+)\s*character", error_lower)
    if context_token_match and chars_match:
        context_tokens = _parse_positive_int(context_token_match.group(1))
        prompt_chars = _parse_positive_int(chars_match.group(1))
        if context_tokens is not None and prompt_chars is not None:
            estimated_input = (prompt_chars + 2) // 3
            available = context_tokens - estimated_input
            return available if available >= 1 else None

    return None


def _parse_positive_int(value: str) -> int | None:
    try:
        parsed = int(value.replace(",", ""))
    except ValueError:
        return None
    return parsed if parsed >= 1 else None


def _error_text(error: "BaseException | Any") -> str:
    parts = [str(error)]
    for attr in ("message", "error", "body", "response"):
        value = getattr(error, attr, None)
        if value:
            parts.append(str(value))
    status_code = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status_code in {400, 413} and len(" ".join(parts).strip()) <= 16:
        parts.append(f"{status_code} status code (no body)")
    return " ".join(part for part in parts if part).strip()
