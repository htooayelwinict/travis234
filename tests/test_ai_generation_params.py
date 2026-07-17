import pytest
from types import MappingProxyType

from travis.ai.providers.params import (
    GENERATION_PARAM_FIELDS,
    GenerationParams,
    compact_generation_params_display,
    generation_params_from_session_mapping,
    generation_params_to_mapping,
    merge_generation_params,
    params_from_mapping,
    remove_generation_param,
    replace_generation_param,
)


def test_params_from_mapping_parses_supported_values_and_records_sources():
    params = params_from_mapping(
        {
            "temperature": "0.2",
            "top_p": "0.95",
            "max_tokens": "4096",
            "timeout_seconds": "30.5",
            "frequency_penalty": "-0.25",
            "presence_penalty": "1.5",
            "seed": "12345",
            "parallel_tool_calls": "true",
            "tool_choice": "auto",
            "stop": '["END", "\\n\\n"]',
            "provider_sort": "latency",
        },
        source="cli",
    )

    assert params.temperature == 0.2
    assert params.top_p == 0.95
    assert params.max_tokens == 4096
    assert params.timeout_seconds == 30.5
    assert params.frequency_penalty == -0.25
    assert params.presence_penalty == 1.5
    assert params.seed == 12345
    assert params.parallel_tool_calls is True
    assert params.tool_choice == "auto"
    assert params.stop == ("END", "\n\n")
    assert params.provider_sort == "latency"
    assert dict(params.sources) == {
        "temperature": "cli",
        "top_p": "cli",
        "max_tokens": "cli",
        "timeout_seconds": "cli",
        "frequency_penalty": "cli",
        "presence_penalty": "cli",
        "seed": "cli",
        "parallel_tool_calls": "cli",
        "tool_choice": "cli",
        "stop": "cli",
        "provider_sort": "cli",
    }


def test_blank_none_and_null_values_are_unset():
    params = params_from_mapping(
        {
            "temperature": "",
            "top_p": " ",
            "max_tokens": None,
            "timeout_seconds": "none",
            "frequency_penalty": "None",
            "presence_penalty": "null",
            "seed": "NULL",
            "parallel_tool_calls": "",
            "tool_choice": None,
            "stop": " null ",
            "provider_sort": "",
        },
        source="env",
    )

    assert params.temperature is None
    assert params.top_p is None
    assert params.max_tokens is None
    assert params.timeout_seconds is None
    assert params.frequency_penalty is None
    assert params.presence_penalty is None
    assert params.seed is None
    assert params.parallel_tool_calls is None
    assert params.tool_choice is None
    assert params.stop == ()
    assert params.provider_sort is None
    assert dict(params.sources) == {}


def test_comma_separated_stop_lists_parse():
    params = params_from_mapping({"stop": "END, DONE ,HALT"}, source="cli")

    assert params.stop == ("END", "DONE", "HALT")
    assert dict(params.sources) == {"stop": "cli"}


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"temperature": "2.1"}, "temperature"),
        ({"top_p": "1.1"}, "top_p"),
        ({"max_tokens": "0"}, "max_tokens"),
        ({"max_tokens": "-1"}, "max_tokens"),
    ],
)
def test_invalid_ranges_are_rejected(values, field):
    with pytest.raises(ValueError, match=field):
        params_from_mapping(values, source="cli")


def test_merge_generation_params_prefers_later_sources_and_preserves_earlier_fields():
    env_params = params_from_mapping(
        {"temperature": "0.1", "max_tokens": "1024"},
        source="env",
    )
    cli_params = params_from_mapping(
        {"temperature": "0.7", "top_p": "0.95"},
        source="cli",
    )

    merged = merge_generation_params(env_params, cli_params)

    assert merged.temperature == 0.7
    assert merged.top_p == 0.95
    assert merged.max_tokens == 1024
    assert dict(merged.sources) == {
        "temperature": "cli",
        "top_p": "cli",
        "max_tokens": "env",
    }


def test_merge_generation_params_treats_empty_stop_as_unset():
    env_params = params_from_mapping({"stop": "END"}, source="env")
    cli_params = GenerationParams(stop=(), sources={"stop": "cli"})

    merged = merge_generation_params(env_params, cli_params)

    assert merged.stop == ("END",)
    assert dict(merged.sources) == {"stop": "env"}


