from __future__ import annotations

import json
import logging
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import travis.cli as cli
from travis.ai.env_config import ModelConfig
from travis.app import CodingApp
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.config import ENV_AGENT_DIR, get_agent_dir
from travis.coding_agent.model_registry import ModelRegistry
from travis.ai.types import Model
from travis.ai.types import UserMessage, now_ms
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from tests._provider_runtime import (
    current_registry,
    register_api_provider,
    register_model,
    reset_api_providers,
    reset_models,
)
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore
from travis.coding_agent.project_trust import ProjectTrustContext
from travis.coding_agent.project_trust import ProjectTrustStore
from travis.coding_agent.settings_manager import FileSettingsStorage, SettingsManager


def setup_function() -> None:
    reset_api_providers()
    reset_models()


def _use_registered_model_runtime(monkeypatch) -> ModelRegistry:
    registry = current_registry()
    monkeypatch.setattr(
        ModelRegistry,
        "create",
        staticmethod(lambda *_args, **_kwargs: registry),
    )
    return registry


def test_cli_installs_first_party_hypa_as_optional_global_extension(monkeypatch, tmp_path, capsys) -> None:
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))

    exit_code = cli.main(["--install-extension", "hypa"])

    installed = agent_dir / "extensions" / "hypa"
    assert exit_code == 0
    assert (installed / "__init__.py").is_file()
    assert (installed / "hypa_tools.py").is_file()
    assert "Installed hypa extension" in capsys.readouterr().out
    assert not (Path(cli.__file__).parent / "coding_agent" / "builtin_extensions").exists()


def test_cli_extension_install_refuses_to_replace_existing_user_code(monkeypatch, tmp_path, capsys) -> None:
    agent_dir = tmp_path / "agent"
    installed = agent_dir / "extensions" / "hypa"
    installed.mkdir(parents=True)
    marker = installed / "__init__.py"
    marker.write_text("# user-owned\n", encoding="utf-8")
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))

    exit_code = cli.main(["--install-extension", "hypa"])

    assert exit_code == 1
    assert marker.read_text(encoding="utf-8") == "# user-owned\n"
    assert "already exists" in capsys.readouterr().err


