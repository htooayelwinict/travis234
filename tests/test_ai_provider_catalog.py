from __future__ import annotations

from travis.ai.providers.catalog import (
    get_provider,
    get_provider_profile,
    normalize_provider,
    provider_catalog,
    provider_catalog_by_slug,
    tab_for_auth_type,
)

EXPECTED_TRAVIS_PROVIDER_ORDER = [
    "nous",
    "openrouter",
    "moa",
    "novita",
    "lmstudio",
    "anthropic",
    "openai-codex",
    "openai-api",
    "alibaba",
    "xai-oauth",
    "xiaomi",
    "tencent-tokenhub",
    "nvidia",
    "copilot",
    "copilot-acp",
    "huggingface",
    "gemini",
    "deepseek",
    "xai",
    "zai",
    "kimi-coding",
    "kimi-coding-cn",
    "stepfun",
    "minimax",
    "minimax-oauth",
    "minimax-cn",
    "ollama-cloud",
    "arcee",
    "gmi",
    "kilocode",
    "opencode-zen",
    "opencode-go",
    "azure-foundry",
    "qwen-oauth",
    "alibaba-coding-plan",
    "custom",
]


def test_provider_catalog_matches_travis_provider_universe() -> None:
    slugs = {descriptor.slug for descriptor in provider_catalog()}

    assert "openai" not in slugs
    assert {
        "nous",
        "openrouter",
        "moa",
        "novita",
        "lmstudio",
        "anthropic",
        "openai-codex",
        "openai-api",
        "alibaba-coding-plan",
        "alibaba",
        "xai-oauth",
        "xiaomi",
        "tencent-tokenhub",
        "nvidia",
        "copilot",
        "copilot-acp",
        "huggingface",
        "gemini",
        "deepseek",
        "xai",
        "zai",
        "kimi-coding",
        "kimi-coding-cn",
        "stepfun",
        "minimax",
        "minimax-oauth",
        "minimax-cn",
        "ollama-cloud",
        "arcee",
        "azure-foundry",
        "custom",
        "gmi",
        "kilocode",
        "opencode-zen",
        "opencode-go",
        "qwen-oauth",
    }.issubset(slugs)
    assert "bedrock" not in slugs


def test_provider_catalog_order_matches_travis_provider_catalog() -> None:
    assert [descriptor.slug for descriptor in provider_catalog()] == EXPECTED_TRAVIS_PROVIDER_ORDER


def test_travis_bare_openai_alias_routes_to_openrouter_not_direct_openai() -> None:
    assert normalize_provider("openai") == "openrouter"

    provider = get_provider("openai")

    assert provider is not None
    assert provider.id == "openrouter"
    assert provider.base_url == "https://openrouter.ai/api/v1"


def test_qwen_alias_routes_to_travis_qwen_oauth_profile() -> None:
    profile = get_provider_profile("qwen")

    assert profile is not None
    assert profile.name == "qwen-oauth"
    assert profile.auth_type == "oauth_external"
    assert profile.base_url == "https://portal.qwen.ai/v1"
    assert profile.get_max_tokens("qwen3-coder-next") == 65536


def test_kimi_alias_uses_travis_kimi_coding_provider() -> None:
    provider = get_provider("kimi")

    assert provider is not None
    assert provider.id == "kimi-coding"
    assert provider.base_url == "https://api.moonshot.ai/v1"


def test_deepseek_profile_emits_travis_thinking_wire_shape() -> None:
    profile = get_provider_profile("deepseek")

    assert profile is not None
    extra_body, top_level = profile.build_api_kwargs_extras(
        model="deepseek-reasoner",
        reasoning_config={"enabled": True, "effort": "high"},
    )

    assert extra_body == {"thinking": {"type": "enabled"}}
    assert top_level == {"reasoning_effort": "high"}


def test_custom_profile_defaults_to_large_ollama_output_limit() -> None:
    profile = get_provider_profile("custom")

    assert profile is not None
    assert profile.get_max_tokens("local-model") == 65536


def test_travis_full_provider_resolution_prefers_user_config_before_aliases() -> None:
    from travis.ai.providers.catalog import resolve_provider_full

    provider = resolve_provider_full(
        "qwen",
        user_providers={
            "qwen": {
                "name": "Private Qwen Proxy",
                "api": "https://qwen-proxy.example/v1",
                "key_env": "PRIVATE_QWEN_API_KEY",
                "transport": "anthropic_messages",
            }
        },
    )

    assert provider is not None
    assert provider.id == "qwen"
    assert provider.name == "Private Qwen Proxy"
    assert provider.transport == "anthropic_messages"
    assert provider.api_key_env_vars == ("PRIVATE_QWEN_API_KEY",)
    assert provider.base_url == "https://qwen-proxy.example/v1"
    assert provider.source == "user-config"


