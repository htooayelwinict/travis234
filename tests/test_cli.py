from __future__ import annotations

import logging
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import travis.cli as cli
from travis.ai.env_config import ModelConfig
from travis.app import CodingApp
from travis.coding_agent.config import ENV_AGENT_DIR, get_agent_dir
from travis.coding_agent.provider_control_plane import ProviderControlPlane
from travis.ai.models import get_api_key_for_provider, register_model, reset_models
from travis.ai.types import Model
from travis.ai.types import UserMessage, now_ms
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.stream import register_api_provider, reset_api_providers
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore


def setup_function() -> None:
    reset_api_providers()
    reset_models()


@pytest.fixture(autouse=True)
def _disable_real_startup_live_catalog_fetch(monkeypatch) -> None:
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "false")


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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--cwd", str(missing_cwd), "--plain", "pwd"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert f"Error: working directory does not exist: {missing_cwd.resolve()}" in captured.err


def test_cli_provider_and_model_flags_resolve_registered_model_when_live_catalog_misses(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [])

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


def test_cli_exact_registered_openrouter_model_prefers_live_metadata(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")
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
        reasoning=True,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(registered_model)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.model.context_window == 400000
    assert app.model.max_tokens == 128000
    assert "Using custom model id" not in captured.err
    assert app.scoped_models == []


def test_cli_startup_live_catalog_force_refreshes_before_stale_cache_fallback(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    observed_kwargs: list[dict[str, object]] = []
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    def fake_live_catalog(**kwargs):
        observed_kwargs.append(dict(kwargs))
        return [live_model]

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fake_live_catalog)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert created["app"].model is live_model
    assert observed_kwargs and observed_kwargs[0]["force_refresh"] is True
    assert "Using custom model id" not in captured.err


def test_cli_unqualified_openrouter_model_id_hydrates_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert created["prompt"] == "inspect"
    assert created["app"].model is live_model
    assert "Using custom model id" not in captured.err


def test_cli_startup_fetch_flag_disables_startup_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    def fail_live_catalog(**kwargs):
        raise AssertionError("startup live catalog should be disabled")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_catalog)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "unknown/vendor-model",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert created["app"].model.provider == "openrouter"
    assert created["app"].model.id == "unknown/vendor-model"
    assert "Using custom model id" in captured.err


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
    registry = ProviderControlPlane.in_memory().models
    registry.replace_models([live_model])

    assert registry.find("openrouter", "openai/gpt-5.4-mini") is live_model


def test_cli_matching_live_model_is_case_insensitive() -> None:
    env_model = Model(
        id="OpenAI/GPT-5.4-MINI",
        name="OpenAI/GPT-5.4-MINI",
        api="openai-completions",
        provider="OpenRouter",
        base_url="https://openrouter.example.test/api",
    )
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    assert cli._matching_live_model(env_model, [live_model]) is live_model


def test_cli_default_openrouter_startup_prefers_live_env_model_metadata(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")

    live_model = Model(
        id="moonshotai/kimi-k2.6",
        name="Kimi K2.6 Live",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=256000,
        max_tokens=16384,
        reasoning=True,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.model.context_window == 256000
    assert app.model.max_tokens == 16384
    assert app.scoped_models == []
    assert "Using custom model id" not in captured.err


def test_cli_implicit_default_openrouter_startup_prefers_live_env_model_metadata(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="moonshotai/kimi-k2.6",
        name="Kimi K2.6 Live",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=256000,
        max_tokens=16384,
        reasoning=True,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.scoped_models == []
    assert "Using custom model id" not in captured.err


def test_cli_loads_persisted_auth_before_model_selection(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "auth.json").write_text(
        '{"openrouter": {"type": "api_key", "key": "persisted-key"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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


def test_cli_passes_travis_loop_runtime_options(monkeypatch, tmp_path) -> None:
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
            self.session = self
            self.grants = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

        def grant_capability(self, name, uses=1):
            self.grants.append((name, uses))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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
            "--allow-package-install",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert app.max_iterations == 7
    assert app.tool_loop_guardrails == {"blocking_enabled": True}
    assert app.grants == [("package_mutation", 1)]
    assert created["prompt"] == "inspect"


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

    def record_provider_registration(dotenv_path, config=None):
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: observed.setdefault("dotenv", dotenv_path))
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
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
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [])

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


def test_cli_models_unqualified_openrouter_model_id_hydrates_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "openai/gpt-5.4-mini:medium",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.thinking_level == "medium"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [(live_model, "medium")]
    assert "Using custom model id" not in captured.err


def test_cli_list_models_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="step-3.7-flash",
            name="Step 3.7 Flash",
            api="openai-completions",
            provider="stepfun",
            base_url="",
        )
    )
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--list-models"])

    assert code == 0
    assert "stepfun/step-3.7-flash" in capsys.readouterr().out