def test_package_install_dispatches_before_agent_start_and_preserves_source(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    project = tmp_path / "repo"
    package = tmp_path / "package"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "demo", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    monkeypatch.setattr(cli, "CodingApp", lambda **kwargs: (_ for _ in ()).throw(AssertionError("agent started")))

    exit_code = cli.main(["install", str(package), "--cwd", str(project)])

    assert exit_code == 0
    settings = json.loads((agent_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["packages"] == [str(package)]
    assert "Installed" in capsys.readouterr().out


def test_project_package_install_requires_explicit_or_saved_trust(monkeypatch, tmp_path, capsys) -> None:
    project = tmp_path / "repo"
    package = tmp_path / "package"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "demo", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))

    denied = cli.main(
        ["install", str(package), "--local", "--no-approve", "--cwd", str(project)]
    )
    approved = cli.main(
        ["install", str(package), "--local", "--approve", "--cwd", str(project)]
    )

    assert denied == 1
    assert approved == 0
    project_settings = json.loads(
        (project / ".travis234" / "settings.json").read_text(encoding="utf-8")
    )
    assert project_settings["packages"] == [str(package)]
    assert "trusted project" in capsys.readouterr().err


def test_project_package_update_uses_saved_trust_without_prompt(monkeypatch, tmp_path, capsys) -> None:
    project = tmp_path / "repo"
    package = tmp_path / "package"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    package.mkdir()
    manifest = package / "package.json"
    manifest.write_text(
        json.dumps({"name": "demo", "version": "1", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    ProjectTrustStore(agent_dir).set(project, True)
    assert cli.main(["install", str(package), "--local", "--cwd", str(project)]) == 0
    manifest.write_text(
        json.dumps({"name": "demo", "version": "2", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("builtins.input", lambda *_args: (_ for _ in ()).throw(AssertionError("prompted")))

    exit_code = cli.main(["update", "--local", "--cwd", str(project)])

    assert exit_code == 0
    assert "Updated 1 package" in capsys.readouterr().out


def test_cli_json_mode_dispatches_to_machine_transport_without_tui(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured["enable_tui"] = kwargs["enable_tui"]

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(
        cli,
        "run_json_mode",
        lambda app, prompt, output: captured.update(prompt=prompt, output=output) or 19,
    )

    exit_code = cli.main(
        ["--cwd", str(tmp_path), "--no-session", "--mode", "json", "inspect"]
    )

    assert exit_code == 19
    assert captured["prompt"] == "inspect"
    assert captured["enable_tui"] is False
    assert captured["closed"] is True


def test_cli_mode_and_plain_alias_are_mutually_exclusive(tmp_path) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli.main(["--cwd", str(tmp_path), "--mode", "print", "--plain", "inspect"])


def test_cli_rpc_mode_dispatches_stdio_without_interactive_trust(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured["trust_context"] = kwargs["project_trust_context"]
            captured["enable_tui"] = kwargs["enable_tui"]

        def close(self):
            pass

    class FakeRpcServer:
        def __init__(self, app, input, output):
            captured.update(app=app, input=input, output=output)

        def run(self):
            return 29

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "RpcServer", FakeRpcServer)

    exit_code = cli.main(["--cwd", str(tmp_path), "--no-session", "--mode", "rpc"])

    assert exit_code == 29
    assert captured["enable_tui"] is False
    assert captured["trust_context"].has_ui is False


def test_readmes_document_optional_extension_install_and_reload() -> None:
    app_root = Path(__file__).resolve().parents[1]
    readmes = [
        (app_root / "README.md").read_text(encoding="utf-8"),
        (app_root / "packages/travis234-cli/README.md").read_text(encoding="utf-8"),
    ]

    for readme in readmes:
        assert "travis234 --install-extension hypa" in readme
        assert "~/.travis234/agent/extensions/" in readme
        assert ".travis234/extensions/" in readme
        assert "`/reload`" in readme
        assert "Travis JavaScript extensions do not run directly" in readme


def test_readme_documents_process_wait_and_async_user_shell() -> None:
    app_root = Path(__file__).resolve().parents[1]
    readmes = [
        (app_root / "README.md").read_text(encoding="utf-8"),
        (app_root / "packages/travis234-cli/README.md").read_text(encoding="utf-8"),
    ]

    for readme in readmes:
        assert "process.wait" in readme
        assert "does not change the command timeout" in readme
        assert "command is not killed" in readme
        assert "64 MiB per process" in readme
        assert "output_limit" in readme
        assert "!command` and `!!command` run asynchronously" in readme
        assert "cannot reattach a running process after an application restart" in readme


def test_coding_app_plain_mode_does_not_render_live_tui(tmp_path, capsys) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "plain reply")))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), enable_tui=False)
    app.run_turn("hi")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert any(getattr(message, "role", None) == "assistant" for message in app.messages)


def test_cli_without_prompt_starts_interactive_tui(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.closed = False
            created["app"] = self

        def close(self):
            self.closed = True

    class FakeInteractiveMode:
        def __init__(self, app, **kwargs):
            created["mode_app"] = app

        def run(self):
            return 17

    monkeypatch.setattr(
        cli,
        "_model_from_env",
        lambda dotenv_path, **kwargs: Model(id="m", name="m", api="faux", provider="faux", base_url=""),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)

    exit_code = cli.main(["--cwd", str(tmp_path)])

    app = created["app"]
    assert exit_code == 17
    assert created["mode_app"] is app
    assert app.enable_tui is True
    assert app.thinking_level == "off"
    assert app.scoped_models == []
    assert app.closed is True


def _install_session_cli_fakes(monkeypatch, captured: dict[str, object]) -> None:
    class FakeApp:
        def __init__(self, **kwargs):
            captured["app_kwargs"] = dict(kwargs)
            self.cwd = kwargs["cwd"]
            self.messages = []
            self.session = SimpleNamespace(grant_capability=lambda *_args, **_kwargs: None)

        def run_turn(self, prompt):
            captured["prompt"] = prompt

    class FakeInteractiveMode:
        def __init__(self, app, **kwargs):
            captured["mode_app"] = app
            captured["mode_kwargs"] = dict(kwargs)

        def run(self):
            return 23

    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)


def _seed_cli_session(agent_dir: Path, cwd: Path, *, session_id: str = "saved") -> Path:
    cwd.mkdir(parents=True, exist_ok=True)
    catalog = SessionCatalog(str(agent_dir))
    path, resolved_id = catalog.new_session_path(str(cwd), session_id=session_id)
    store = SessionStore(path, cwd=str(cwd.resolve()), session_id=resolved_id)
    store.append_message(UserMessage(content=f"marker-{session_id}", timestamp=now_ms()))
    return Path(path)


def test_cli_trust_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit, match="2"):
        cli.main(["--approve", "--no-approve", "--no-session", "prompt"])


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("--approve", True), ("--no-approve", False)],
)
def test_cli_forwards_trust_override_and_file_backed_settings(
    monkeypatch,
    tmp_path,
    flag: str,
    expected: bool,
) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    exit_code = cli.main(["--cwd", str(tmp_path), flag, "--no-session", "inspect"])

    app_kwargs = captured["app_kwargs"]
    assert exit_code == 0
    assert app_kwargs["project_trust_override"] is expected
    assert isinstance(app_kwargs["project_trust_context"], ProjectTrustContext)
    assert app_kwargs["project_trust_context"].has_ui is False
    assert isinstance(app_kwargs["settings_manager"], SettingsManager)
    assert isinstance(app_kwargs["settings_manager"].storage, FileSettingsStorage)


