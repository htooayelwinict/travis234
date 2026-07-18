from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import travis.cli as cli
from travis.ai.auth import ModelAuth, ModelsError, OAuthAuth, ProviderAuth
from travis.ai.models import Models, Provider, ProviderStreams
from travis.ai.providers.faux import faux_model
from travis.ai.types import Model
from travis.app import CodingApp
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.config import ENV_AGENT_DIR
from travis.coding_agent.package_manager import DefaultPackageManager


def _write_extension_tool(path: Path, name: str = "extension_tool") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "from travis.agent.types import AgentToolResult",
                "from travis.ai.types import TextContent",
                "from travis.coding_agent.tools.types import ToolDefinition",
                "",
                "def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):",
                "    return AgentToolResult(content=[TextContent(text='ok')], details=None)",
                "",
                "def extension(travis):",
                "    travis.register_tool(ToolDefinition(",
                f"        name={name!r},",
                f"        label={name!r},",
                "        description='operator extension tool',",
                "        parameters={'type': 'object', 'properties': {}},",
                "        execute=execute,",
                "    ))",
            ]
        ),
        encoding="utf-8",
    )


def _write_extension_flags(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "def extension(travis):",
                "    travis.register_flag('profile', {'type': 'string'})",
                "    travis.register_flag('verbose', {'type': 'boolean'})",
            ]
        ),
        encoding="utf-8",
    )


def _write_skill(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {name} instructions\n---\nUse {name} carefully.",
        encoding="utf-8",
    )


def test_cli_forwards_repeatable_tool_resource_and_offline_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    extension = tmp_path / "operator" / "extension.py"
    skill = tmp_path / "operator" / "skill" / "SKILL.md"
    prompt = tmp_path / "operator" / "review.md"
    theme = tmp_path / "operator" / "night.json"
    _write_extension_tool(extension)
    _write_skill(skill, "operator-skill")
    prompt.write_text("Review $ARGUMENTS", encoding="utf-8")
    theme.write_text('{"name":"night","colors":{}}', encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.session = SimpleNamespace(
                get_known_tool_names=lambda: ["read", "bash", "grep", "extension_tool"]
            )

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "run_print_mode", lambda *_args: 23)

    exit_code = cli.main(
        [
            "--cwd",
            str(project),
            "--no-session",
            "--mode",
            "print",
            "--tools",
            "read,bash",
            "--tools",
            "grep,extension_tool",
            "--exclude-tools",
            "bash",
            "--extension",
            str(extension),
            "--skill",
            str(skill),
            "--prompt-template",
            str(prompt),
            "--theme",
            str(theme),
            "--offline",
            "inspect",
        ]
    )

    assert exit_code == 23
    assert captured["allowed_tool_names"] == ["read", "bash", "grep", "extension_tool"]
    assert captured["excluded_tool_names"] == ["bash"]
    assert captured["additional_extension_paths"] == [str(extension.resolve())]
    assert captured["additional_skill_paths"] == [str(skill.resolve())]
    assert captured["additional_prompt_template_paths"] == [str(prompt.resolve())]
    assert captured["additional_theme_paths"] == [str(theme.resolve())]
    assert captured["offline"] is True
    assert captured["closed"] is True


def test_cli_no_tools_disables_all_tools_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.session = SimpleNamespace(get_known_tool_names=lambda: ["read", "bash"])

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "run_print_mode", lambda *_args: 0)

    assert cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--no-session",
            "--mode",
            "print",
            "--no-tools",
            "inspect",
        ]
    ) == 0

    assert captured["allowed_tool_names"] == []
    assert captured["excluded_tool_names"] == []


def test_coding_app_applies_allowlist_then_denylist_with_explicit_extension(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "operator" / "extension.py"
    _write_extension_tool(extension)

    app = CodingApp(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        allowed_tool_names=["read", "bash", "extension_tool"],
        excluded_tool_names=["bash"],
        additional_extension_paths=[str(extension)],
    )
    try:
        assert app.session.get_active_tool_names() == ["read", "extension_tool"]
        assert {tool["name"] for tool in app.session.get_all_tools()} == {
            "read",
            "extension_tool",
        }
        assert app.session.get_tool_definition("bash") is None
        assert app.session.agent.state.system_prompt == app.session.system_prompt
    finally:
        app.close()


def test_coding_app_applies_extension_flags_to_initial_and_replacement_sessions(
    tmp_path: Path,
) -> None:
    from travis.coding_agent.resource_loader import DefaultResourceLoader

    project = tmp_path / "project"
    project.mkdir()
    extension = tmp_path / "operator" / "flags.py"
    _write_extension_flags(extension)
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        additional_extension_paths=[str(extension)],
    )
    loader.reload({"projectTrustOverride": False})

    app = CodingApp(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        additional_extension_paths=[str(extension)],
        initial_resource_loader=loader,
        extension_flag_values={"profile": "security", "verbose": True},
    )
    try:
        assert app.session.extension_runner.get_flag("profile") == "security"
        assert app.session.extension_runner.get_flag("verbose") is True

        replacement = app._create_runtime_session({"cwd": str(project)})
        try:
            assert replacement.session.extension_runner.get_flag("profile") == "security"
            assert replacement.session.extension_runner.get_flag("verbose") is True
        finally:
            replacement.session.dispose()
    finally:
        app.close()


