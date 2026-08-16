from __future__ import annotations

from travis.coding_agent.memory import MemorySettings
from travis.coding_agent.settings_manager import InMemorySettingsStorage, SettingsManager


GIB = 1024 * 1024 * 1024


def _settings(global_value: object, project_value: object, *, trusted: bool = True):
    return SettingsManager(
        InMemorySettingsStorage(),
        {"memory": global_value},
        {"memory": project_value},
        project_trusted=trusted,
    )


def test_default_memory_is_disabled_and_project_scoped() -> None:
    assert SettingsManager.in_memory().get_memory_settings() == MemorySettings()


def test_global_user_can_enable_and_allow_global_scope() -> None:
    settings = _settings(
        {
            "enabled": True,
            "allowedScopes": ["project", "global"],
            "maxFactBytes": 2048,
            "maxFactsPerScope": 50,
            "maxTotalBytes": 4096,
            "recallLimit": 5,
            "recallBytes": 1024,
        },
        {},
    )

    assert settings.get_memory_settings() == MemorySettings(
        enabled=True,
        allowed_scopes=("project", "global"),
        max_fact_bytes=2048,
        max_facts_per_scope=50,
        max_total_bytes=4096,
        recall_limit=5,
        recall_bytes=1024,
    )


def test_project_settings_cannot_enable_or_widen_scopes() -> None:
    disabled = _settings(
        {"enabled": False, "allowedScopes": ["project"]},
        {"enabled": True, "allowedScopes": ["project", "global"]},
    )
    enabled = _settings(
        {"enabled": True, "allowedScopes": ["project"]},
        {"enabled": False, "allowedScopes": ["project", "global"]},
    )

    assert disabled.get_memory_settings().enabled is False
    assert disabled.get_memory_settings().allowed_scopes == ("project",)
    assert enabled.get_memory_settings().enabled is True
    assert enabled.get_memory_settings().allowed_scopes == ("project",)


def test_trusted_project_can_only_lower_numeric_limits() -> None:
    settings = _settings(
        {
            "enabled": True,
            "maxFactBytes": 4096,
            "maxFactsPerScope": 100,
            "maxTotalBytes": 8192,
            "recallLimit": 10,
            "recallBytes": 2048,
        },
        {
            "maxFactBytes": 1024,
            "maxFactsPerScope": 200,
            "maxTotalBytes": 4096,
            "recallLimit": 20,
            "recallBytes": 512,
        },
    )

    value = settings.get_memory_settings()
    assert value.max_fact_bytes == 1024
    assert value.max_facts_per_scope == 100
    assert value.max_total_bytes == 4096
    assert value.recall_limit == 10
    assert value.recall_bytes == 512


def test_untrusted_and_invalid_project_memory_are_ignored_or_bounded() -> None:
    untrusted = _settings(
        {"enabled": True, "maxFactBytes": 4096},
        {"maxFactBytes": 1},
        trusted=False,
    )
    invalid = _settings(
        {"enabled": True, "maxFactBytes": 4096},
        {"maxFactBytes": True, "recallLimit": 0},
    )

    assert untrusted.get_memory_settings().max_fact_bytes == 4096
    assert invalid.get_memory_settings().max_fact_bytes == 4096
    assert invalid.get_memory_settings().recall_limit == MemorySettings().recall_limit
    assert invalid.drain_errors()[0]["scope"] == "project"


def test_invalid_global_memory_falls_back_to_safe_defaults() -> None:
    settings = _settings(
        {
            "enabled": "yes",
            "allowedScopes": ["global", "other"],
            "maxTotalBytes": -1,
        },
        {},
    )

    assert settings.get_memory_settings() == MemorySettings()
    assert settings.drain_errors()[0]["scope"] == "global"
