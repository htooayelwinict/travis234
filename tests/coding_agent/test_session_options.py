from __future__ import annotations

from collections.abc import Mapping

import pytest

from travis.coding_agent.session_options import SessionBootstrapOptions


ALIAS_PAIRS = (
    ("agent_dir", "agentDir"),
    ("settings_manager", "settingsManager"),
    ("session_dir", "sessionDir"),
    ("session_catalog", "sessionCatalog"),
    ("resource_loader", "resourceLoader"),
    ("resource_loader_options", "resourceLoaderOptions"),
    ("resource_loader_reload_options", "resourceLoaderReloadOptions"),
    ("project_trust_override", "projectTrustOverride"),
    ("project_trust_context", "projectTrustContext"),
    ("trust_store", "trustStore"),
    ("auth_storage", "authStorage"),
    ("model_registry", "modelRegistry"),
    ("session_id", "sessionId"),
    ("session_path", "sessionPath"),
    ("extension_flag_values", "extensionFlagValues"),
    ("operation_runtime", "operationRuntime"),
    ("session_factory", "sessionFactory"),
    ("thinking_level", "thinkingLevel"),
    ("scoped_models", "scopedModels"),
    ("is_continuing", "isContinuing"),
    ("model_id", "modelId"),
    ("exclude_tools", "excludeTools"),
    ("convert_to_llm", "convertToLlm"),
    ("parent_session_path", "parentSession"),
    ("session_start_event", "sessionStartEvent"),
    ("defer_session_start", "deferSessionStart"),
    ("model_role_bindings", "modelRoleBindings"),
    ("model_role_event_sink", "modelRoleEventSink"),
    ("custom_tools", "customTools"),
    ("no_tools", "noTools"),
    ("retry_enabled", "retryEnabled"),
    ("max_retries", "maxRetries"),
    ("retry_delay_ms", "retryDelayMs"),
    ("max_retry_delay_ms", "maxRetryDelayMs"),
)


@pytest.mark.parametrize(("snake_name", "camel_name"), ALIAS_PAIRS)
def test_bootstrap_alias_pairs_normalize_to_equal_values(
    snake_name: str,
    camel_name: str,
) -> None:
    value = object()

    from_snake = SessionBootstrapOptions.from_mapping({snake_name: value})
    from_camel = SessionBootstrapOptions.from_mapping({camel_name: value})

    assert getattr(from_snake, snake_name) is value
    assert getattr(from_camel, snake_name) is value


@pytest.mark.parametrize(("snake_name", "camel_name"), ALIAS_PAIRS)
def test_conflicting_bootstrap_aliases_name_both_keys(
    snake_name: str,
    camel_name: str,
) -> None:
    with pytest.raises(ValueError, match=rf"{camel_name}.*{snake_name}|{snake_name}.*{camel_name}"):
        SessionBootstrapOptions.from_mapping(
            {snake_name: "snake", camel_name: "camel"}
        )


def test_bootstrap_options_preserve_known_single_keys_and_read_only_extras() -> None:
    source: dict[str, object] = {
        "cwd": "/workspace",
        "model": object(),
        "provider": "faux",
        "services": object(),
        "tools": ["read"],
        "futureOption": {"enabled": True},
    }
    original = dict(source)

    options = SessionBootstrapOptions.from_mapping(source)

    assert options.cwd == "/workspace"
    assert options.model is source["model"]
    assert options.provider == "faux"
    assert options.services is source["services"]
    assert options.tools is source["tools"]
    assert isinstance(options.extras, Mapping)
    assert options.extras == {"futureOption": {"enabled": True}}
    with pytest.raises(TypeError):
        options.extras["futureOption"] = False  # type: ignore[index]
    assert source == original


def test_bootstrap_options_repr_never_contains_secret_values() -> None:
    options = SessionBootstrapOptions.from_mapping(
        {
            "apiKey": "bootstrap-super-secret",
            "authorization": "Bearer private-token",
            "authStorage": object(),
        }
    )

    rendered = repr(options)

    assert "bootstrap-super-secret" not in rendered
    assert "private-token" not in rendered


def test_bootstrap_options_distinguish_absent_values_from_explicit_none() -> None:
    absent = SessionBootstrapOptions.from_mapping({})
    explicit = SessionBootstrapOptions.from_mapping(
        {"operationRuntime": None, "sessionPath": None, "sessionId": None}
    )

    assert absent.was_provided("operation_runtime") is False
    assert absent.was_provided("session_path") is False
    assert absent.was_provided("session_id") is False
    assert explicit.was_provided("operation_runtime") is True
    assert explicit.was_provided("session_path") is True
    assert explicit.was_provided("session_id") is True
