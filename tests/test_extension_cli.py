from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from travis.coding_agent.extension_cli import ExtensionFlagSchemaError, add_extension_flags
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.resource_loader import DefaultResourceLoader


def _parser(runtime: ExtensionRunner) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*")
    add_extension_flags(parser, runtime)
    return parser


def _runtime() -> ExtensionRunner:
    runtime = ExtensionRunner()
    runtime.register_flag("verbose", {"type": "boolean", "description": "Verbose"})
    runtime.register_flag("profile", {"type": "string", "description": "Profile"})
    return runtime


def test_typed_extension_flags_preserve_prompt_and_last_string_value() -> None:
    args = _parser(_runtime()).parse_args(
        ["--profile", "safe", "--verbose", "--profile=security", "inspect"]
    )

    assert args.extension_flag_values == {"profile": "security", "verbose": True}
    assert args.prompt == ["inspect"]


def test_boolean_extension_flag_never_consumes_following_prompt() -> None:
    args = _parser(_runtime()).parse_args(["--verbose", "inspect"])

    assert args.extension_flag_values == {"verbose": True}
    assert args.prompt == ["inspect"]


def test_option_terminator_keeps_extension_shaped_prompt_text() -> None:
    args = _parser(_runtime()).parse_args(["--", "--verbose", "inspect"])

    assert args.extension_flag_values == {}
    assert args.prompt == ["--verbose", "inspect"]


@pytest.mark.parametrize("argv", [["--profile"], ["--verbose=false"]])
def test_invalid_extension_flag_arity_uses_argparse_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        _parser(_runtime()).parse_args(argv)


def test_extension_flag_cannot_shadow_builtin_option() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    runtime = ExtensionRunner()
    runtime.register_flag("model", {"type": "string"})

    with pytest.raises(ExtensionFlagSchemaError, match="--model.*built-in"):
        add_extension_flags(parser, runtime)


def test_cross_owner_extension_flag_conflict_is_fatal(tmp_path: Path) -> None:
    def first(runner: ExtensionRunner) -> None:
        runner.register_flag("profile", {"type": "string"})

    def second(runner: ExtensionRunner) -> None:
        runner.register_flag("profile", {"type": "boolean"})

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[first, second],
    )
    loader.reload({"projectTrustOverride": False})
    runtime = loader.get_extensions()["runtime"]

    with pytest.raises(
        ExtensionFlagSchemaError,
        match=r"--profile.*<inline:2>.*<inline:1>",
    ):
        add_extension_flags(argparse.ArgumentParser(), runtime)


def test_extension_flag_help_uses_registered_schema() -> None:
    help_text = _parser(_runtime()).format_help()

    assert "--profile VALUE" in help_text
    assert "--verbose" in help_text
    assert "Profile" in help_text
    assert "Verbose" in help_text
