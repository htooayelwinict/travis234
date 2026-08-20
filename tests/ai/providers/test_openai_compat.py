"""Characterization tests for OpenAI-compatible model capability detection."""

from __future__ import annotations

import pytest

from travis.ai.providers.openai_compat import resolve_openai_compat
from travis.ai.types import Model


def _model(
    provider: str,
    *,
    model_id: str = "model",
    base_url: str = "https://provider.example/v1",
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url=base_url,
    )


@pytest.mark.parametrize(
    ("model", "thinking_format", "max_tokens_field", "strict", "long_cache"),
    (
        (_model("deepseek"), "deepseek", "max_completion_tokens", True, True),
        (_model("zai"), "zai", "max_tokens", True, True),
        (_model("together"), "together", "max_tokens", False, False),
        (_model("ant-ling"), "ant-ling", "max_tokens", True, False),
        (
            _model("openrouter", model_id="anthropic/claude-test"),
            "openrouter",
            "max_completion_tokens",
            True,
            True,
        ),
        (_model("openai"), "openai", "max_completion_tokens", True, True),
    ),
)
def test_provider_family_detection_preserves_capability_defaults(
    model: Model,
    thinking_format: str,
    max_tokens_field: str,
    strict: bool,
    long_cache: bool,
) -> None:
    compat = resolve_openai_compat(model)

    assert compat.thinking_format == thinking_format
    assert compat.max_tokens_field == max_tokens_field
    assert compat.supports_strict_mode is strict
    assert compat.supports_long_cache_retention is long_cache


def test_catalog_compat_fields_override_detected_defaults_individually() -> None:
    model = _model("zai")
    model.compat = {
        "thinkingFormat": "catalog-owned",
        "supportsStore": True,
    }

    compat = resolve_openai_compat(model)

    assert compat.thinking_format == "catalog-owned"
    assert compat.supports_store is True
    assert compat.max_tokens_field == "max_tokens"