def test_cli_list_models_without_openrouter_provider_does_not_fetch_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="step-3.7-flash",
            name="Step 3.7 Flash",
            api="openai-completions",
            provider="stepfun",
            base_url="",
        )
    )

    calls = {"count": 0}

    def fail_live_fetch(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("unqualified --list-models should not fetch live OpenRouter catalog")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_fetch)

    code = cli.main(["--cwd", str(tmp_path), "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "stepfun/step-3.7-flash" in captured.out
    assert captured.err == ""
    assert calls["count"] == 0


def test_cli_list_models_includes_live_openrouter_catalog(monkeypatch, tmp_path, capsys) -> None:
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    assert code == 0
    assert "openrouter/openai/gpt-5.4-mini" in capsys.readouterr().out


def test_cli_list_models_warns_when_openrouter_live_catalog_unavailable(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")
    register_model(
        Model(
            id="known-local",
            name="Known Local",
            api="openai-completions",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [])

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/known-local" in captured.out
    assert "OpenRouter live model catalog unavailable" in captured.err


def test_cli_list_models_does_not_warn_when_startup_live_catalog_disabled(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="known-local",
            name="Known Local",
            api="openai-completions",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
    )

    def fail_live_fetch(*args, **kwargs):
        raise AssertionError("disabled startup live catalog should not fetch")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_fetch)

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/known-local" in captured.out
    assert captured.err == ""


def test_cli_list_models_verbose_shows_live_metadata(monkeypatch, tmp_path, capsys) -> None:
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
        input=["text", "image"],
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models", "--verbose-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/openai/gpt-5.4-mini" in captured.out
    assert "context=400000" in captured.out
    assert "max_tokens=128000" in captured.out
    assert "reasoning=true" in captured.out
    assert "input=text,image" in captured.out


def test_cli_list_providers_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="step-3.7-flash",
            name="Step 3.7 Flash",
            api="openai-completions",
            provider="stepfun",
            base_url="",
        )
    )
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [])

    code = cli.main(["--cwd", str(tmp_path), "--dotenv", str(env_path), "--plain", "inspect"])

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model.provider == "openrouter"
    assert app.model.id == "openai/gpt-5.4-mini"
    assert "TRAVIS234_WORKER_LLM" not in captured.err


def test_cli_startup_hydrates_live_openrouter_model_before_custom_fallback(monkeypatch, tmp_path, capsys) -> None:
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

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.model.context_window == 400000
    assert app.model.max_tokens == 128000
    assert "Using custom model id" not in captured.err


def test_cli_scoped_models_hydrates_exact_live_openrouter_model(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "openrouter/openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.thinking_level == "off"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [(live_model, None)]
    assert "No models match" not in captured.err
    assert "Using custom model id" not in captured.err


def test_cli_scoped_models_hydrates_unqualified_live_openrouter_model_with_thinking(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "openai/gpt-5.4-mini:medium",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.thinking_level == "medium"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [(live_model, "medium")]
    assert "No models match" not in captured.err
    assert "Using custom model id" not in captured.err


def test_cli_startup_preserves_custom_model_fallback_when_live_catalog_misses(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "unknown/vendor-model",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model.provider == "openrouter"
    assert app.model.id == "unknown/vendor-model"
    assert 'Model "unknown/vendor-model" not found for provider "openrouter". Using custom model id.' in captured.err


def test_cli_startup_preserves_custom_model_fallback_when_live_catalog_raises(
    monkeypatch, tmp_path, capsys, caplog
) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "true")

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    def fail_live_catalog(*args, **kwargs):
        raise RuntimeError("catalog boom")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_catalog)

    with caplog.at_level(logging.WARNING, logger="travis.cli"):
        code = cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--dotenv",
                str(tmp_path / "missing.env"),
                "--provider",
                "openrouter",
                "--model",
                "unknown/vendor-model",
                "--plain",
                "inspect",
            ]
        )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model.provider == "openrouter"
    assert app.model.id == "unknown/vendor-model"
    assert 'Model "unknown/vendor-model" not found for provider "openrouter". Using custom model id.' in captured.err
    assert "OpenRouter live model catalog unavailable during startup" in caplog.text
    assert "catalog boom" in caplog.text


def test_cli_generation_flags_are_passed_to_registered_provider(monkeypatch, tmp_path, capsys) -> None:
    observed: dict[str, object] = {}

    def record_registration(dotenv_path, config=None):
        observed["config"] = config

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", record_registration)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

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
    assert "Warning: generation parameter provider_sort dropped:" in capsys.readouterr().err


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

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
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
