from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Protocol, cast

import pytest

from travis.ai.providers.faux import faux_model
from travis.coding_agent.agent_session_services import (
    _build_session_dependencies,
    create_agent_session_from_services,
)
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_composition import SessionDependencies
from travis.coding_agent.settings_manager import SettingsManager


class _OperationCoordinator:
    def close(self) -> None:
        return None


class _OperationRuntime:
    def for_session(self, _session_id: str, *, diagnostic_sink=None) -> _OperationCoordinator:
        return _OperationCoordinator()


class _ComposedSessionView(Protocol):
    cwd: str
    auth_storage: AuthStorage
    model_registry: ModelRegistry
    operation_runtime: object | None


def _injected_dependencies(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    project = tmp_path / "project"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    settings = SettingsManager.in_memory()
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        settings_manager=settings,
    )
    auth = AuthStorage.create(str(agent_dir / "auth.json"))
    registry = ModelRegistry.create(auth, str(agent_dir / "models.json"))
    catalog = SessionCatalog(str(agent_dir))
    operation_runtime = _OperationRuntime()
    owners = {
        "settings": settings,
        "loader": loader,
        "auth": auth,
        "registry": registry,
        "catalog": catalog,
        "operation_runtime": operation_runtime,
    }
    options: dict[str, object] = {
        "cwd": str(project / ".." / "project"),
        "agentDir": str(agent_dir / ".." / "agent"),
        "settingsManager": settings,
        "resourceLoader": loader,
        "authStorage": auth,
        "modelRegistry": registry,
        "sessionCatalog": catalog,
        "operationRuntime": operation_runtime,
    }
    return options, owners


def test_build_session_dependencies_canonicalizes_paths_and_preserves_owners(tmp_path: Path) -> None:
    options, owners = _injected_dependencies(tmp_path)

    dependencies = _build_session_dependencies(options)

    assert dependencies.cwd == str((tmp_path / "project").resolve())
    assert dependencies.agent_dir == str((tmp_path / "agent").resolve())
    assert dependencies.settings_manager is owners["settings"]
    assert dependencies.resource_loader is owners["loader"]
    assert dependencies.auth_storage is owners["auth"]
    assert dependencies.model_registry is owners["registry"]
    assert dependencies.model_registry.auth_storage is dependencies.auth_storage
    assert dependencies.session_catalog is owners["catalog"]
    assert dependencies.operation_runtime is owners["operation_runtime"]
    assert Path(dependencies.session_path).is_absolute()
    assert dependencies.session_id
    assert dependencies.diagnostics == ()


def test_session_dependencies_are_frozen_slotted_and_round_trip_legacy_shape(tmp_path: Path) -> None:
    options, _owners = _injected_dependencies(tmp_path)
    dependencies = _build_session_dependencies(options)

    with pytest.raises(FrozenInstanceError):
        dependencies.cwd = "replacement"  # type: ignore[misc]
    assert not hasattr(dependencies, "__dict__")

    legacy = dependencies.to_legacy_mapping()

    assert set(legacy) == {
        "cwd",
        "agentDir",
        "settingsManager",
        "resourceLoader",
        "authStorage",
        "modelRegistry",
        "sessionCatalog",
        "sessionPath",
        "sessionId",
        "operationRuntime",
        "diagnostics",
    }
    assert isinstance(legacy["diagnostics"], list)
    round_tripped = SessionDependencies.from_legacy_mapping(legacy)
    assert round_tripped == dependencies
    assert round_tripped.auth_storage is dependencies.auth_storage
    assert round_tripped.model_registry is dependencies.model_registry


def test_build_session_dependencies_rejects_registry_with_different_auth(tmp_path: Path) -> None:
    options, _owners = _injected_dependencies(tmp_path)
    other_auth = AuthStorage.create(str(tmp_path / "other-agent" / "auth.json"))
    options["modelRegistry"] = ModelRegistry.create(
        other_auth,
        str(tmp_path / "other-agent" / "models.json"),
    )

    with pytest.raises(ValueError, match="modelRegistry and authStorage"):
        _build_session_dependencies(options)


def test_create_agent_session_from_services_accepts_typed_dependencies(tmp_path: Path) -> None:
    options, _owners = _injected_dependencies(tmp_path)
    dependencies = _build_session_dependencies(options)

    result = create_agent_session_from_services(
        {"services": dependencies, "model": faux_model()}
    )

    try:
        session = cast(_ComposedSessionView, result.session)
        assert session.cwd == dependencies.cwd
        assert session.auth_storage is dependencies.auth_storage
        assert session.model_registry is dependencies.model_registry
        assert session.operation_runtime is dependencies.operation_runtime
    finally:
        result.session.dispose()
