from __future__ import annotations

from appv231.ai.models import (
    calculate_cost,
    clamp_thinking_level,
    get_api_key_and_headers,
    get_model,
    get_models,
    get_provider_display_name,
    get_providers,
    get_supported_thinking_levels,
    register_model_request_headers,
    register_provider_auth_config,
    register_model,
    reset_models,
)
from appv231.ai.types import Cost, Model


def setup_function() -> None:
    reset_models()


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
    m = _model()
    register_model(m)
    assert get_model("openrouter", "m1") is m
    assert get_models("openrouter") == [m]
    assert get_providers() == ["openrouter"]


def test_get_unknown_model_returns_none() -> None:
    assert get_model("openrouter", "missing") is None


def test_calculate_cost_per_million_tokens() -> None:
    m = _model()
    cost = calculate_cost(m, {"input": 1_000_000, "output": 500_000, "cache_read": 2_000_000, "cache_write": 0})
    assert cost.input == 1.0
    assert cost.output == 1.0
    assert cost.cache_read == 1.0
    assert cost.total == 3.0


def test_get_supported_thinking_levels_matches_pi_reasoning_capabilities() -> None:
    non_reasoning = _model()
    assert get_supported_thinking_levels(non_reasoning) == ["off"]

    reasoning = _model()
    reasoning.reasoning = True
    assert get_supported_thinking_levels(reasoning) == ["off", "minimal", "low", "medium", "high"]

    restricted = _model()
    restricted.reasoning = True
    restricted.thinking_level_map = {"off": None, "minimal": None, "low": None, "xhigh": "max"}

    assert get_supported_thinking_levels(restricted) == ["medium", "high", "xhigh"]


def test_clamp_thinking_level_uses_nearest_supported_pi_level() -> None:
    model = _model()
    model.reasoning = True
    model.thinking_level_map = {"off": None, "minimal": None, "low": None, "xhigh": "max"}

    assert clamp_thinking_level(model, "off") == "medium"
    assert clamp_thinking_level(model, "low") == "medium"
    assert clamp_thinking_level(model, "xhigh") == "xhigh"
    assert clamp_thinking_level(model, "invalid") == "medium"


def test_get_api_key_and_headers_merges_provider_model_and_auth_header() -> None:
    register_provider_auth_config(
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

    result = get_api_key_and_headers(model)

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
    register_provider_auth_config("proxy", {"authHeader": True})
    model = Model(id="m", name="M", api="faux", provider="proxy", base_url="")

    result = get_api_key_and_headers(model)

    assert result == {"ok": False, "error": 'No API key found for "proxy"'}


def test_get_api_key_and_headers_uses_separate_model_request_headers() -> None:
    register_provider_auth_config("proxy", {"headers": {"X-Shared": "provider", "X-Provider": "provider"}})
    register_model_request_headers("proxy", "m", {"X-Shared": "model", "X-Model": "model"})
    model = Model(
        id="m",
        name="M",
        api="faux",
        provider="proxy",
        base_url="",
        headers={"X-Base": "base"},
    )

    result = get_api_key_and_headers(model)

    assert result == {
        "ok": True,
        "apiKey": None,
        "headers": {
            "X-Base": "base",
            "X-Shared": "model",
            "X-Provider": "provider",
            "X-Model": "model",
        },
    }


def test_get_api_key_and_headers_resolves_env_templates_and_uppercase_literals(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_TOKEN", "secret")
    register_provider_auth_config(
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

    result = get_api_key_and_headers(model)

    assert result == {
        "ok": True,
        "apiKey": "key-secret",
        "headers": {"X-Template": "Bearer secret", "X-Literal": "OPENROUTER"},
    }


def test_get_api_key_and_headers_resolves_command_api_key_on_each_request(monkeypatch) -> None:
    register_provider_auth_config("proxy", {"apiKey": "!printenv PROXY_TOKEN", "authHeader": True})
    model = Model(id="m", name="M", api="faux", provider="proxy", base_url="")

    monkeypatch.setenv("PROXY_TOKEN", "token-1")
    first = get_api_key_and_headers(model)
    monkeypatch.setenv("PROXY_TOKEN", "token-2")
    second = get_api_key_and_headers(model)

    assert first["headers"] == {"Authorization": "Bearer token-1"}
    assert second["headers"] == {"Authorization": "Bearer token-2"}


def test_get_api_key_and_headers_does_not_execute_shell_metacharacters() -> None:
    register_provider_auth_config("proxy", {"apiKey": "!printf safe; printf hacked", "authHeader": True})
    model = Model(id="m", name="M", api="faux", provider="proxy", base_url="")

    result = get_api_key_and_headers(model)

    assert result["ok"] is False
    assert "headers" not in result


def test_get_provider_display_name_resolves_registered_oauth_built_in_and_fallback() -> None:
    assert get_provider_display_name("openai") == "OpenAI"
    assert get_provider_display_name("github-copilot") == "GitHub Copilot"
    assert get_provider_display_name("unknown-provider") == "unknown-provider"

    register_provider_auth_config(
        "named-provider",
        {"name": "Named Provider", "apiKey": "test-key"},
    )
    assert get_provider_display_name("named-provider") == "Named Provider"

    register_provider_auth_config(
        "oauth-provider",
        {"oauth": {"name": "OAuth Provider"}},
    )
    assert get_provider_display_name("oauth-provider") == "OAuth Provider"


def test_ai_package_exports_pi_model_helper_aliases() -> None:
    from appv231.ai import (
        calculateCost,
        clampThinkingLevel,
        getModel,
        getModels,
        getProviders,
        getSupportedThinkingLevels,
        modelsAreEqual,
        registerModel,
        resetModels,
    )

    resetModels()
    model = _model()
    other_same = _model()
    other_provider = _model()
    other_provider.provider = "other"

    registerModel(model)

    assert getModel("openrouter", "m1") is model
    assert getModels("openrouter") == [model]
    assert getProviders() == ["openrouter"]
    assert getSupportedThinkingLevels(model) == ["off"]
    assert clampThinkingLevel(model, "high") == "off"
    assert modelsAreEqual(model, other_same) is True
    assert modelsAreEqual(model, other_provider) is False
    assert modelsAreEqual(model, None) is False
    assert calculateCost(model, {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}).input == 1.0
