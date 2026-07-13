from __future__ import annotations

from pathlib import Path

import appv23.cli as cli
from appv23.app import CodingApp
from appv23.ai.models import get_api_key_for_provider, register_model, reset_models
from appv23.ai.types import Model
from appv23.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from appv23.ai.stream import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()
    reset_models()


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
            created["app"] = self

    class FakeInteractiveMode:
        def __init__(self, app):
            created["mode_app"] = app

        def run(self):
            return 17

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
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


def test_cli_rejects_missing_cwd_before_starting_app(monkeypatch, tmp_path, capsys) -> None:
    missing_cwd = tmp_path / "missing-project"

    def fail_startup(*args, **kwargs):
        raise AssertionError("invalid cwd should stop before provider/model/app startup")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--cwd", str(missing_cwd), "--plain", "pwd"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert f"Error: working directory does not exist: {missing_cwd.resolve()}" in captured.err


def test_cli_provider_and_model_flags_resolve_registered_model(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
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
    assert app.scoped_models == []


def test_cli_loads_persisted_auth_before_model_selection(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "auth.json").write_text(
        '{"openrouter": {"type": "api_key", "key": "persisted-key"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))

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
        observed["api_key"] = get_api_key_for_provider("openrouter")
        return cli._StartupModelSelection(
            model=Model(
                id="qwen/qwen3.6-flash",
                name="qwen/qwen3.6-flash",
                api="faux",
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
            )
        )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--plain", "hi"])

    assert exit_code == 0
    assert observed["prompt"] == "hi"
    assert observed["api_key"] == "persisted-key"


def test_cli_passes_hermes_loop_runtime_options(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(
            self,
            *,
            cwd,
            model,
            enable_tui,
            thinking_level,
            scoped_models,
            max_iterations=None,
            tool_loop_guardrails=None,
            **kwargs,
        ):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.max_iterations = max_iterations
            self.tool_loop_guardrails = tool_loop_guardrails
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--max-iterations",
            "7",
            "--tool-loop-hard-stop",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert app.max_iterations == 7
    assert app.tool_loop_guardrails == {"hard_stop_enabled": True}
    assert created["prompt"] == "inspect"


def test_cli_default_dotenv_searches_parent_dirs_for_npm_prefix_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "appV2.3"
    app_dir.mkdir(parents=True)
    project = repo / "project"
    project.mkdir()
    env_path = repo / ".env"
    env_path.write_text(
        "APPV2_WORKER_LLM_ENABLED=true\nOPENROUTER_API_KEY=test-key\n",
        encoding="utf-8",
    )
    (app_dir / ".env").write_text(
        "APPV2_WORKER_LLM_ENABLED=true\nOPENROUTER_API_KEY=wrong-prefix-key\n",
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

    def record_provider_registration(dotenv_path):
        observed["registered_dotenv"] = Path(dotenv_path)

    def record_startup(dotenv_path, **kwargs):
        observed["startup_dotenv"] = Path(dotenv_path)
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        )

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    monkeypatch.setattr(cli, "register_builtin_providers", record_provider_registration)
    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(project), "--plain", "inspect"])

    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert observed["registered_dotenv"] == env_path
    assert observed["startup_dotenv"] == env_path


def test_cli_default_cwd_uses_npm_initial_cwd_for_prefix_wrapper(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "appV2.3"
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--plain", "inspect"])

    app = observed["app"]
    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert app.cwd == str(repo.resolve())


def test_cli_explicit_relative_dotenv_uses_npm_initial_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "appV2.3"
    app_dir.mkdir(parents=True)
    env_path = repo / ".env"
    env_path.write_text("APPV2_WORKER_LLM_ENABLED=true\n", encoding="utf-8")
    (app_dir / ".env").write_text("APPV2_WORKER_LLM_ENABLED=false\n", encoding="utf-8")
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: observed.setdefault("dotenv", dotenv_path))
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
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
    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--export", str(session_path), str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert called == {"input_path": str(session_path), "options": str(output_path)}
    assert captured.out == f"Exported to: {output_path}\n"
    assert captured.err == ""


def test_cli_models_flag_sets_scoped_models_and_initial_model(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    sonnet = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )
    qwen = Model(
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

    register_model(sonnet)
    register_model(qwen)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path: None)
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
