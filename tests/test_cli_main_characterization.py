from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path

import pytest

import travis.cli as cli
from travis.ai.env_config import ModelConfig
from travis.ai.types import Model
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.extensions import ExtensionFlagValidationError, ExtensionRunner
from travis.coding_agent.model_registry import ModelRegistry


_ACTIVE_MODEL_ENV = "TRAVIS234_ORCHESTRATION_ACTIVE_MODEL"


def _model_config() -> ModelConfig:
    return ModelConfig(
        enabled=True,
        api_key=None,
        model="test-model",
        base_url="https://provider.example.test/v1",
        timeout_seconds=30.0,
        temperature=0.0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="openrouter",
    )


def _model() -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        api="openai-completions",
        provider="openrouter",
        base_url="https://provider.example.test/v1",
    )


class _TrackingRuntime(ExtensionRunner):
    def __init__(self, label: str, cwd: Path, events: list[str]) -> None:
        super().__init__(cwd=str(cwd))
        self.label = label
        self._events = events

    def dispose(self) -> None:
        self._events.append(f"dispose:{self.label}")
        super().dispose()


class _KnownTools:
    def __init__(self, names: list[str]) -> None:
        self._names = list(names)

    def get_known_tool_names(self) -> list[str]:
        return list(self._names)


@dataclass
class _MainHarness:
    events: list[str]
    app_kwargs: dict[str, object]
    dispatch_env: dict[str, str | None]
    pretrust_runtime: _TrackingRuntime
    full_runtime: _TrackingRuntime
    loader_count: list[int] = field(default_factory=list)


def _install_main_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    known_tools: list[str] | None = None,
) -> _MainHarness:
    events: list[str] = []
    app_kwargs: dict[str, object] = {}
    dispatch_env: dict[str, str | None] = {}
    loader_count: list[int] = []
    pretrust_runtime = _TrackingRuntime("pretrust", tmp_path, events)
    full_runtime = _TrackingRuntime("full", tmp_path, events)
    config = _model_config()
    model = _model()
    auth_storage = AuthStorage.in_memory()
    model_registry = ModelRegistry.in_memory(auth_storage, provider_config=config)

    class FakeResourceLoader:
        def __init__(self, **_options: object) -> None:
            loader_count.append(1)
            events.append("loader:init")
            self._runtime = pretrust_runtime

        def load_project_trust_extensions(self) -> dict[str, object]:
            events.append("extensions:pretrust")
            return {"runtime": pretrust_runtime}

        def complete_reload(
            self,
            _options: Mapping[str, object] | None = None,
            *,
            pretrust_extensions: dict[str, object] | None = None,
        ) -> None:
            assert pretrust_extensions == {"runtime": pretrust_runtime}
            events.append("extensions:reload")
            pretrust_runtime.dispose()
            self._runtime = full_runtime

        def get_extensions(self) -> dict[str, object]:
            return {"runtime": self._runtime}

    tool_names = list(known_tools or ["read", "bash", "mcp"])

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            events.append("app:init")
            app_kwargs.update(kwargs)
            self.session = _KnownTools(tool_names)

        def close(self) -> None:
            events.append(f"app:close:{os.environ.get(_ACTIVE_MODEL_ENV)}")
            full_runtime.dispose()

    def run_print(
        _app: object,
        prompt: str,
        _output: object,
        **_options: object,
    ) -> int:
        events.append(f"dispatch:print:{prompt}")
        dispatch_env[_ACTIVE_MODEL_ENV] = os.environ.get(_ACTIVE_MODEL_ENV)
        return 41

    def run_json(
        _app: object,
        prompt: str,
        _output: object,
        **_options: object,
    ) -> int:
        events.append(f"dispatch:json:{prompt}")
        dispatch_env[_ACTIVE_MODEL_ENV] = os.environ.get(_ACTIVE_MODEL_ENV)
        return 42

    class FakeRpcServer:
        def __init__(self, _app: object, _input: object, _output: object) -> None:
            events.append("dispatch:rpc:init")

        def run(self) -> int:
            events.append("dispatch:rpc:run")
            dispatch_env[_ACTIVE_MODEL_ENV] = os.environ.get(_ACTIVE_MODEL_ENV)
            return 43

    class FakeInteractiveMode:
        def __init__(self, _app: object, **_options: object) -> None:
            events.append("dispatch:interactive:init")

        def run(self) -> int:
            events.append("dispatch:interactive:run")
            dispatch_env[_ACTIVE_MODEL_ENV] = os.environ.get(_ACTIVE_MODEL_ENV)
            return 44

    monkeypatch.setattr(cli, "DefaultResourceLoader", FakeResourceLoader)
    monkeypatch.setattr(cli.SettingsManager, "create", staticmethod(lambda *_args: object()))
    monkeypatch.setattr(cli, "SessionCatalog", lambda *_args: object())
    monkeypatch.setattr(cli, "get_agent_dir", lambda: str(tmp_path / "agent"))
    monkeypatch.setattr(cli, "get_auth_path", lambda: str(tmp_path / "auth.json"))
    monkeypatch.setattr(cli, "get_models_path", lambda: str(tmp_path / "models.json"))
    monkeypatch.setattr(cli, "load_model_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cli.AuthStorage, "create", staticmethod(lambda *_args: auth_storage))
    monkeypatch.setattr(
        cli.ModelRegistry,
        "create",
        staticmethod(lambda *_args, **_kwargs: model_registry),
    )
    monkeypatch.setattr(cli, "_register_dotenv_provider_credentials", lambda *_args: [])
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *_args, **_kwargs: cli._StartupModelSelection(model=model),
    )
    monkeypatch.setattr(cli, "_generation_param_warnings_for_model", lambda *_args: [])
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "run_print_mode", run_print)
    monkeypatch.setattr(cli, "run_json_mode", run_json)
    monkeypatch.setattr(cli, "RpcServer", FakeRpcServer)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)
    return _MainHarness(
        events=events,
        app_kwargs=app_kwargs,
        dispatch_env=dispatch_env,
        pretrust_runtime=pretrust_runtime,
        full_runtime=full_runtime,
        loader_count=loader_count,
    )


