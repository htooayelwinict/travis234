from __future__ import annotations

from travis.ai.catalog_generation import apply_openrouter_capabilities


def test_openrouter_capability_refresh_is_model_agnostic() -> None:
    catalog = {
        "openrouter": {
            "vendor/alpha": {"id": "vendor/alpha", "contextWindow": 32_000, "maxTokens": 4_096},
            "vendor/beta": {"id": "vendor/beta", "contextWindow": 64_000, "maxTokens": 8_192},
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
        ]
    }

    refreshed, changed = apply_openrouter_capabilities(catalog, payload)

    assert changed == 2
    assert refreshed["openrouter"]["vendor/alpha"]["contextWindow"] == 1_000_000
    assert refreshed["openrouter"]["vendor/alpha"]["maxTokens"] == 64_000
    assert refreshed["openrouter"]["vendor/beta"]["contextWindow"] == 256_000
    assert refreshed["openrouter"]["vendor/beta"]["maxTokens"] == 8_192
    assert refreshed["direct"] == catalog["direct"]