def test_session_replacement_invalidates_captured_extension_api(tmp_path: Path) -> None:
    from travis.coding_agent.resource_loader import DefaultResourceLoader

    captured: list[object] = []
    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[lambda api: captured.append(api)],
    )
    loader.reload({"projectTrustOverride": False})
    old_api = captured[-1]

    app = CodingApp(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        initial_resource_loader=loader,
    )
    try:
        app.new_session()

        with pytest.raises(RuntimeError, match="stale"):
            old_api.get_commands()
    finally:
        app.close()


def test_replacement_missing_cli_flag_schema_keeps_current_session(tmp_path: Path) -> None:
    from travis.coding_agent.extensions import ExtensionFlagValidationError
    from travis.coding_agent.resource_loader import DefaultResourceLoader

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[
            lambda runner: runner.register_flag("profile", {"type": "string"})
        ],
    )
    loader.reload({"projectTrustOverride": False})
    app = CodingApp(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        initial_resource_loader=loader,
        extension_flag_values={"profile": "security"},
    )
    current = app.session
    try:
        with pytest.raises(ExtensionFlagValidationError, match="Unknown option: --profile"):
            app.new_session()
        assert app.session is current
    finally:
        app.close()


def test_cli_rejects_unknown_tool_after_extensions_load_before_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    closed: list[bool] = []

    class FakeApp:
        def __init__(self, **_kwargs):
            self.session = SimpleNamespace(get_known_tool_names=lambda: ["read", "bash"])

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(
        cli,
        "run_print_mode",
        lambda *_args: (_ for _ in ()).throw(AssertionError("provider turn started")),
    )

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--no-session",
                "--mode",
                "print",
                "--tools",
                "missing_tool",
                "inspect",
            ]
        )

    assert closed == [True]
    assert "unknown tool name: missing_tool" in capsys.readouterr().err


def test_cli_rejects_missing_explicit_resource_before_app_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("app started")),
    )

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--no-session",
                "--mode",
                "print",
                "--skill",
                "missing/SKILL.md",
                "inspect",
            ]
        )

    assert "skill path does not exist" in capsys.readouterr().err


def test_explicit_skill_is_temporary_and_untrusted_project_skill_stays_blocked(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    project_skill = project / ".travis234" / "skills" / "project" / "SKILL.md"
    operator_skill = tmp_path / "operator" / "SKILL.md"
    _write_skill(project_skill, "project-skill")
    _write_skill(operator_skill, "operator-skill")

    app = CodingApp(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        additional_skill_paths=[str(operator_skill)],
    )
    try:
        skills = app.session._resource_loader.get_skills()["skills"]
        assert [skill.name for skill in skills] == [
            "operator-skill",
            "subagent-delegation",
            "web-search",
        ]
        assert skills[0].source_info.source == "local"
        assert skills[0].source_info.scope == "temporary"
        assert skills[0].source_info.origin == "top-level"
    finally:
        app.close()


def test_offline_models_skip_catalog_and_oauth_refresh(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "example": {
                    "type": "oauth",
                    "access": "stale",
                    "refresh": "refresh-token",
                    "expires": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    catalog_refreshes = 0
    oauth_refreshes = 0

    def refresh_catalog():
        nonlocal catalog_refreshes
        catalog_refreshes += 1
        return []

    def refresh_oauth(credential):
        nonlocal oauth_refreshes
        oauth_refreshes += 1
        return credential

    model = Model(
        id="example",
        name="Example",
        api="faux",
        provider="example",
        base_url="https://example.invalid/v1",
    )
    unused_stream = lambda *_args, **_kwargs: None
    runtime = Models(credentials=AuthStorage.create(auth_path), offline=True)
    runtime.set_provider(
        Provider(
            id="example",
            auth=ProviderAuth(
                oauth=OAuthAuth(
                    name="Example",
                    login=lambda _callbacks: {},
                    refresh=refresh_oauth,
                    to_auth=lambda credential: ModelAuth(api_key=str(credential["access"])),
                )
            ),
            models=[model],
            api=ProviderStreams(stream=unused_stream, stream_simple=unused_stream),
            refresh_models=refresh_catalog,
        )
    )

    runtime.refresh()
    with pytest.raises(ModelsError, match="offline mode"):
        runtime.get_auth(model)

    assert catalog_refreshes == 0
    assert oauth_refreshes == 0
    assert json.loads(auth_path.read_text(encoding="utf-8"))["example"]["access"] == "stale"


def test_offline_package_manager_allows_local_and_blocks_network_sources(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(
        json.dumps({"name": "local-package", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "project"),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
        offline=True,
    )

    installed = manager.install(str(source), scope="temporary")
    with pytest.raises(RuntimeError, match="offline mode"):
        manager.install("remote-package==1.0", scope="global")

    assert installed.install_path == str(source.resolve())