def _print_argv(tmp_path: Path, *options: str) -> list[str]:
    return [
        "--cwd",
        str(tmp_path),
        "--no-session",
        "--no-approve",
        "--mode",
        "print",
        *options,
        "inspect",
    ]


def test_main_package_action_bypasses_bootstrap_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(cli, "is_package_cli_invocation", lambda argv: argv == ["list"])
    monkeypatch.setattr(
        cli,
        "run_package_cli",
        lambda argv, *, agent_dir: observed.update(argv=argv, agent_dir=agent_dir) or 17,
    )
    monkeypatch.setattr(cli, "get_agent_dir", lambda: "/agent")
    monkeypatch.setattr(
        cli,
        "_build_parser",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("bootstrap parser created")),
    )

    assert cli.main(["list"]) == 17
    assert observed == {"argv": ["list"], "agent_dir": "/agent"}


def test_main_uses_bootstrap_pretrust_and_full_parsers_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    original_build_parser = cli._build_parser
    parser_stages: list[tuple[bool, str | None]] = []

    def track_parser(
        *,
        include_prompt: bool,
        extension_runtime: ExtensionRunner | None = None,
    ) -> argparse.ArgumentParser:
        label = extension_runtime.label if isinstance(extension_runtime, _TrackingRuntime) else None
        parser_stages.append((include_prompt, label))
        return original_build_parser(
            include_prompt=include_prompt,
            extension_runtime=extension_runtime,
        )

    monkeypatch.setattr(cli, "_build_parser", track_parser)

    assert cli.main(_print_argv(tmp_path)) == 41
    assert parser_stages == [
        (False, None),
        (True, "pretrust"),
        (True, "full"),
    ]
    assert harness.loader_count == [1]


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_main_export_is_core_only_and_preserves_output_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fails: bool,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    source = tmp_path / "session.jsonl"
    output = tmp_path / "session.html"

    def export(_source: str, _output: str | None) -> str:
        if fails:
            raise ValueError("cannot export session")
        return str(output)

    monkeypatch.setattr(cli, "export_from_file", export)

    result = cli.main(["--export", str(source), str(output)])

    captured = capsys.readouterr()
    assert result == (1 if fails else 0)
    assert harness.loader_count == []
    if fails:
        assert captured.out == ""
        assert captured.err == "Error: cannot export session\n"
    else:
        assert captured.out == f"Exported to: {output}\n"
        assert captured.err == ""


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_main_validates_cwd_before_loading_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    cwd = tmp_path / kind
    if kind == "file":
        cwd.write_text("not a directory", encoding="utf-8")

    assert cli.main(["--cwd", str(cwd), "--plain", "inspect"]) == 1

    captured = capsys.readouterr()
    suffix = "does not exist" if kind == "missing" else "is not a directory"
    assert captured.err == f"Error: working directory {suffix}: {cwd.resolve()}\n"
    assert harness.loader_count == []