def test_cli_continue_uses_latest_workspace_session_without_creating_another(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    session_path = _seed_cli_session(agent_dir, project)
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    exit_code = cli.main(["--cwd", str(project), "--continue", "--plain", "inspect"])

    assert exit_code == 0
    assert captured["app_kwargs"]["session_path"] == str(session_path)
    assert captured["app_kwargs"]["session_id"] == "saved"
    assert list(session_path.parent.glob("*.jsonl")) == [session_path]


def test_cli_exact_session_restores_header_cwd_when_cwd_is_not_explicit(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    session_path = _seed_cli_session(agent_dir, project, session_id="exact")
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    exit_code = cli.main(["--session", str(session_path), "--plain", "inspect"])

    assert exit_code == 0
    assert captured["app_kwargs"]["cwd"] == str(project.resolve())
    assert captured["app_kwargs"]["session_path"] == str(session_path)
    assert captured["app_kwargs"]["session_id"] == "exact"


def test_cli_exact_session_keeps_explicit_cwd_override(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    override = tmp_path / "override"
    override.mkdir()
    session_path = _seed_cli_session(agent_dir, project, session_id="override")
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    exit_code = cli.main(
        ["--cwd", str(override), "--session", str(session_path), "--plain", "inspect"]
    )

    assert exit_code == 0
    assert captured["app_kwargs"]["cwd"] == str(override.resolve())
    assert captured["app_kwargs"]["session_path"] == str(session_path)


def test_cli_no_session_starts_ephemerally_without_creating_jsonl(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    exit_code = cli.main(["--cwd", str(project), "--no-session", "--plain", "inspect"])

    assert exit_code == 0
    assert captured["app_kwargs"]["session_path"] is None
    assert captured["app_kwargs"]["session_id"] is None
    assert list(agent_dir.rglob("*.jsonl")) == []


def test_cli_resume_boots_ephemerally_and_opens_tui_picker(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    exit_code = cli.main(["--cwd", str(project), "--resume"])

    assert exit_code == 23
    assert captured["app_kwargs"]["session_path"] is None
    assert captured["app_kwargs"]["session_id"] is None
    assert captured["mode_kwargs"]["open_resume_picker"] is True
    assert list(agent_dir.rglob("*.jsonl")) == []


def test_cli_session_modes_are_mutually_exclusive(monkeypatch, tmp_path, capsys) -> None:
    _install_session_cli_fakes(monkeypatch, {})

    with pytest.raises(SystemExit, match="2"):
        cli.main(["--cwd", str(tmp_path), "--continue", "--no-session"])

    assert "not allowed with argument" in capsys.readouterr().err


def test_cli_continue_without_previous_session_reports_error_without_creating_file(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_session_cli_fakes(monkeypatch, captured)

    with pytest.raises(SystemExit, match="2"):
        cli.main(["--cwd", str(project), "--continue", "--plain"])

    assert "No previous session for this workspace" in capsys.readouterr().err
    assert "app_kwargs" not in captured
    assert list(agent_dir.rglob("*.jsonl")) == []


def test_cli_rejects_missing_cwd_before_starting_app(monkeypatch, tmp_path, capsys) -> None:
    missing_cwd = tmp_path / "missing-project"

    def fail_startup(*args, **kwargs):
        raise AssertionError("invalid cwd should stop before provider/model/app startup")

    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--cwd", str(missing_cwd), "--plain", "pwd"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert f"Error: working directory does not exist: {missing_cwd.resolve()}" in captured.err


def test_cli_provider_and_model_flags_resolve_registered_static_model(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    selected_model = Model(
        id="qwen/qwen3-coder:exacto",
        name="Qwen3 Coder Exacto",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(selected_model)
    _use_registered_model_runtime(monkeypatch)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "qwen/qwen3-coder:exacto",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is selected_model
    assert app.enable_tui is False
    assert app.thinking_level == "off"


def test_cli_model_registry_find_prefers_live_override_over_global_registered_model() -> None:
    registered_model = Model(
        id="openai/gpt-5.4-mini",
        name="Old Registered Metadata",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    register_model(registered_model)
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    registry.replace_all([live_model])

    assert registry.find("openrouter", "openai/gpt-5.4-mini") is live_model


def test_cli_loads_persisted_auth_before_model_selection(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "auth.json").write_text(
        '{"openrouter": {"type": "api_key", "key": "persisted-key"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    monkeypatch.setattr(
        ModelRegistry,
        "create",
        staticmethod(
            lambda auth_storage, models_path, **kwargs: ModelRegistry(
                auth_storage,
                models_path,
                **kwargs,
            )
        ),
    )

    class FakeApp:
        def __init__(
            self,
            *,
            cwd,
            model,
            enable_tui,
            thinking_level,
            scoped_models,
            **kwargs,
        ):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            observed["app"] = self

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    def record_startup(dotenv_path, **kwargs):
        observed["api_key"] = kwargs["model_registry"].get_api_key_for_provider(
            "openrouter"
        )
        return cli._StartupModelSelection(
            model=Model(
                id="qwen/qwen3.6-flash",
                name="qwen/qwen3.6-flash",
                api="faux",
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
            )
        )

    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--plain", "hi"])

    assert exit_code == 0
    assert observed["prompt"] == "hi"
    assert observed["api_key"] == "persisted-key"


def test_travis_config_reads_travis_agent_dir(monkeypatch, tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    monkeypatch.delenv(ENV_AGENT_DIR, raising=False)
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    assert get_agent_dir() == str(agent_dir)


def test_cli_default_dotenv_searches_parent_dirs_for_npm_prefix_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "travis234"
    app_dir.mkdir(parents=True)
    project = repo / "project"
    project.mkdir()
    env_path = repo / ".env"
    env_path.write_text(
        "TRAVIS234_WORKER_LLM_ENABLED=true\nOPENROUTER_API_KEY=test-key\n",
        encoding="utf-8",
    )
    (app_dir / ".env").write_text(
        "TRAVIS234_WORKER_LLM_ENABLED=true\nOPENROUTER_API_KEY=wrong-prefix-key\n",
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            observed["app"] = self

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    def record_startup(dotenv_path, **kwargs):
        observed["startup_dotenv"] = Path(dotenv_path)
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        )

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(project), "--plain", "inspect"])

    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert observed["startup_dotenv"] == env_path


def test_cli_default_cwd_uses_npm_initial_cwd_for_prefix_wrapper(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "travis234"
    app_dir.mkdir(parents=True)
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            observed["app"] = self

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    def record_startup(dotenv_path, **kwargs):
        observed["dotenv"] = Path(dotenv_path)
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        )

    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--plain", "inspect"])

    app = observed["app"]
    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert app.cwd == str(repo.resolve())


def test_cli_explicit_relative_dotenv_uses_npm_initial_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "travis234"
    app_dir.mkdir(parents=True)
    env_path = repo / ".env"
    env_path.write_text("TRAVIS234_WORKER_LLM_ENABLED=true\n", encoding="utf-8")
    (app_dir / ".env").write_text("TRAVIS234_WORKER_LLM_ENABLED=false\n", encoding="utf-8")
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    def record_startup(dotenv_path, **kwargs):
        observed["dotenv"] = Path(dotenv_path)
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        )

    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--dotenv", ".env", "--plain", "inspect"])

    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert observed["dotenv"] == env_path


def test_cli_model_thinking_suffix_sets_initial_thinking_level(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    selected_model = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(selected_model)
    _use_registered_model_runtime(monkeypatch)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--model",
            "anthropic/claude-sonnet-4-5:high",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is selected_model
    assert app.thinking_level == "high"


def test_cli_thinking_flag_overrides_model_suffix(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    selected_model = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(selected_model)
    _use_registered_model_runtime(monkeypatch)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--model",
            "anthropic/claude-sonnet-4-5:low",
            "--thinking",
            "high",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is selected_model
    assert app.thinking_level == "high"


def test_cli_invalid_thinking_level_warns_and_uses_default(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--thinking",
            "turbo",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    captured = capsys.readouterr()
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.thinking_level == "off"
    assert 'Invalid thinking level "turbo"' in captured.err


def test_cli_export_session_file_to_html_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    session_path = tmp_path / "session.jsonl"
    output_path = tmp_path / "session.html"
    called: dict[str, object] = {}

    def fake_export_from_file(input_path, options=None):
        called["input_path"] = input_path
        called["options"] = options
        return str(output_path)

    def fail_startup(*args, **kwargs):
        raise AssertionError("export should not initialize providers or app runtime")

    monkeypatch.setattr(cli, "export_from_file", fake_export_from_file, raising=False)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--export", str(session_path), str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert called == {"input_path": str(session_path), "options": str(output_path)}
    assert captured.out == f"Exported to: {output_path}\n"
    assert captured.err == ""


def test_cli_models_flag_sets_scoped_models_and_initial_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    created: dict[str, object] = {}
    sonnet = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="faux",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )
    qwen = Model(
        id="qwen/qwen3-coder:exacto",
        name="Qwen3 Coder Exacto",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(sonnet)
    register_model(qwen)
    _use_registered_model_runtime(monkeypatch)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "anthropic/claude-sonnet-4-5:high,openrouter/qwen/qwen3-coder:exacto:low",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is sonnet
    assert app.thinking_level == "high"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [
        (sonnet, "high"),
        (qwen, "low"),
    ]


def test_cli_list_models_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--list-models"])

    assert code == 0
    assert "stepfun/step-3.7-flash" in capsys.readouterr().out


def test_cli_list_models_includes_dotenv_model_from_isolated_control_plane(monkeypatch, tmp_path, capsys) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TRAVIS234_WORKER_LLM_ENABLED=true\n"
        "TRAVIS234_WORKER_LLM_PROVIDER=stepfun\n"
        "TRAVIS234_WORKER_LLM_MODEL=step-3.7-flash\n"
        "TRAVIS234_WORKER_LLM_BASE_URL=https://api.stepfun.example/v1\n",
        encoding="utf-8",
    )

    code = cli.main(["--cwd", str(tmp_path), "--dotenv", str(dotenv), "--list-models"])

    assert code == 0
    assert "stepfun/step-3.7-flash" in capsys.readouterr().out


def test_cli_wires_configured_auxiliary_compression_route(monkeypatch, tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TRAVIS234_WORKER_LLM_ENABLED=true\n"
        "TRAVIS234_WORKER_LLM_PROVIDER=openrouter\n"
        "TRAVIS234_WORKER_LLM_MODEL=qwen/qwen3-coder-next\n"
        "TRAVIS234_COMPRESSION_LLM_ENABLED=true\n"
        "TRAVIS234_COMPRESSION_LLM_PROVIDER=stepfun\n"
        "TRAVIS234_COMPRESSION_LLM_MODEL=step-3.7-flash\n"
        "TRAVIS234_COMPRESSION_LLM_BASE_URL=https://summary.example.test/v1\n"
        "TRAVIS234_COMPRESSION_LLM_API_KEY=summary-test-key\n"
        "TRAVIS234_COMPRESSION_LLM_TIMEOUT_SECONDS=17\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = []

        def run_turn(self, prompt):
            captured["prompt"] = prompt

        def close(self):
            return None

    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(["--cwd", str(tmp_path), "--dotenv", str(dotenv), "--plain", "inspect"])

    assert code == 0
    compression_model = captured["compression_model"]
    assert isinstance(compression_model, Model)
    assert compression_model.provider == "stepfun"
    assert compression_model.id == "step-3.7-flash"
    assert compression_model.base_url == "https://summary.example.test/v1"
    assert captured["compression_api_key"] == "summary-test-key"
    assert captured["compression_timeout_seconds"] == 17


def test_startup_model_preserves_explicit_provider_binding(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "TRAVIS234_WORKER_LLM_ENABLED=true",
                "TRAVIS234_WORKER_LLM_PROVIDER=stepfun",
                "TRAVIS234_WORKER_LLM_MODEL=step-3.7-flash",
                "TRAVIS234_WORKER_LLM_CONTEXT_WINDOW=256000",
                "STEPFUN_API_KEY=step-key",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TRAVIS234_WORKER_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TRAVIS234_WORKER_LLM_MODEL", raising=False)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)

    model = cli._model_from_env(dotenv)

    assert model.provider == "stepfun"
    assert model.id == "step-3.7-flash"
    assert model.base_url == "https://api.stepfun.ai/step_plan/v1"
    assert model.context_window == 256_000


def test_dotenv_credentials_are_registered_per_provider(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "OPENROUTER_API_KEY=router-key\nSTEPFUN_API_KEY=step-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())

    secrets = cli._register_dotenv_provider_credentials(registry, dotenv)

    assert registry.auth_storage.get_api_key("openrouter") == "router-key"
    assert registry.auth_storage.get_api_key("stepfun") == "step-key"
    assert set(secrets) >= {"router-key", "step-key"}


def test_dotenv_provider_runtime_survives_model_picker_registry_hydration(tmp_path, monkeypatch) -> None:
    default_url = "https://openrouter.ai/api/v1"
    local_url = "http://127.0.0.1:18765/v1"
    standard = Model(
        id="xiaomi/mimo-v2.5",
        name="MiMo V2.5",
        api="openai-completions",
        provider="openrouter",
        base_url=default_url,
        context_window=32_000,
        max_tokens=4_096,
    )
    pro = Model(
        id="xiaomi/mimo-v2.5-pro",
        name="MiMo V2.5 Pro",
        api="openai-completions",
        provider="openrouter",
        base_url=default_url,
        context_window=1_048_576,
        max_tokens=131_072,
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TRAVIS234_WORKER_LLM_ENABLED=true\n"
        "TRAVIS234_WORKER_LLM_PROVIDER=openrouter\n"
        "TRAVIS234_WORKER_LLM_MODEL=xiaomi/mimo-v2.5-pro\n"
        "TRAVIS234_WORKER_LLM_CONTEXT_WINDOW=256000\n"
        f"TRAVIS234_WORKER_LLM_BASE_URL={local_url}\n"
        "OPENROUTER_API_KEY=local-key\n"
        f"OPENROUTER_BASE_URL={local_url}\n",
        encoding="utf-8",
    )
    for key in (
        "TRAVIS234_WORKER_LLM_PROVIDER",
        "TRAVIS234_WORKER_LLM_MODEL",
        "TRAVIS234_WORKER_LLM_CONTEXT_WINDOW",
        "TRAVIS234_WORKER_LLM_BASE_URL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    registry.replace_model(standard)
    registry.replace_model(pro)

    cli._register_dotenv_provider_credentials(registry, dotenv)
    config = cli.load_model_config("TRAVIS234_WORKER_LLM", dotenv)
    startup = cli._startup_model_from_env(
        dotenv,
        config=config,
        model_registry=registry,
    )

    selected_standard = registry.find("openrouter", standard.id)
    selected_pro = registry.find("openrouter", pro.id)
    assert startup.model.id == pro.id
    assert startup.model.base_url == local_url
    assert startup.model.context_window == 256_000
    assert selected_standard is not None
    assert selected_standard.base_url == local_url
    assert selected_pro is not None
    assert selected_pro.base_url == local_url
    assert selected_pro.context_window == 256_000


def test_cli_list_providers_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--list-providers"])

    assert code == 0
    assert "stepfun" in capsys.readouterr().out.splitlines()


def test_cli_provider_stepfun_model_uses_custom_known_provider(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--provider",
            "stepfun",
            "--model",
            "step-3.7-flash",
            "--plain",
            "noop",
        ]
    )

    assert code == 0
    model = observed["model"]
    assert model.provider == "stepfun"
    assert model.id == "step-3.7-flash"


def test_cli_reads_travis_worker_llm_prefix(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TRAVIS234_WORKER_LLM_ENABLED=true\n"
        "TRAVIS234_WORKER_LLM_MODEL=openai/gpt-5.4-mini\n"
        "TRAVIS234_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1\n",
        encoding="utf-8",
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(["--cwd", str(tmp_path), "--dotenv", str(env_path), "--plain", "inspect"])

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model.provider == "openrouter"
    assert app.model.id == "openai/gpt-5.4-mini"
    assert "TRAVIS234_WORKER_LLM" not in captured.err


def test_cli_generation_flags_are_passed_to_registered_provider(monkeypatch, tmp_path, capsys) -> None:
    observed: dict[str, object] = {}

    def create_registry(auth_storage, models_path, *, provider_config=None):
        observed["config"] = provider_config
        return ModelRegistry(
            auth_storage,
            models_path,
            provider_config=provider_config,
        )

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(ModelRegistry, "create", staticmethod(create_registry))

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "stepfun/step-3.7-flash",
            "--temperature",
            "0.2",
            "--top-p",
            "0.9",
            "--max-tokens",
            "4096",
            "--timeout-seconds",
            "75",
            "--provider-sort",
            "throughput",
            "--stop",
            "END,STOP",
            "--plain",
            "noop",
        ]
    )

    assert code == 0
    params = observed["config"].generation_params
    assert params.temperature == 0.2
    assert params.top_p == 0.9
    assert params.max_tokens == 4096
    assert params.timeout_seconds == 75
    assert params.provider_sort == "throughput"
    assert params.stop == ("END", "STOP")
    assert observed["config"].timeout_seconds == 75
    assert "generation parameter provider_sort dropped" not in capsys.readouterr().err


def test_cli_passes_generation_params_to_interactive_mode(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []
            self.cwd = kwargs["cwd"]
            self.session = type(
                "FakeSession",
                (),
                {
                    "model": kwargs["model"],
                    "thinking_level": kwargs["thinking_level"],
                    "session_name": "test",
                    "subscribe": lambda self, callback: (lambda: None),
                },
            )()
            self.tui = None

    class FakeInteractiveMode:
        def __init__(self, app, *, generation_params=None, **kwargs):
            observed["generation_params"] = generation_params

        def run(self):
            return 0

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "stepfun/step-3.7-flash",
            "--temperature",
            "0.2",
            "--max-tokens",
            "4096",
            "--tui",
        ]
    )

    assert code == 0
    params = observed["generation_params"]
    assert params.temperature == 0.2
    assert params.max_tokens == 4096


def test_cli_generation_merge_preserves_legacy_config_fields_when_flags_absent() -> None:
    config = ModelConfig(
        enabled=True,
        api_key="k",
        model="m",
        base_url="https://example.test/v1",
        timeout_seconds=45,
        temperature=0,
        top_p=0.8,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        stop=["END"],
        provider_sort="latency",
        max_tokens=2048,
    )
    args = Namespace(
        temperature=None,
        top_p=None,
        max_tokens=None,
        timeout_seconds=None,
        provider_sort=None,
        stop=None,
    )

    merged = cli._config_with_cli_generation_params(config, args)

    assert merged.top_p == 0.8
    assert merged.max_tokens == 2048
    assert merged.provider_sort == "latency"
    assert merged.stop == ["END"]
