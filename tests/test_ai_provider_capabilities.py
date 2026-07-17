import pytest

from travis.ai.providers.capabilities import build_generation_payload
from travis.ai.providers.params import GenerationParams


def test_openrouter_payload_preserves_routing_preferences() -> None:
    payload = build_generation_payload(
        provider="openrouter",
        api_mode="chat_completions",
        params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=4096,
            provider_sort="throughput",
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.2
    assert payload.max_tokens == 4096
    assert payload.provider_preferences == {"sort": "throughput", "allow_fallbacks": True}
    assert payload.request_overrides == {"top_p": 0.9}
    assert payload.warnings == []


def test_anthropic_translates_stop_and_drops_unsupported_penalties() -> None:
    payload = build_generation_payload(
        provider="anthropic",
        api_mode="anthropic_messages",
        params=GenerationParams(
            temperature=0.4,
            top_p=0.8,
            max_tokens=2000,
            stop=("END",),
            frequency_penalty=0.3,
            seed=123,
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.4
    assert payload.max_tokens == 2000
    assert payload.request_overrides == {"top_p": 0.8, "stop_sequences": ["END"]}
    assert [warning.param for warning in payload.warnings] == ["frequency_penalty", "seed"]
    assert all(warning.action == "dropped" for warning in payload.warnings)


def test_codex_responses_uses_only_documented_generation_fields() -> None:
    payload = build_generation_payload(
        provider="openai-codex",
        api_mode="openai_codex_responses",
        params=GenerationParams(
            temperature=0.1,
            top_p=0.95,
            max_tokens=6000,
            stop=("END",),
            frequency_penalty=0.2,
            presence_penalty=0.3,
            seed=7,
            provider_sort="latency",
            parallel_tool_calls=False,
            tool_choice="auto",
        ),
        tools_enabled=True,
    )

    assert payload.temperature is None
    assert payload.max_tokens is None
    assert payload.request_overrides == {
        "parallel_tool_calls": False,
        "tool_choice": "auto",
    }
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("temperature", "dropped"),
        ("top_p", "dropped"),
        ("max_tokens", "dropped"),
        ("stop", "dropped"),
        ("frequency_penalty", "dropped"),
        ("presence_penalty", "dropped"),
        ("seed", "dropped"),
        ("provider_sort", "dropped"),
    ]


@pytest.mark.parametrize("api_mode", ["openai_responses", "azure_openai_responses"])
def test_non_codex_responses_keep_existing_sampling_fields(api_mode: str) -> None:
    payload = build_generation_payload(
        provider="openai" if api_mode == "openai_responses" else "azure-openai-responses",
        api_mode=api_mode,
        params=GenerationParams(temperature=0.1, top_p=0.95, max_tokens=6000),
        tools_enabled=True,
    )

    assert payload.temperature == 0.1
    assert payload.max_tokens == 6000
    assert payload.request_overrides == {"top_p": 0.95}
    assert payload.warnings == []


def test_stepfun_uses_conservative_openai_compatible_policy() -> None:
    payload = build_generation_payload(
        provider="stepfun",
        api_mode="chat_completions",
        params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=8192,
            presence_penalty=0.1,
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.2
    assert payload.max_tokens == 8192
    assert payload.request_overrides == {"top_p": 0.9, "presence_penalty": 0.1}
    assert payload.warnings == []


def test_unknown_api_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported api_mode"):
        build_generation_payload(
            provider="amazon-bedrock",
            api_mode="bedrock_converse",
            params=GenerationParams(top_p=0.9),
            tools_enabled=True,
        )


def test_chat_payload_drops_parallel_tools_when_tools_are_disabled() -> None:
    payload = build_generation_payload(
        provider="openrouter",
        api_mode="chat_completions",
        params=GenerationParams(top_p=0.8, parallel_tool_calls=True),
        tools_enabled=False,
    )

    assert payload.request_overrides == {"top_p": 0.8}
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("parallel_tool_calls", "dropped")
    ]


def test_codex_responses_warns_when_stop_is_not_supported() -> None:
    payload = build_generation_payload(
        provider="openai-codex",
        api_mode="openai_codex_responses",
        params=GenerationParams(stop=("END",)),
        tools_enabled=True,
    )

    assert payload.request_overrides == {}
    assert [(warning.param, warning.action) for warning in payload.warnings] == [("stop", "dropped")]


def test_codex_responses_drops_parallel_tools_when_tools_are_disabled() -> None:
    payload = build_generation_payload(
        provider="openai-codex",
        api_mode="openai_codex_responses",
        params=GenerationParams(parallel_tool_calls=True),
        tools_enabled=False,
    )

    assert payload.request_overrides == {}
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("parallel_tool_calls", "dropped")
    ]


def test_openrouter_merges_explicit_provider_preferences() -> None:
    payload = build_generation_payload(
        provider="openrouter",
        api_mode="chat_completions",
        params=GenerationParams(
            provider_sort="latency",
            provider_preferences={"only": ["Fireworks"], "allow_fallbacks": False},
        ),
        tools_enabled=True,
    )

    assert payload.provider_preferences == {
        "only": ["Fireworks"],
        "allow_fallbacks": False,
        "sort": "latency",
    }


def test_chat_payload_warns_when_provider_sort_is_unsupported() -> None:
    payload = build_generation_payload(
        provider="stepfun",
        api_mode="chat_completions",
        params=GenerationParams(provider_sort="latency"),
        tools_enabled=True,
    )

    assert payload.provider_preferences is None
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("provider_sort", "dropped")
    ]
