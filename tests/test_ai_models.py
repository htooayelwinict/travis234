from __future__ import annotations

from travis.ai.models import (
    Models,
    Provider,
    calculate_cost,
    clamp_thinking_level,
    get_supported_thinking_levels,
    models_are_equal,
)
from travis.ai.builtin_models import load_builtin_models
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.ai.types import Cost, CostTier, Model


def _registry() -> ModelRegistry:
    return ModelRegistry.in_memory(AuthStorage.in_memory())


def _model() -> Model:
    return Model(
        id="m1",
        name="M1",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        cost=Cost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
        context_window=128000,
        max_tokens=8192,
    )


def test_register_and_lookup() -> None:
    registry = _registry()
    registry.replace_all([])
    m = _model()
    registry.register_model(m)
    assert registry.find("openrouter", "m1") is m
    assert registry.runtime.get_models("openrouter") == (m,)
    assert registry.runtime.get_provider("openrouter") is not None


def test_get_unknown_model_returns_none() -> None:
    assert _registry().find("openrouter", "missing") is None


def test_calculate_cost_per_million_tokens() -> None:
    m = _model()
    cost = calculate_cost(m, {"input": 1_000_000, "output": 500_000, "cache_read": 2_000_000, "cache_write": 0})
    assert cost.input == 1.0
    assert cost.output == 1.0
    assert cost.cache_read == 1.0
    assert cost.total == 3.0


def test_calculate_cost_uses_the_highest_matching_request_tier() -> None:
    model = _model()
    model.cost.tiers = [
        CostTier(input_tokens_above=100_000, input=3.0, output=6.0, cache_read=0.3, cache_write=3.75),
        CostTier(input_tokens_above=250_000, input=5.0, output=10.0, cache_read=0.5, cache_write=6.25),
    ]

    cost = calculate_cost(
        model,
        {"input": 200_000, "output": 100_000, "cache_read": 50_000, "cache_write": 1},
    )

    assert cost.input == 1.0
    assert cost.output == 1.0
    assert cost.cache_read == 0.025
    assert cost.cache_write == 0.00000625


def test_calculate_cost_prices_long_cache_writes_at_twice_the_input_rate() -> None:
    model = _model()
    model.cost = Cost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)

    cost = calculate_cost(
        model,
        {"input": 0, "output": 0, "cache_read": 0, "cache_write": 400_000, "cache_write_1h": 400_000},
    )

    assert cost.cache_write == 2.4
    assert cost.total == 2.4


def test_get_supported_thinking_levels_matches_travis234_reasoning_capabilities() -> None:
    non_reasoning = _model()
    assert get_supported_thinking_levels(non_reasoning) == ["off"]

    reasoning = _model()
    reasoning.reasoning = True
    assert get_supported_thinking_levels(reasoning) == ["off", "minimal", "low", "medium", "high"]

    restricted = _model()
    restricted.reasoning = True
    restricted.thinking_level_map = {"off": None, "minimal": None, "low": None, "xhigh": "max"}

    assert get_supported_thinking_levels(restricted) == ["medium", "high", "xhigh"]


def test_max_thinking_level_is_opt_in_and_clamps_across_an_unsupported_level() -> None:
    model = _model()
    model.reasoning = True
    model.thinking_level_map = {"xhigh": None, "max": "max"}

    assert get_supported_thinking_levels(model) == ["off", "minimal", "low", "medium", "high", "max"]
    assert clamp_thinking_level(model, "xhigh") == "max"


def test_builtin_model_catalog_preserves_request_pricing_tiers() -> None:
    model = next(
        candidate
        for candidate in load_builtin_models()
        if candidate.provider == "openai" and candidate.id == "gpt-5.6-luna"
    )

    assert [(tier.input_tokens_above, tier.input, tier.output) for tier in model.cost.tiers] == [
        (272_000, 2.0, 9.0)
    ]


def test_clamp_thinking_level_uses_nearest_supported_travis234_level() -> None:
    model = _model()
    model.reasoning = True
    model.thinking_level_map = {"off": None, "minimal": None, "low": None, "xhigh": "max"}

    assert clamp_thinking_level(model, "off") == "medium"
    assert clamp_thinking_level(model, "low") == "medium"
    assert clamp_thinking_level(model, "xhigh") == "xhigh"
    assert clamp_thinking_level(model, "invalid") == "medium"


def test_get_api_key_and_headers_merges_provider_model_and_auth_header() -> None:
    registry = _registry()
    registry.register_provider(
        "proxy",
        {"apiKey": "literal-key", "headers": {"X-Provider": "provider"}, "authHeader": True},
    )
    model = Model(
        id="m",
        name="M",
        api="faux",
        provider="proxy",
        base_url="",
        headers={"X-Model": "model"},
    )

    registry.register_model(model)
    result = registry.get_api_key_and_headers(model)

    assert result == {
        "ok": True,
        "apiKey": "literal-key",
        "headers": {
            "X-Model": "model",
            "X-Provider": "provider",
            "Authorization": "Bearer literal-key",
        },
    }


