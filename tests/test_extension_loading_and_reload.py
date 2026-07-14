from __future__ import annotations

import asyncio
from pathlib import Path

from tests._support_coding_agent import AgentSession, faux_model
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.settings_manager import SettingsManager


def _write_extension(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "def extension(travis):",
                "    travis.register_command(",
                "        'extension-version',",
                f"        {{'description': 'version {version}', 'handler': lambda args, ctx: []}},",
                "    )",
            ]
        ),
        encoding="utf-8",
    )


def test_resource_loader_discovers_global_and_trusted_project_extensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    global_extension = agent_dir / "extensions" / "global.py"
    project_extension = project / ".travis234" / "extensions" / "project.py"
    _write_extension(global_extension, "global")
    _write_extension(project_extension, "project")

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), project_trusted=True)
    loader.reload()

    result = loader.get_extensions()
    loaded_paths = {entry["path"] for entry in result["extensions"]}
    command = result["runtime"].get_registered_command("extension-version")

    assert loaded_paths == {str(global_extension.resolve()), str(project_extension.resolve())}
    assert result["errors"] == []
    assert command is not None
    assert command.description == "version project"


def test_resource_loader_does_not_load_untrusted_project_extensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    _write_extension(project / ".travis234" / "extensions" / "project.py", "project")

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), project_trusted=False)
    loader.reload()

    assert loader.get_extensions()["extensions"] == []
    assert loader.get_extensions()["runtime"].get_registered_command("extension-version") is None


def test_session_reload_replaces_runtime_with_fresh_extension_code(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    extension_path = project / ".travis234" / "extensions" / "version.py"
    _write_extension(extension_path, "one")
    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload()
    old_runtime = loader.get_extensions()["runtime"]
    stale_context = old_runtime.create_context()
    session = AgentSession(
        cwd=str(project),
        agent_dir=str(agent_dir),
        model=faux_model(),
        resource_loader=loader,
        extension_runner=old_runtime,
    )

    _write_extension(extension_path, "two")
    session.reload()

    new_runtime = session.extension_runner
    command = new_runtime.get_registered_command("extension-version")
    assert new_runtime is loader.get_extensions()["runtime"]
    assert new_runtime is not old_runtime
    assert command is not None
    assert command.description == "version two"
    try:
        _ = stale_context.cwd
        assert False, "expected a context captured before reload to become stale"
    except RuntimeError as error:
        assert "stale" in str(error)


def test_reload_reloads_extension_paths_from_settings(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    extension_path = tmp_path / "shared-extensions" / "settings.py"
    _write_extension(extension_path, "settings")
    settings = SettingsManager.create(str(project), str(agent_dir))
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        settings_manager=settings,
    )
    loader.reload()
    assert loader.get_extensions()["runtime"].get_registered_command("extension-version") is None

    other_process = SettingsManager.create(str(project), str(agent_dir))
    other_process.set_extension_paths([str(extension_path)])
    other_process.flush()
    loader.reload()

    command = loader.get_extensions()["runtime"].get_registered_command("extension-version")
    assert command is not None
    assert command.description == "version settings"


def test_resource_loader_awaits_async_extension_factories(tmp_path: Path) -> None:
    async def extension_factory(travis) -> None:
        await asyncio.sleep(0)
        travis.register_command(
            "async-ready",
            {"description": "async ready", "handler": lambda args, ctx: []},
        )

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )
    loader.reload()

    result = loader.get_extensions()
    assert result["errors"] == []
    assert result["runtime"].get_registered_command("async-ready") is not None