@pytest.mark.parametrize(
    ("option", "label"),
    [
        ("--extension", "extension"),
        ("--skill", "skill"),
        ("--prompt-template", "prompt-template"),
        ("--theme", "theme"),
        ("--image", "image"),
    ],
)
def test_main_validates_each_explicit_resource_path_before_session_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    label: str,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    missing = (tmp_path / "missing-resource").resolve()

    with pytest.raises(SystemExit, match="2"):
        cli.main(_print_argv(tmp_path, option, str(missing)))

    assert f"{label} path does not exist: {missing}" in capsys.readouterr().err
    assert harness.loader_count == []


def test_main_uses_resolved_session_cwd_for_resources_and_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    selected_cwd = tmp_path / "selected"
    selected_cwd.mkdir()
    observed: dict[str, object] = {}

    def resolve_session(
        args: argparse.Namespace,
        *,
        cwd: Path,
        cwd_was_explicit: bool,
        launch_dir: Path,
        catalog: object,
    ) -> cli._StartupSessionSelection:
        observed.update(
            no_session=args.no_session,
            cwd=cwd,
            cwd_was_explicit=cwd_was_explicit,
            launch_dir=launch_dir,
            catalog=catalog,
        )
        return cli._StartupSessionSelection(selected_cwd, "/session.jsonl", "session-id", True)

    monkeypatch.setattr(cli, "_resolve_startup_session", resolve_session)

    assert cli.main(_print_argv(tmp_path)) == 41
    assert observed["no_session"] is True
    assert observed["cwd"] == tmp_path.resolve()
    assert observed["cwd_was_explicit"] is True
    assert harness.app_kwargs["cwd"] == str(selected_cwd)
    assert harness.app_kwargs["session_path"] == "/session.jsonl"
    assert harness.app_kwargs["session_id"] == "session-id"


@pytest.mark.parametrize(
    ("stage", "argv", "expected_disposals"),
    [
        ("pretrust-schema", ["--help"], ["dispose:pretrust"]),
        ("pretrust-parse", ["--profile"], ["dispose:pretrust"]),
        (
            "full-schema",
            ["--help"],
            ["dispose:pretrust", "dispose:full"],
        ),
        (
            "full-parse",
            ["--profile"],
            ["dispose:pretrust", "dispose:full"],
        ),
    ],
)
def test_main_disposes_the_runtime_that_owns_each_extension_parse_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    argv: list[str],
    expected_disposals: list[str],
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    if stage == "pretrust-schema":
        harness.pretrust_runtime.register_flag("model", {"type": "string"})
    elif stage == "pretrust-parse":
        harness.pretrust_runtime.register_flag("profile", {"type": "string"})
    elif stage == "full-schema":
        harness.full_runtime.register_flag("model", {"type": "string"})
    else:
        harness.full_runtime.register_flag("profile", {"type": "string"})

    with pytest.raises(SystemExit, match="2"):
        cli.main(["--cwd", str(tmp_path), "--no-session", "--no-approve", *argv])

    assert [event for event in harness.events if event.startswith("dispose:")] == expected_disposals
    assert "app:init" not in harness.events


