from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import travis.cli as cli
from travis.ai.providers.faux import faux_model
from travis.coding_agent.config import ENV_AGENT_DIR


def _write_flag_extension(path: Path, counter: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counter_code = ""
    if counter is not None:
        counter_code = (
            "from pathlib import Path\n"
            f"_counter = Path({str(counter)!r})\n"
            "_counter.write_text(str(int(_counter.read_text() or '0') + 1) if _counter.exists() "
            "else '1', encoding='utf-8')\n"
        )
    path.write_text(
        counter_code
        + "\n"
        + "def extension(travis):\n"
        + "    travis.register_flag('verbose', {'type': 'boolean', 'description': 'Verbose extension'})\n"
        + "    travis.register_flag('profile', {'type': 'string', 'description': 'Extension profile'})\n",
        encoding="utf-8",
    )


def _install_app_capture(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
) -> None:
    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            captured["app_kwargs"] = dict(kwargs)
            self.session = SimpleNamespace(get_known_tool_names=lambda: [])

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *_args, **_kwargs: cli._StartupModelSelection(model=faux_model()),
    )

    def capture_print(_app: object, prompt: str, _output: object, **_kwargs: object) -> int:
        captured["prompt"] = prompt
        return 0

    monkeypatch.setattr(cli, "run_print_mode", capture_print)


def test_cli_parses_typed_extension_flags_once_and_preserves_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    extension = tmp_path / "operator" / "flags.py"
    counter = tmp_path / "counter.txt"
    _write_flag_extension(extension, counter)
    captured: dict[str, object] = {}
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    _install_app_capture(monkeypatch, captured)

    exit_code = cli.main(
        [
            "--cwd",
            str(project),
            "--no-session",
            "--mode",
            "print",
            "--extension",
            str(extension),
            "--profile",
            "safe",
            "--verbose",
            "--profile=security",
            "inspect",
        ]
    )

    app_kwargs = captured["app_kwargs"]
    loader = app_kwargs["initial_resource_loader"]
    assert exit_code == 0
    assert app_kwargs["extension_flag_values"] == {"profile": "security", "verbose": True}
    assert captured["prompt"] == "inspect"
    assert loader.get_extensions()["runtime"].get_flags()["profile"].type == "string"
    assert counter.read_text(encoding="utf-8") == "1"


def test_extension_help_loads_schema_without_model_or_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extension = tmp_path / "operator" / "flags.py"
    counter = tmp_path / "counter.txt"
    _write_flag_extension(extension, counter)
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("session constructed")),
    )
    monkeypatch.setattr(
        cli,
        "load_model_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model config loaded")),
    )

    exit_code = cli.main(["--cwd", str(tmp_path), "--extension", str(extension), "--help"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "--profile VALUE" in output.out
    assert "Extension profile" in output.out
    assert "--verbose" in output.out
    assert "Verbose extension" in output.out
    assert counter.read_text(encoding="utf-8") == "1"


def test_cli_disposes_loaded_extension_runtime_when_model_config_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from travis.coding_agent.extensions import ExtensionRunner

    extension = tmp_path / "flags.py"
    _write_flag_extension(extension)
    disposed_with_profile: list[ExtensionRunner] = []
    original_dispose = ExtensionRunner.dispose

    def record_dispose(runtime: ExtensionRunner) -> None:
        if "profile" in runtime.get_flags():
            disposed_with_profile.append(runtime)
        original_dispose(runtime)

    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    monkeypatch.setattr(ExtensionRunner, "dispose", record_dispose)
    monkeypatch.setattr(
        cli,
        "load_model_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid model config")),
    )

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--no-session",
                "--extension",
                str(extension),
                "inspect",
            ]
        )

    assert len(disposed_with_profile) == 1


@pytest.mark.parametrize("trust_flag", [None, "--no-approve"])
def test_unknown_project_flag_fails_closed_without_executing_project_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    trust_flag: str | None,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "project-extension-loaded.txt"
    extension = project / ".travis234" / "extensions" / "flags.py"
    _write_flag_extension(extension, marker)
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("session constructed")),
    )
    argv = ["--cwd", str(project), "--no-session", "--mode", "print"]
    if trust_flag is not None:
        argv.append(trust_flag)
    argv.extend(["--profile", "security", "inspect"])

    with pytest.raises(SystemExit, match="2"):
        cli.main(argv)

    assert not marker.exists()


def test_approved_project_flag_loads_and_reaches_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "project-extension-loaded.txt"
    extension = project / ".travis234" / "extensions" / "flags.py"
    _write_flag_extension(extension, marker)
    captured: dict[str, object] = {}
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    _install_app_capture(monkeypatch, captured)

    exit_code = cli.main(
        [
            "--cwd",
            str(project),
            "--approve",
            "--no-session",
            "--mode",
            "print",
            "--profile",
            "security",
            "inspect",
        ]
    )

    assert exit_code == 0
    assert marker.read_text(encoding="utf-8") == "1"
    assert captured["app_kwargs"]["extension_flag_values"] == {"profile": "security"}


@pytest.mark.parametrize("extension_args", [["--profile"], ["--verbose=false"]])
def test_cli_extension_flag_arity_errors_use_argparse_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extension_args: list[str],
) -> None:
    extension = tmp_path / "flags.py"
    _write_flag_extension(extension)
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--no-session",
                "--extension",
                str(extension),
                *extension_args,
            ]
        )


def test_cli_unknown_short_option_remains_an_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))

    with pytest.raises(SystemExit, match="2"):
        cli.main(["--cwd", str(tmp_path), "--no-session", "-z", "inspect"])


def test_extension_flags_keep_json_stdout_machine_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extension = tmp_path / "flags.py"
    _write_flag_extension(extension)
    captured: dict[str, object] = {}
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    _install_app_capture(monkeypatch, captured)

    def capture_json(_app: object, prompt: str, output: object, **_kwargs: object) -> int:
        print(json.dumps({"prompt": prompt}), file=output)
        return 0

    monkeypatch.setattr(cli, "run_json_mode", capture_json)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--no-session",
            "--mode",
            "json",
            "--extension",
            str(extension),
            "--verbose",
            "inspect",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(output.out) == {"prompt": "inspect"}
    assert output.err == ""


def test_option_terminator_keeps_extension_shaped_cli_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extension = tmp_path / "flags.py"
    _write_flag_extension(extension)
    captured: dict[str, object] = {}
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    _install_app_capture(monkeypatch, captured)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--no-session",
            "--extension",
            str(extension),
            "--",
            "--verbose",
            "inspect",
        ]
    )

    assert exit_code == 0
    assert captured["app_kwargs"]["extension_flag_values"] == {}
    assert captured["prompt"] == "--verbose inspect"


def test_cli_extension_flag_contract_is_manifested() -> None:
    from scripts.parity_contracts import PI_CONTRACTS

    entry = next(item for item in PI_CONTRACTS if item.contract_id == "pi.cli.extension_flags")
    assert entry.status == "parity"
    assert entry.evidence.endswith(
        "tests/test_cli_extension_flags.py::"
        "test_cli_parses_typed_extension_flags_once_and_preserves_prompt"
    )


def test_readme_documents_typed_extension_flag_edges() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "Authorized extension flags appear in `--help`" in readme
    assert "Boolean flags never consume the following prompt" in readme
    assert "repeated string flag uses the last value" in readme
    assert "Use `--` to keep option-shaped text in the prompt" in readme