def test_travis_custom_provider_resolution_uses_canonical_custom_slug() -> None:
    from travis.ai.providers.catalog import resolve_provider_full

    provider = resolve_provider_full(
        "Local Lab",
        custom_providers=[
            {
                "name": "Local Lab",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_env": "LOCAL_LAB_KEY",
                "api_mode": "chat_completions",
            }
        ],
    )

    assert provider is not None
    assert provider.id == "custom:local-lab"
    assert provider.name == "Local Lab"
    assert provider.transport == "openai_chat"
    assert provider.api_key_env_vars == ("LOCAL_LAB_KEY",)
    assert provider.base_url == "http://127.0.0.1:1234/v1"
    assert provider.source == "custom-provider"


def test_travis_runtime_resolution_binds_profile_base_url_and_api_mode() -> None:
    from travis.ai.providers.catalog import resolve_provider_runtime

    runtime = resolve_provider_runtime(
        "openai-api",
        fallback_base_url="https://openrouter.ai/api/v1",
    )

    assert runtime.provider == "openai-api"
    assert runtime.profile.name == "openai-api"
    assert runtime.api_mode == "codex_responses"
    assert runtime.base_url == "https://api.openai.com/v1"
    assert runtime.endpoint_path == "/responses"


def test_travis_overlay_base_url_overrides_profile_base_url_for_nous() -> None:
    from travis.ai.providers.catalog import resolve_provider_runtime

    provider = get_provider("nous")
    runtime = resolve_provider_runtime("nous")

    assert provider is not None
    assert provider.base_url == "https://inference-api.nousresearch.com/v1"
    assert runtime.base_url == "https://inference-api.nousresearch.com/v1"


def test_travis_runtime_resolution_uses_provider_base_url_env_var(monkeypatch) -> None:
    from travis.ai.providers.catalog import resolve_provider_runtime

    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-proxy.example/v1")

    runtime = resolve_provider_runtime(
        "openai-api",
        fallback_base_url="https://openrouter.ai/api/v1",
    )

    assert runtime.provider == "openai-api"
    assert runtime.api_mode == "codex_responses"
    assert runtime.base_url == "https://openai-proxy.example/v1"


def test_travis_routing_aggregator_distinguishes_first_party_resellers() -> None:
    from travis.ai.providers.catalog import is_aggregator, is_routing_aggregator

    assert is_aggregator("openrouter") is True
    assert is_routing_aggregator("openrouter") is True
    assert is_aggregator("opencode-zen") is True
    assert is_routing_aggregator("opencode-zen") is False


def test_travis_tab_for_auth_type_routes_aws_sdk_to_keys() -> None:
    assert tab_for_auth_type("api_key") == "keys"
    assert tab_for_auth_type("aws_sdk") == "keys"
    assert tab_for_auth_type("oauth_external") == "accounts"
    assert tab_for_auth_type("oauth_device_code") == "accounts"
    assert tab_for_auth_type("external_process") == "accounts"


def test_bedrock_is_not_advertised_without_an_implemented_transport() -> None:
    assert "bedrock" not in provider_catalog_by_slug()


def test_direct_anthropic_api_key_uses_native_auth_headers() -> None:
    profile = get_provider_profile("anthropic")
    assert profile is not None

    headers = profile.auth_headers("test-key")

    assert headers["x-api-key"] == "test-key"
    assert headers["anthropic-version"]
    assert "Authorization" not in headers


def test_copilot_descriptor_matches_travis_provider_catalog() -> None:
    copilot = provider_catalog_by_slug()["copilot"]

    assert copilot.label == "GitHub Copilot"
    assert copilot.auth_type == "api_key"
    assert copilot.tab == "keys"
    assert copilot.api_key_env_vars[:2] == ("COPILOT_GITHUB_TOKEN", "GH_TOKEN")


def test_minimax_oauth_descriptor_matches_travis_provider_catalog() -> None:
    minimax = provider_catalog_by_slug()["minimax-oauth"]

    assert minimax.auth_type == "oauth_minimax"
    assert minimax.tab == "accounts"


def test_provider_catalog_uses_travis_canonical_labels_when_profile_metadata_is_blank() -> None:
    by_slug = provider_catalog_by_slug()

    assert by_slug["alibaba"].label == "Qwen Cloud"
    assert by_slug["gemini"].label == "Google AI Studio"
    assert by_slug["kilocode"].label == "Kilo Code"


def test_travis_runtime_detection_handles_kimi_coding_and_bedrock_urls() -> None:
    from travis.ai.providers.catalog import determine_api_mode

    assert determine_api_mode("custom", "https://api.kimi.com/coding") == "anthropic_messages"
    assert (
        determine_api_mode(
            "custom",
            "https://bedrock-runtime.us-east-1.amazonaws.com",
        )
        == "bedrock_converse"
    )


def test_travis_bare_custom_provider_falls_back_to_first_custom_provider() -> None:
    from travis.ai.providers.catalog import resolve_provider_full

    provider = resolve_provider_full(
        "custom",
        custom_providers=[
            {
                "name": "Local Lab",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_env": "LOCAL_LAB_KEY",
            }
        ],
    )

    assert provider is not None
    assert provider.id == "custom:local-lab"
    assert provider.name == "Local Lab"
    assert provider.base_url == "http://127.0.0.1:1234/v1"
