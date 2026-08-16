from __future__ import annotations

import json
from pathlib import Path

from travis.coding_agent import DefaultResourceLoader, SettingsManager
from travis.coding_agent.capabilities import CapabilityKind
from travis.coding_agent.package_manager import DefaultPackageManager


def _write_role(path: Path, name: str, description: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "description": description}), encoding="utf-8")


def test_loader_discovers_global_roles_and_trusted_project_overrides(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent = tmp_path / "agent"
    project.mkdir()
    _write_role(agent / "roles" / "reviewer.json", "reviewer", "global")
    _write_role(project / ".travis234" / "roles" / "reviewer.json", "reviewer", "project")

    untrusted = DefaultResourceLoader(
        cwd=str(project), agent_dir=str(agent), project_trusted=False
    )
    untrusted.reload()
    assert untrusted.get_agent_roles().get("reviewer").description == "global"

    trusted = DefaultResourceLoader(
        cwd=str(project), agent_dir=str(agent), project_trusted=True
    )
    trusted.reload()
    role = trusted.get_agent_roles().get("reviewer")
    assert role is not None
    assert role.description == "project"
    resolution = trusted.get_capability_snapshot().resolve(
        CapabilityKind.AGENT_ROLE, "reviewer"
    )
    assert [candidate.source.scope for candidate in resolution.candidates] == [
        "project",
        "global",
    ]


def test_invalid_role_is_a_provenanced_diagnostic_without_file_contents(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent = tmp_path / "agent"
    project.mkdir()
    invalid = agent / "roles" / "broken.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text('{"name":"secret-role","description":"DO_NOT_LEAK"', encoding="utf-8")
    loader = DefaultResourceLoader(
        cwd=str(project), agent_dir=str(agent), project_trusted=False
    )

    loader.reload()

    diagnostics = [
        item
        for item in loader.get_capability_snapshot().diagnostics
        if item.code == "invalid_agent_role"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].source is not None
    assert diagnostics[0].source.path == str(invalid.resolve())
    assert "DO_NOT_LEAK" not in diagnostics[0].message


def test_package_manifest_resolves_only_json_role_files(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    _write_role(package / "roles" / "worker.json", "worker")
    (package / "package.json").write_text(
        json.dumps({"name": "roles", "travis": {"roles": ["roles/worker.json"]}}),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory()
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )

    manager.install(str(package), scope="global")
    resolved = manager.resolve()

    assert [Path(item.path).name for item in resolved.roles] == ["worker.json"]


def test_role_reload_replaces_snapshot_atomically(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent = tmp_path / "agent"
    project.mkdir()
    path = agent / "roles" / "worker.json"
    _write_role(path, "worker", "one")
    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent), project_trusted=False)
    loader.reload()
    first = loader.get_capability_snapshot()

    _write_role(path, "worker", "two")
    loader.reload()
    second = loader.get_capability_snapshot()

    assert second.generation == first.generation + 1
    assert loader.get_agent_roles().get("worker").description == "two"


def test_loader_discovers_bounded_builtin_coordination_planner(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=False,
    )

    loader.reload()

    planner = loader.get_agent_roles().get("coordination-planner")
    assert planner is not None
    assert planner.source.source == "builtin"
    assert planner.source.scope == "builtin"
    assert planner.source.origin == "package"
    assert planner.model_role == "reviewer"
    assert planner.allowed_tools == ("read", "grep", "find", "ls")
    assert planner.allowed_effects == ("read",)
    assert planner.can_spawn is False
    assert planner.default_timeout_seconds == 120
    assert planner.artifact_policy == "none"


def test_trusted_project_coordination_planner_overrides_builtin_candidate(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    project_role = project / ".travis234" / "roles" / "coordination-planner.json"
    _write_role(project_role, "coordination-planner", "trusted project planner")
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
    )

    loader.reload()

    planner = loader.get_agent_roles().get("coordination-planner")
    assert planner is not None
    assert planner.description == "trusted project planner"
    resolution = loader.get_capability_snapshot().resolve(
        CapabilityKind.AGENT_ROLE, "coordination-planner"
    )
    assert [candidate.source.scope for candidate in resolution.candidates] == [
        "project",
        "builtin",
    ]


def test_no_agent_roles_omits_builtin_coordination_planner(tmp_path: Path) -> None:
    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=False,
        no_agent_roles=True,
    )

    loader.reload()

    assert loader.get_agent_roles().get("coordination-planner") is None