def test_get_api_key_and_headers_reports_missing_auth_header_key() -> None:
    registry = _registry()
    registry.register_provider("proxy", {"authHeader": True})
    model = Model(id="m", name="M", api="faux", provider="proxy", base_url="")

    registry.register_model(model)
    result = registry.get_api_key_and_headers(model)

    assert result == {"ok": False, "error": "API key auth failed for provider proxy"}


def test_get_api_key_and_headers_uses_separate_model_request_headers() -> None:
    registry = _registry()
    registry.register_provider(
        "proxy",
        {
            "apiKey": "literal-key",
            "baseUrl": "https://proxy.invalid/v1",
            "api": "faux",
            "headers": {"X-Shared": "provider", "X-Provider": "provider"},
            "modelOverrides": {
                "m": {"headers": {"X-Shared": "model", "X-Model": "model"}}
            },
            "models": [
                {
                    "id": "m",
                    "name": "M",
                    "headers": {"X-Base": "base"},
                }
            ],
        },
    )
    resolved = registry.find("proxy", "m")
    assert resolved is not None
    result = registry.get_api_key_and_headers(resolved)

    assert result == {
        "ok": True,
        "apiKey": "literal-key",
        "headers": {
            "X-Base": "base",
            "X-Shared": "model",
            "X-Provider": "provider",
            "X-Model": "model",
        },
    }


def test_get_api_key_and_headers_resolves_env_templates_and_uppercase_literals(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_TOKEN", "secret")
    registry = _registry()
    registry.register_provider(
        "proxy",
        {
            "apiKey": "key-$PROXY_TOKEN",
            "headers": {
                "X-Template": "Bearer ${PROXY_TOKEN}",
                "X-Literal": "OPENROUTER",
            },
        },
    )
    model = Model(id="m", name="M", api="faux", provider="proxy", base_url="")

    registry.register_model(model)
    result = registry.get_api_key_and_headers(model)

    assert result == {
        "ok": True,
        "apiKey": "key-secret",
        "headers": {"X-Template": "Bearer secret", "X-Literal": "OPENROUTER"},
    }


def test_get_api_key_and_headers_resolves_command_api_key_on_each_request(monkeypatch) -> None:
    registry = _registry()
    registry.register_provider("proxy", {"apiKey": "!printenv PROXY_TOKEN", "authHeader": True})
    model = Model(id="m", name="M", api="faux", provider="proxy", base_url="")
    registry.register_model(model)

    monkeypatch.setenv("PROXY_TOKEN", "token-1")
    first = registry.get_api_key_and_headers(model)
    monkeypatch.setenv("PROXY_TOKEN", "token-2")
    second = registry.get_api_key_and_headers(model)

    assert first["headers"] == {"Authorization": "Bearer token-1"}
    assert second["headers"] == {"Authorization": "Bearer token-2"}


def test_get_api_key_and_headers_does_not_execute_shell_metacharacters(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run_without_shell(args, **kwargs):
        calls.append((args, kwargs))
        return type("Completed", (), {"returncode": 0, "stdout": "safe\n"})()

    monkeypatch.setattr("travis.coding_agent.resolve_config_value.subprocess.run", run_without_shell)
    provider = "command-auth-metacharacter-test"
    registry = _registry()
    registry.register_provider(provider, {"apiKey": "!printf safe; printf hacked", "authHeader": True})
    model = Model(id="m", name="M", api="faux", provider=provider, base_url="")
    registry.register_model(model)

    result = registry.get_api_key_and_headers(model)

    assert result["headers"] == {"Authorization": "Bearer safe"}
    assert calls[0][0] == ["printf", "safe;", "printf", "hacked"]
    assert calls[0][1]["shell"] is False


def test_get_provider_display_name_resolves_registered_oauth_built_in_and_fallback() -> None:
    registry = _registry()
    assert registry.get_provider_display_name("openai") == "OpenAI"
    assert registry.get_provider_display_name("github-copilot") == "GitHub Copilot"
    assert registry.get_provider_display_name("unknown-provider") == "unknown-provider"

    registry.register_provider(
        "named-provider",
        {"name": "Named Provider", "apiKey": "test-key"},
    )
    assert registry.get_provider_display_name("named-provider") == "Named Provider"

    registry.register_provider(
        "oauth-provider",
        {
            "name": "OAuth Provider",
            "oauth": {
                "name": "OAuth Provider",
                "login": lambda _callbacks: {},
                "refreshToken": lambda credential: credential,
                "getApiKey": lambda credential: credential.get("access", ""),
            },
        },
    )
    assert registry.get_provider_display_name("oauth-provider") == "OAuth Provider"


def test_ai_package_exports_provider_owned_runtime() -> None:
    from travis.ai import (
        Models as ExportedModels,
        Provider as ExportedProvider,
        calculate_cost,
        clamp_thinking_level,
        get_supported_thinking_levels,
        models_are_equal,
    )

    model = _model()
    other_same = _model()
    other_provider = _model()
    other_provider.provider = "other"

    assert ExportedModels is Models
    assert ExportedProvider is Provider
    assert get_supported_thinking_levels(model) == ["off"]
    assert clamp_thinking_level(model, "high") == "off"
    assert models_are_equal(model, other_same) is True
    assert models_are_equal(model, other_provider) is False
    assert models_are_equal(model, None) is False
    assert calculate_cost(model, {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}).input == 1.0
