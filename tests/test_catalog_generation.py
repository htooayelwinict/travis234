from __future__ import annotations

import json
from pathlib import Path

import pytest

from travis.ai.catalog_generation import apply_openrouter_capabilities


def test_generated_openrouter_capacities_match_pinned_pi_fixture() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (root / "tests/fixtures/pi_openrouter_route_capacities.json").read_text(encoding="utf-8")
    )
    generated = json.loads(
        (root / "travis/ai/builtin_models.json").read_text(encoding="utf-8")
    )["openrouter"]
    expected = {model_id: dict(capacities) for model_id, capacities in fixture.items()}
    # Pi preserves route responses whose maximum output equals the entire
    # route window. Travis keeps an existing smaller value or uses the safe
    # 4K fallback so compaction always has positive input capacity.
    safe_output_overrides = {
        "google/gemma-4-31b-it": 8_192,
        "minimax/minimax-m2.5": 4_096,
        "minimax/minimax-m2.7": 4_096,
        "nvidia/nemotron-3-super-120b-a12b:free": 4_096,
        "qwen/qwen-2.5-7b-instruct": 4_096,
        "qwen/qwen3-14b": 4_096,
        "qwen/qwen3-coder:free": 4_096,
        "qwen/qwen3.6-27b": 131_072,
    }
    for model_id, max_tokens in safe_output_overrides.items():
        expected[model_id]["maxTokens"] = max_tokens

    assert {
        model_id: {
            "contextWindow": generated[model_id]["contextWindow"],
            "maxTokens": generated[model_id]["maxTokens"],
        }
        for model_id in fixture
    } == expected


def test_subscription_claude_sampling_flags_are_pinned_to_anthropic_routes() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads(
        (root / "travis/ai/builtin_models.json").read_text(encoding="utf-8")
    )

    restricted = {
        "anthropic": [
            "claude-fable-5",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-sonnet-5",
        ],
        "github-copilot": [
            "claude-opus-4.7",
            "claude-opus-4.8",
            "claude-sonnet-5",
        ],
    }
    for provider, model_ids in restricted.items():
        for model_id in model_ids:
            record = catalog[provider][model_id]
            assert record["api"] == "anthropic-messages"
            assert record["compat"]["supportsTemperature"] is False
            assert record["compat"]["supportsTopP"] is False

    copilot_fable = catalog["github-copilot"]["claude-fable-5"]
    assert copilot_fable["api"] == "openai-completions"
    assert "supportsTemperature" not in copilot_fable["compat"]
    assert "supportsTopP" not in copilot_fable["compat"]


def test_openrouter_capability_refresh_is_model_agnostic() -> None:
    catalog = {
        "openrouter": {
            "vendor/alpha": {"id": "vendor/alpha", "contextWindow": 32_000, "maxTokens": 4_096},
            "vendor/beta": {"id": "vendor/beta", "contextWindow": 64_000, "maxTokens": 8_192},
            "vendor/gamma": {"id": "vendor/gamma", "contextWindow": 32_000, "maxTokens": 4_096},
        },
        "direct": {
            "vendor/alpha": {"id": "vendor/alpha", "contextWindow": 16_000, "maxTokens": 2_048},
        },
    }
    payload = {
        "data": [
            {
                "id": "vendor/alpha",
                "context_length": 1_000_000,
                "top_provider": {"context_length": 128_000, "max_completion_tokens": 64_000},
            },
            {
                "id": "vendor/beta",
                "context_length": 256_000,
                "top_provider": {"context_length": 32_000, "max_completion_tokens": None},
            },
            {
                "id": "vendor/gamma",
                "context_length": 96_000,
                "top_provider": {"context_length": None, "max_completion_tokens": 16_000},
            },
        ]
    }

    refreshed, changed = apply_openrouter_capabilities(catalog, payload)

    assert changed == 3
    assert refreshed["openrouter"]["vendor/alpha"]["contextWindow"] == 128_000
    assert refreshed["openrouter"]["vendor/alpha"]["maxTokens"] == 64_000
    assert refreshed["openrouter"]["vendor/beta"]["contextWindow"] == 32_000
    assert refreshed["openrouter"]["vendor/beta"]["maxTokens"] == 8_192
    assert refreshed["openrouter"]["vendor/gamma"]["contextWindow"] == 96_000
    assert refreshed["openrouter"]["vendor/gamma"]["maxTokens"] == 16_000
    assert refreshed["direct"] == catalog["direct"]


def test_openrouter_refresh_rejects_output_limit_at_or_above_route_window() -> None:
    catalog = {
        "openrouter": {
            "vendor/invalid": {
                "id": "vendor/invalid",
                "contextWindow": 32_000,
                "maxTokens": 4_096,
            }
        }
    }
    payload = {
        "data": [
            {
                "id": "vendor/invalid",
                "context_length": 1_000_000,
                "top_provider": {
                    "context_length": 32_000,
                    "max_completion_tokens": 64_000,
                },
            }
        ]
    }

    with pytest.warns(RuntimeWarning, match="vendor/invalid.*64000.*32000"):
        refreshed, changed = apply_openrouter_capabilities(catalog, payload)

    assert changed == 0
    assert refreshed is catalog


def test_openrouter_refresh_repairs_invalid_existing_output_limit() -> None:
    catalog = {
        "openrouter": {
            "vendor/invalid": {
                "id": "vendor/invalid",
                "contextWindow": 32_000,
                "maxTokens": 32_000,
            }
        }
    }
    payload = {
        "data": [
            {
                "id": "vendor/invalid",
                "top_provider": {
                    "context_length": 32_000,
                    "max_completion_tokens": 32_000,
                },
            }
        ]
    }

    with pytest.warns(RuntimeWarning, match="vendor/invalid"):
        refreshed, changed = apply_openrouter_capabilities(catalog, payload)

    assert changed == 1
    assert refreshed["openrouter"]["vendor/invalid"]["maxTokens"] == 4_096
