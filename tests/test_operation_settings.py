from __future__ import annotations

from travis.coding_agent.settings_manager import InMemorySettingsStorage, SettingsManager


GIB = 1024 * 1024 * 1024


def _settings(global_value: object, project_value: object, *, trusted: bool = True):
    return SettingsManager(
        InMemorySettingsStorage(),
        {"operations": global_value},
        {"operations": project_value},
        project_trusted=trusted,
    )


def test_operations_default_to_observe_only() -> None:
    assert SettingsManager.in_memory().get_operation_settings() == {
        "mode": "observe",
        "maxBytes": GIB,
    }


def test_global_user_can_disable_or_raise_capacity() -> None:
    assert _settings(
        {"mode": "disabled", "maxBytes": GIB * 2}, {}
    ).get_operation_settings() == {"mode": "disabled", "maxBytes": GIB * 2}


def test_trusted_project_can_only_lower_capacity_and_cannot_disable() -> None:
    settings = _settings(
        {"mode": "observe", "maxBytes": GIB},
        {"mode": "disabled", "maxBytes": GIB // 2},
    )

    assert settings.get_operation_settings() == {
        "mode": "observe",
        "maxBytes": GIB // 2,
    }


def test_project_cannot_raise_capacity_or_enable_disabled_global_mode() -> None:
    settings = _settings(
        {"mode": "disabled", "maxBytes": 1024},
        {"mode": "observe", "maxBytes": 2048},
    )

    assert settings.get_operation_settings() == {"mode": "disabled", "maxBytes": 1024}


def test_untrusted_project_operations_are_ignored() -> None:
    settings = _settings(
        {"mode": "observe", "maxBytes": 4096},
        {"mode": "disabled", "maxBytes": 1},
        trusted=False,
    )

    assert settings.get_operation_settings() == {"mode": "observe", "maxBytes": 4096}


def test_invalid_scoped_values_fall_back_without_weakening_global() -> None:
    global_invalid = _settings(
        {"mode": "other", "maxBytes": True}, {}, trusted=True
    )
    project_invalid = _settings(
        {"mode": "observe", "maxBytes": 4096},
        {"mode": "disabled", "maxBytes": "tiny"},
        trusted=True,
    )

    assert global_invalid.get_operation_settings() == {"mode": "observe", "maxBytes": GIB}
    assert project_invalid.get_operation_settings() == {"mode": "observe", "maxBytes": 4096}
    assert global_invalid.drain_errors()[0]["scope"] == "global"
    assert project_invalid.drain_errors()[0]["scope"] == "project"