@pytest.mark.parametrize(
    ("argv", "expected_code"),
    [
        (["--help"], 0),
        (["--mode", "print"], 2),
        (["--resume", "inspect"], 2),
    ],
    ids=["help-return", "missing-prompt-error", "resume-with-prompt-error"],
)
def test_main_disposes_full_runtime_on_post_parse_return_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    expected_code: int,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    full_argv = ["--cwd", str(tmp_path), "--no-approve", *argv]
    if "--resume" not in argv:
        full_argv.insert(2, "--no-session")

    if expected_code == 0:
        assert cli.main(full_argv) == 0
    else:
        with pytest.raises(SystemExit, match=str(expected_code)):
            cli.main(full_argv)

    assert [event for event in harness.events if event.startswith("dispose:")] == [
        "dispose:pretrust",
        "dispose:full",
    ]
    assert "app:init" not in harness.events


def test_main_disposes_full_runtime_when_mcp_is_also_excluded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)

    with pytest.raises(SystemExit, match="2"):
        cli.main(_print_argv(tmp_path, "--mcp", "--exclude-tools", "mcp"))

    assert "--mcp cannot be combined with --exclude-tools mcp" in capsys.readouterr().err
    assert [event for event in harness.events if event.startswith("dispose:")] == [
        "dispose:pretrust",
        "dispose:full",
    ]
    assert "app:init" not in harness.events


def test_main_preserves_tool_selection_and_invalid_thinking_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)

    assert cli.main(
        _print_argv(
            tmp_path,
            "--tools",
            "read,bash",
            "--exclude-tools",
            "bash",
            "--mcp",
            "--thinking",
            "turbo",
        )
    ) == 41

    assert harness.app_kwargs["allowed_tool_names"] == ["read", "bash", "mcp"]
    assert harness.app_kwargs["excluded_tool_names"] == ["bash"]
    assert harness.app_kwargs["additional_active_tool_names"] == ["mcp"]
    assert harness.app_kwargs["thinking_level"] == "off"
    assert capsys.readouterr().err == (
        'Warning: Invalid thinking level "turbo". '
        "Valid values: off, minimal, low, medium, high, xhigh, max\n"
    )


@pytest.mark.parametrize("action", ["providers", "models"])
def test_main_core_lists_exit_without_extension_or_app_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action: str,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_print_provider_list", lambda _registry: harness.events.append("list:providers"))
    monkeypatch.setattr(
        cli,
        "_print_model_list",
        lambda _registry, *, verbose=False: harness.events.append(f"list:models:{verbose}"),
    )

    assert cli.main(["--cwd", str(tmp_path), "--no-session", f"--list-{action}"]) == 0
    assert harness.loader_count == []
    assert "app:init" not in harness.events
    assert f"list:{action}" in harness.events[-1]


def test_main_core_list_preserves_model_config_parser_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "load_model_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid worker config")),
    )

    with pytest.raises(SystemExit, match="2"):
        cli.main(["--cwd", str(tmp_path), "--no-session", "--list-models"])

    assert "invalid worker config" in capsys.readouterr().err
    assert harness.loader_count == []
    assert not [event for event in harness.events if event.startswith("dispose:")]


@pytest.mark.parametrize("failure", ["config", "startup"])
def test_main_disposes_full_runtime_on_configuration_and_startup_model_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    if failure == "config":
        monkeypatch.setattr(
            cli,
            "load_model_config",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid config")),
        )
    else:
        monkeypatch.setattr(
            cli,
            "_startup_model_from_env",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unknown startup model")),
        )

    with pytest.raises(SystemExit, match="2"):
        cli.main(_print_argv(tmp_path))

    expected = "invalid config" if failure == "config" else "unknown startup model"
    assert expected in capsys.readouterr().err
    assert [event for event in harness.events if event.startswith("dispose:")] == [
        "dispose:pretrust",
        "dispose:full",
    ]
    assert "app:init" not in harness.events


