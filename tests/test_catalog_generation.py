from __future__ import annotations

import json
from pathlib import Path

import pytest

from travis.ai.catalog_generation import (
    CatalogDrift,
    apply_openrouter_capabilities,
    apply_pi_promotions,
    catalog_drift_to_dict,
    compare_pi_catalogs,
    load_promotion_set,
    validate_catalog,
)


def _catalog_record(provider: str, model_id: str, **overrides) -> dict:
    record = {
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        "provider": provider,
        "baseUrl": f"https://{provider}.invalid/v1",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 32_000,
        "maxTokens": 4_096,
    }
    record.update(overrides)
    return record


def test_compare_pi_catalogs_is_stable_and_classifies_drift() -> None:
    current = {
        "direct": {
            "same": _catalog_record("direct", "same", contextWindow=32_000),
            "removed": _catalog_record("direct", "removed"),
        },
        "travis-only": {"local": _catalog_record("travis-only", "local")},
    }
    pi = {
        "direct": {
            "same": _catalog_record("direct", "same", contextWindow=64_000),
            "added": _catalog_record(
                "direct",
                "added",
                compat={"futureCompatFlag": True},
            ),
        },
        "pi-only": {"remote": _catalog_record("pi-only", "remote")},
    }

    first = compare_pi_catalogs(current, pi)
    second = compare_pi_catalogs(current, pi)

    assert first == second
    assert first == tuple(sorted(first, key=lambda item: item.sort_key()))
    assert {item.kind for item in first} == {
        "provider_missing_from_pi",
        "provider_missing_from_travis",
        "model_missing_from_pi",
        "model_missing_from_travis",
        "field_difference",
        "unsupported_compatibility",
    }
    assert any(
        item.kind == "field_difference"
        and item.provider == "direct"
        and item.model == "same"
        and item.field == "contextWindow"
        for item in first
    )
    assert any(
        item.kind == "unsupported_compatibility"
        and item.field == "compat.futureCompatFlag"
        for item in first
    )
    assert [catalog_drift_to_dict(item) for item in first] == [
        catalog_drift_to_dict(item) for item in second
    ]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"provider": []},
        {"provider": {"model": []}},
        {"provider": {"model": {"id": "different"}}},
        {
            "provider": {
                "model": {
                    "id": "model",
                    "provider": "provider",
                    "api": [],
                }
            }
        },
    ],
)
def test_validate_catalog_rejects_malformed_shapes(payload) -> None:
    with pytest.raises(ValueError, match="catalog"):
        validate_catalog(payload)


def test_apply_pi_promotions_changes_only_explicit_scope() -> None:
    current = {
        "direct": {
            "keep": _catalog_record("direct", "keep", contextWindow=32_000),
            "retire": _catalog_record("direct", "retire"),
        }
    }
    pi = {
        "direct": {
            "keep": _catalog_record("direct", "keep", contextWindow=64_000),
            "add": _catalog_record("direct", "add"),
        }
    }
    promotions = load_promotion_set(
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "add",
                    "provider": "direct",
                    "model": "add",
                    "reason": "supported current model",
                    "evidence": "https://provider.invalid/models",
                },
                {
                    "action": "retire",
                    "provider": "direct",
                    "model": "retire",
                    "reason": "retired upstream",
                    "evidence": "https://provider.invalid/deprecations",
                },
            ],
        }
    )

    updated, changed = apply_pi_promotions(current, pi, promotions)

    assert changed == ("add:direct/add", "retire:direct/retire")
    assert updated["direct"]["keep"] == current["direct"]["keep"]
    assert updated["direct"]["add"] == pi["direct"]["add"]
    assert "retire" not in updated["direct"]


def test_apply_pi_promotions_rejects_unavailable_or_unsupported_updates() -> None:
    current = {"direct": {"keep": _catalog_record("direct", "keep")}}
    pi = {
        "direct": {
            "future": _catalog_record(
                "direct",
                "future",
                api="future-api",
                compat={"futureCompatFlag": True},
            )
        }
    }
    promotions = load_promotion_set(
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "add",
                    "provider": "direct",
                    "model": "future",
                    "reason": "not yet supported",
                    "evidence": "https://provider.invalid/models",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unsupported api"):
        apply_pi_promotions(current, pi, promotions)


def test_apply_pi_promotions_rejects_unregistered_provider() -> None:
    current = {"direct": {"keep": _catalog_record("direct", "keep")}}
    pi = {"new-provider": {"m": _catalog_record("new-provider", "m")}}
    promotions = load_promotion_set(
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "add",
                    "provider": "new-provider",
                    "model": "m",
                    "reason": "provider is not registered in Travis234",
                    "evidence": "https://provider.invalid/models",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unsupported provider"):
        apply_pi_promotions(current, pi, promotions)


@pytest.mark.parametrize(
    "payload",
    [
        {"piCommit": "", "promotions": []},
        {"piCommit": "abc", "promotions": []},
        {
            "piCommit": "b" * 40,
            "promotions": [
                {
                    "action": "update",
                    "provider": "direct",
                    "model": "m",
                    "reason": "",
                    "evidence": "http://not-secure.invalid",
                }
            ],
        },
    ],
)
def test_load_promotion_set_requires_auditable_evidence(payload) -> None:
    with pytest.raises(ValueError, match="promotion"):
        load_promotion_set(payload)


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
            "claude-opus-5",
            "claude-sonnet-5",
        ],
        "github-copilot": [
            "claude-opus-4.7",
            "claude-opus-4.8",
            "claude-opus-5",
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


def test_claude_opus_5_catalog_routes_match_current_pi_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads(
        (root / "travis/ai/builtin_models.json").read_text(encoding="utf-8")
    )

    expected_routes = {
        "anthropic": ("claude-opus-5", "anthropic-messages", 128_000),
        "github-copilot": ("claude-opus-5", "anthropic-messages", 64_000),
        "cloudflare-ai-gateway": ("claude-opus-5", "anthropic-messages", 128_000),
        "opencode": ("claude-opus-5", "anthropic-messages", 128_000),
        "openrouter": ("anthropic/claude-opus-5", "openai-completions", 128_000),
        "vercel-ai-gateway": ("anthropic/claude-opus-5", "anthropic-messages", 128_000),
    }
    for provider, (model_id, api, max_tokens) in expected_routes.items():
        record = catalog[provider][model_id]
        assert record["api"] == api
        assert record["reasoning"] is True
        assert record["thinkingLevelMap"]["xhigh"] == "xhigh"
        assert record["thinkingLevelMap"]["max"] == "max"
        assert record["contextWindow"] == 1_000_000
        assert record["maxTokens"] == max_tokens

    bedrock = catalog["amazon-bedrock"]
    assert "anthropic.claude-opus-5" not in bedrock
    for prefix in ("global", "us", "eu", "jp", "au"):
        record = bedrock[f"{prefix}.anthropic.claude-opus-5"]
        assert record["api"] == "bedrock-converse-stream"
        assert record["contextWindow"] == 1_000_000
        assert record["maxTokens"] == 128_000


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
