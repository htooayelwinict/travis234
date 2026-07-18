from __future__ import annotations

import asyncio
from pathlib import Path

from tests._support_coding_agent import AgentSession, faux_model
from travis.coding_agent.event_bus import create_event_bus
from travis.coding_agent.agent_session_services import create_agent_session_services
from travis.coding_agent.project_trust import ProjectTrustContext, ProjectTrustStore
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
    project_command = result["runtime"].get_registered_command("extension-version:1")

    assert loaded_paths == {str(global_extension.resolve()), str(project_extension.resolve())}
    assert result["errors"] == []
    assert command is not None
    assert command.description == "version global"
    assert project_command is not None
    assert project_command.description == "version project"


def test_resource_loader_does_not_load_untrusted_project_extensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    _write_extension(project / ".travis234" / "extensions" / "project.py", "project")

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), project_trusted=False)
    loader.reload()

    assert loader.get_extensions()["extensions"] == []
    assert loader.get_extensions()["runtime"].get_registered_command("extension-version") is None


def test_resource_loader_never_executes_unknown_project_extension(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    sentinel = tmp_path / "project-extension-executed"
    project_extension = project / ".travis234" / "extensions" / "project.py"
    project_extension.parent.mkdir(parents=True)
    project_extension.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')",
                "def extension(travis):",
                "    return None",
            ]
        ),
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload()

    assert loader.project_trusted is False
    assert not sentinel.exists()
    assert loader.get_extensions()["extensions"] == []


def test_resource_loader_uses_saved_trust_before_loading_project_extensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    project_extension = project / ".travis234" / "extensions" / "project.py"
    _write_extension(project_extension, "project")
    ProjectTrustStore(agent_dir).set(project, True)

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload({"projectTrustContext": ProjectTrustContext(False, None)})

    assert loader.project_trusted is True
    assert {entry["path"] for entry in loader.get_extensions()["extensions"]} == {
        str(project_extension.resolve())
    }


def test_bootstrap_extension_can_approve_before_project_extension_executes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    global_extension = agent_dir / "extensions" / "trust.py"
    global_extension.parent.mkdir(parents=True)
    global_extension.write_text(
        "\n".join(
            [
                "def extension(travis):",
                "    travis.on(",
                "        'project_trust',",
                "        lambda event, context: {'trusted': 'yes', 'remember': False},",
                "    )",
            ]
        ),
        encoding="utf-8",
    )
    sentinel = tmp_path / "project-extension-executed"
    project_extension = project / ".travis234" / "extensions" / "project.py"
    project_extension.parent.mkdir(parents=True)
    project_extension.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')",
                "def extension(travis):",
                "    return None",
            ]
        ),
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload({"projectTrustContext": ProjectTrustContext(False, None)})

    assert loader.project_trusted is True
    assert sentinel.read_text(encoding="utf-8") == "executed"
    assert {entry["path"] for entry in loader.get_extensions()["extensions"]} == {
        str(global_extension.resolve()),
        str(project_extension.resolve()),
    }


def test_resource_loader_marks_resource_free_project_safe_without_prompt(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload({"projectTrustContext": ProjectTrustContext(False, None)})

    assert loader.project_trusted is True


def test_resource_loader_reuses_shared_event_bus_for_extension_runtime(tmp_path: Path) -> None:
    event_bus = create_event_bus()
    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        event_bus=event_bus,
    )

    loader.reload({"projectTrustContext": ProjectTrustContext(False, None)})

    assert loader.get_extensions()["runtime"].events is event_bus


def test_extension_context_reads_project_trust_dynamically(tmp_path: Path) -> None:
    settings = SettingsManager.in_memory()
    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=False,
    )
    loader.reload()
    runner = loader.get_extensions()["runtime"]
    AgentSession(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        resource_loader=loader,
        extension_runner=runner,
        settings_manager=settings,
    )
    context = runner.create_context()

    assert context.is_project_trusted() is False
    settings.set_project_trusted(True)
    assert context.is_project_trusted() is True


def test_agent_session_services_forward_top_level_project_trust_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    project_extension = project / ".travis234" / "extensions" / "project.py"
    _write_extension(project_extension, "project")

    services = create_agent_session_services(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "projectTrustOverride": True,
        }
    )

    assert services["resourceLoader"].project_trusted is True
    assert {entry["path"] for entry in services["resourceLoader"].get_extensions()["extensions"]} == {
        str(project_extension.resolve())
    }


def test_session_reload_replaces_runtime_with_fresh_extension_code(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    extension_path = project / ".travis234" / "extensions" / "version.py"
    _write_extension(extension_path, "one")
    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), project_trusted=True)
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


def test_extension_factory_api_preserves_source_and_expires_on_reload(tmp_path: Path) -> None:
    captured: list[object] = []

    def extension_factory(travis) -> None:
        captured.append(travis)

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )
    loader.reload()
    old_api = captured[-1]
    old_events = old_api.events
    old_api.register_command("late", {"handler": lambda *_args: None})
    command = loader.get_extensions()["runtime"].get_registered_command("late")

    assert command is not None
    assert command.source_info.path == "<inline:1>"

    loader.reload()

    try:
        old_api.register_command("stale", {"handler": lambda *_args: None})
        assert False, "expected an extension API captured before reload to become stale"
    except RuntimeError as error:
        assert "stale" in str(error)
    try:
        old_events.emit("stale", None)
        assert False, "expected an event bus captured before reload to become stale"
    except RuntimeError as error:
        assert "stale" in str(error)


def test_extension_handler_failure_reports_its_source_path(tmp_path: Path) -> None:
    extension_path = tmp_path / "extension.py"
    extension_path.write_text(
        "def extension(travis):\n"
        "    def explode(event, context):\n"
        "        raise RuntimeError('source probe')\n"
        "    travis.on('session_start', explode)\n",
        encoding="utf-8",
    )
    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
        additional_extension_paths=[str(extension_path)],
    )
    loader.reload()
    runner = loader.get_extensions()["runtime"]
    errors: list[dict[str, object]] = []
    runner.on_error(errors.append)

    runner.emit({"type": "session_start", "reason": "startup"})

    assert errors == [
        {
            "extensionPath": str(extension_path),
            "event": "session_start",
            "error": "source probe",
        }
    ]


def test_extension_factory_session_action_fails_before_binding(tmp_path: Path) -> None:
    def extension_factory(travis) -> None:
        travis.send_message({"customType": "probe", "content": "too early"})

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )

    loader.reload()

    assert loader.get_extensions()["errors"] == [
        {
            "path": "<inline:1>",
            "error": "Extension session action 'send_message' is unavailable before the session is bound",
        }
    ]


def test_queued_provider_failure_isolated_with_extension_source(tmp_path: Path) -> None:
    def extension_factory(travis) -> None:
        travis.register_provider(
            "broken-provider",
            {
                "baseUrl": "https://provider.example.test",
                "apiKey": "test-key",
                "models": [{"id": "broken", "name": "Broken"}],
            },
        )

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )
    loader.reload()
    runner = loader.get_extensions()["runtime"]
    errors: list[dict[str, object]] = []

    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        resource_loader=loader,
        extension_runner=runner,
    )
    runner.on_error(errors.append)

    assert session.extension_runner is runner
    assert errors == [
        {
            "extensionPath": "<inline:1>",
            "event": "register_provider",
            "error": 'Provider broken-provider, model broken: no "api" specified.',
        }
    ]