@pytest.mark.parametrize("failure", ["extension-flags", "base-exception"])
def test_main_disposes_full_runtime_when_app_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)
    if failure == "extension-flags":
        error: BaseException = ExtensionFlagValidationError(
            [{"message": "invalid extension flag value"}]
        )
    else:
        error = KeyboardInterrupt("construction interrupted")

    def fail_app(**_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(cli, "CodingApp", fail_app)

    if failure == "extension-flags":
        with pytest.raises(SystemExit, match="2"):
            cli.main(_print_argv(tmp_path))
        assert "invalid extension flag value" in capsys.readouterr().err
    else:
        with pytest.raises(KeyboardInterrupt, match="construction interrupted"):
            cli.main(_print_argv(tmp_path))

    assert [event for event in harness.events if event.startswith("dispose:")] == [
        "dispose:pretrust",
        "dispose:full",
    ]


@pytest.mark.parametrize(
    ("requested", "known", "message"),
    [
        (["missing"], ["read"], "unknown tool name: missing"),
        (["first", "second"], ["read"], "unknown tool names: first, second"),
        (
            ["mcp"],
            ["read"],
            "MCP tool is unavailable; install it with: travis234 install travis234-mcp-adapter",
        ),
    ],
)
def test_main_closes_app_after_unknown_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    requested: list[str],
    known: list[str],
    message: str,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path, known_tools=known)
    option = "--mcp" if requested == ["mcp"] else "--tools"
    values = [] if option == "--mcp" else [",".join(requested)]

    with pytest.raises(SystemExit, match="2"):
        cli.main(_print_argv(tmp_path, option, *values))

    assert message in capsys.readouterr().err
    assert harness.events[-2:] == ["app:close:None", "dispose:full"]


def test_main_restores_orchestration_defaults_before_app_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(_ACTIVE_MODEL_ENV, "original/model")
    harness = _install_main_harness(monkeypatch, tmp_path)

    assert cli.main(_print_argv(tmp_path)) == 41

    assert harness.dispatch_env == {_ACTIVE_MODEL_ENV: "openrouter/test-model"}
    assert os.environ[_ACTIVE_MODEL_ENV] == "original/model"
    assert harness.events[-3:] == [
        "dispatch:print:inspect",
        "app:close:original/model",
        "dispose:full",
    ]


def test_main_restores_orchestration_defaults_and_closes_app_after_dispatch_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(_ACTIVE_MODEL_ENV, "original/model")
    harness = _install_main_harness(monkeypatch, tmp_path)

    def fail_dispatch(
        _app: object,
        _prompt: str,
        _output: object,
        **_options: object,
    ) -> int:
        harness.events.append(
            f"dispatch:error:{os.environ.get(_ACTIVE_MODEL_ENV)}"
        )
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(cli, "run_print_mode", fail_dispatch)

    with pytest.raises(RuntimeError, match="dispatch failed"):
        cli.main(_print_argv(tmp_path))

    assert os.environ[_ACTIVE_MODEL_ENV] == "original/model"
    assert harness.events[-3:] == [
        "dispatch:error:openrouter/test-model",
        "app:close:original/model",
        "dispose:full",
    ]


@pytest.mark.parametrize(
    ("argv", "event", "result"),
    [
        (["--mode", "print", "inspect"], "dispatch:print:inspect", 41),
        (["--mode", "json", "inspect"], "dispatch:json:inspect", 42),
        (["--mode", "rpc"], "dispatch:rpc:run", 43),
        (["--mode", "interactive"], "dispatch:interactive:run", 44),
        (["inspect"], "dispatch:print:inspect", 41),
    ],
    ids=["print", "json", "rpc", "interactive", "implicit-print"],
)
def test_main_preserves_final_mode_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    event: str,
    result: int,
) -> None:
    harness = _install_main_harness(monkeypatch, tmp_path)

    assert cli.main(
        ["--cwd", str(tmp_path), "--no-session", "--no-approve", *argv]
    ) == result
    assert event in harness.events
    assert harness.events[-2:] == ["app:close:None", "dispose:full"]