def test_merge_generation_params_preserves_provider_preferences():
    params = merge_generation_params(
        GenerationParams(provider_preferences={"sort": "latency"}),
        GenerationParams(provider_preferences={"sort": "throughput"}),
    )

    assert params.provider_preferences == {"sort": "throughput"}
    assert isinstance(params.provider_preferences, MappingProxyType)


def test_generation_params_constructor_normalizes_stop_string_as_single_sequence():
    params = GenerationParams(stop="END")

    assert params.stop == ("END",)


def test_generation_params_constructor_rejects_non_string_stop_entries():
    with pytest.raises(ValueError, match="stop entries must be strings"):
        GenerationParams(stop=["END", 3])


def test_params_from_mapping_does_not_source_empty_stop_list():
    params = params_from_mapping({"stop": "[]"}, source="env")

    assert params.stop == ()
    assert dict(params.sources) == {}


def test_compact_generation_params_display_has_default_fallback():
    assert compact_generation_params_display(GenerationParams()) == "default generation parameters"


def test_compact_generation_params_display_is_secret_free_and_formats_plan_case():
    params = merge_generation_params(
        params_from_mapping({"temperature": "0.2"}, source="cli"),
        params_from_mapping({"top_p": "0.95"}, source="env"),
        params_from_mapping(
            {
                "max_tokens": "4096",
                "stop": '["END"]',
                "provider_sort": "latency",
                "api_key": "sk-secret",
            },
            source="",
        ),
    )

    display = compact_generation_params_display(params)

    assert display == (
        "temperature=0.2 (cli), top_p=0.95 (env), "
        "max_tokens=4096, stop=1 sequence, provider_sort=latency"
    )
    assert "sk-secret" not in display


def test_session_mapping_round_trip_is_normalized_and_source_labeled():
    params = generation_params_from_session_mapping(
        {
            "temperature": 0.2,
            "parallel_tool_calls": True,
            "stop": ["END", "STOP"],
        }
    )

    assert params is not None
    assert generation_params_to_mapping(params) == {
        "temperature": 0.2,
        "parallel_tool_calls": True,
        "stop": ["END", "STOP"],
    }
    assert dict(params.sources) == {
        "temperature": "session",
        "parallel_tool_calls": "session",
        "stop": "session",
    }


@pytest.mark.parametrize(
    "values",
    [
        None,
        [],
        {"api_key": "sk-secret"},
        {"temperature": None},
        {"stop": []},
        {"temperature": "not-a-number"},
    ],
)
def test_invalid_session_snapshot_is_rejected(values):
    assert generation_params_from_session_mapping(values) is None


def test_empty_session_snapshot_is_a_valid_full_reset():
    params = generation_params_from_session_mapping({})

    assert params == GenerationParams()
    assert generation_params_to_mapping(params) == {}


def test_generation_param_fields_expose_only_safe_override_fields():
    assert GENERATION_PARAM_FIELDS == (
        "temperature",
        "top_p",
        "max_tokens",
        "timeout_seconds",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "parallel_tool_calls",
        "tool_choice",
        "stop",
        "provider_sort",
    )
    assert "provider_preferences" not in GENERATION_PARAM_FIELDS


def test_replace_and_remove_generation_param_keep_only_explicit_fields():
    params = replace_generation_param(GenerationParams(), "temperature", "0.2")
    params = replace_generation_param(params, "stop", '["END", "STOP"]')

    assert generation_params_to_mapping(remove_generation_param(params, "temperature")) == {
        "stop": ["END", "STOP"]
    }


@pytest.mark.parametrize("value", ["", "none", "null", None])
def test_replace_requires_explicit_reset_for_unset_values(value):
    with pytest.raises(ValueError, match=r"/params reset temperature"):
        replace_generation_param(GenerationParams(), "temperature", value)


def test_replace_and_remove_reject_unknown_generation_parameters():
    with pytest.raises(ValueError, match="unsupported generation parameter: api_key"):
        replace_generation_param(GenerationParams(), "api_key", "sk-secret")
    with pytest.raises(ValueError, match="unsupported generation parameter: api_key"):
        remove_generation_param(GenerationParams(), "api_key")
