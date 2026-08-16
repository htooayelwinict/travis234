from __future__ import annotations

import json
from pathlib import Path

import pytest

from travis.coding_agent.settings_manager import (
    InMemorySettingsStorage,
    SettingsManager,
)


def _settings_with_scopes(
    global_settings: dict,
    project_settings: dict,
    *,
    trusted: bool,
) -> tuple[SettingsManager, InMemorySettingsStorage]:
    storage = InMemorySettingsStorage()
    storage.with_lock(
        "global",
        lambda _current: json.dumps(global_settings, indent=2),
    )
    storage.with_lock(
        "project",
        lambda _current: json.dumps(project_settings, indent=2),
    )
    return (
        SettingsManager.from_storage(storage, {"projectTrusted": trusted}),
        storage,
    )


def test_model_role_reads_project_precedence_and_reports_source() -> None:
    settings, _storage = _settings_with_scopes(
        {"modelRoles": {"worker": "global/worker", "vision": "global/vision"}},
        {"modelRoles": {"worker": "project/worker"}},
        trusted=True,
    )

    assert settings.get_model_roles() == {
        "worker": "project/worker",
        "vision": "global/vision",
    }
    assert settings.get_model_role("worker") == "project/worker"
    assert settings.get_model_role_source("worker") == "project"
    assert settings.get_model_role("vision") == "global/vision"
    assert settings.get_model_role_source("vision") == "global"


def test_clearing_project_role_reveals_global_without_persisting_null() -> None:
    settings, storage = _settings_with_scopes(
        {"modelRoles": {"worker": "global/worker"}},
        {"modelRoles": {"worker": "project/worker"}},
        trusted=True,
    )

    settings.set_project_model_role("worker", None)

    assert settings.get_model_role("worker") == "global/worker"
    assert settings.get_model_role_source("worker") == "global"
    assert storage.project_content is not None
    assert "null" not in storage.project_content
    assert "modelRoles" not in json.loads(storage.project_content)


def test_global_role_setter_trims_values_and_removes_empty_mapping() -> None:
    settings = SettingsManager.in_memory()
    storage = settings.storage
    assert isinstance(storage, InMemorySettingsStorage)

    settings.set_model_role("compression", "  provider/summary:low  ")
    assert settings.get_model_role("compression") == "provider/summary:low"

    settings.set_model_role("compression", None)
    assert settings.get_model_roles() == {}
    assert storage.global_content is not None
    assert "modelRoles" not in json.loads(storage.global_content)


@pytest.mark.parametrize("role", ["", "primary", "unknown role"])
def test_role_writes_reject_unsupported_names(role: str) -> None:
    settings = SettingsManager.in_memory()

    with pytest.raises(ValueError, match="model role"):
        settings.set_model_role(role, "provider/model")


@pytest.mark.parametrize("selector", ["", " ", "\t\n"])
def test_role_writes_reject_blank_selectors(selector: str) -> None:
    settings = SettingsManager.in_memory()

    with pytest.raises(ValueError, match="selector"):
        settings.set_model_role("worker", selector)


def test_project_role_write_requires_trust_before_mutating_memory() -> None:
    settings, storage = _settings_with_scopes({}, {}, trusted=False)
    before = settings.get_project_settings()

    with pytest.raises(RuntimeError, match="not trusted"):
        settings.set_project_model_role("worker", "provider/model")

    assert settings.get_project_settings() == before
    assert storage.project_content == "{}"


def test_untrusted_project_role_does_not_override_global_role() -> None:
    settings = SettingsManager(
        InMemorySettingsStorage(),
        {"modelRoles": {"worker": "global/worker"}},
        {"modelRoles": {"worker": "project/worker"}},
        project_trusted=False,
    )

    assert settings.get_model_role("worker") == "global/worker"
    assert settings.get_model_role_source("worker") == "global"


def test_malformed_hand_edited_model_roles_are_ignored() -> None:
    settings, _storage = _settings_with_scopes(
        {
            "modelRoles": {
                "worker": 42,
                "reviewer": "  ",
                "vision": " provider/vision ",
                "unknown": "provider/unknown",
            }
        },
        {},
        trusted=True,
    )

    assert settings.get_model_roles() == {"vision": "provider/vision"}
    assert settings.get_model_role("worker") is None
    assert settings.get_model_role_source("worker") is None
    assert settings.get_model_role_source("reviewer") is None
    assert settings.get_model_role_source("vision") == "global"


def test_malformed_project_role_does_not_hide_valid_global_role() -> None:
    settings, _storage = _settings_with_scopes(
        {
            "modelRoles": {
                "worker": "global/worker",
                "reviewer": "global/reviewer",
            }
        },
        {"modelRoles": {"worker": 42, "reviewer": "  "}},
        trusted=True,
    )

    assert settings.get_model_roles() == {
        "worker": "global/worker",
        "reviewer": "global/reviewer",
    }
    assert settings.get_model_role_source("worker") == "global"
    assert settings.get_model_role_source("reviewer") == "global"


def test_file_backed_model_roles_survive_global_and_project_reload(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    settings = SettingsManager.create(
        str(project),
        str(agent_dir),
        {"projectTrusted": True},
    )

    settings.set_model_role("worker", "provider/global-worker:low")
    settings.set_project_model_role("reviewer", "provider/project-reviewer:high")

    reloaded = SettingsManager.create(
        str(project),
        str(agent_dir),
        {"projectTrusted": True},
    )
    assert reloaded.get_model_role("worker") == "provider/global-worker:low"
    assert reloaded.get_model_role_source("worker") == "global"
    assert reloaded.get_model_role("reviewer") == "provider/project-reviewer:high"
    assert reloaded.get_model_role_source("reviewer") == "project"
