from __future__ import annotations

from travis.coding_agent.artifact_store import ArtifactLimits
from travis.coding_agent.settings_manager import InMemorySettingsStorage, SettingsManager


def _settings(
    *,
    global_value: object,
    project_value: object,
    trusted: bool,
) -> SettingsManager:
    return SettingsManager(
        InMemorySettingsStorage(),
        {"artifacts": global_value},
        {"artifacts": project_value},
        project_trusted=trusted,
    )


def test_project_artifact_limits_can_lower_but_not_raise_global() -> None:
    settings = _settings(
        global_value={"maxObjectBytes": 1024},
        project_value={"maxObjectBytes": 2048, "maxSessionLogicalBytes": 512},
        trusted=True,
    )

    limits = settings.get_artifact_limits()

    assert limits.max_object_bytes == 1024
    assert limits.max_session_logical_bytes == 512


def test_global_artifact_limits_can_raise_defaults() -> None:
    defaults = ArtifactLimits()
    settings = _settings(
        global_value={
            "maxObjectBytes": defaults.max_object_bytes + 1,
            "maxPhysicalObjects": defaults.max_physical_objects + 1,
        },
        project_value={},
        trusted=True,
    )

    limits = settings.get_artifact_limits()

    assert limits.max_object_bytes == defaults.max_object_bytes + 1
    assert limits.max_physical_objects == defaults.max_physical_objects + 1


def test_invalid_project_value_does_not_hide_valid_global_value() -> None:
    settings = _settings(
        global_value={"maxObjectBytes": 4096, "maxSessionObjects": 25},
        project_value={"maxObjectBytes": "bad", "maxSessionObjects": True},
        trusted=True,
    )

    limits = settings.get_artifact_limits()

    assert limits.max_object_bytes == 4096
    assert limits.max_session_objects == 25


def test_untrusted_project_artifact_limits_are_ignored() -> None:
    settings = _settings(
        global_value={"maxObjectBytes": 4096},
        project_value={"maxObjectBytes": 1},
        trusted=False,
    )

    assert settings.get_artifact_limits().max_object_bytes == 4096
