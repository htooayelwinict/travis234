from __future__ import annotations

from pathlib import Path

import pytest

from travis.resources.extensions.hypa import (
    HypaConfig,
    apply_replace_mode_filter,
    install_hypa_extension,
    is_hypa_command,
    load_config,
    map_rewrite_result,
    parse_rewrite_json,
    rewrite_command,
)
from travis.resources.extensions.hypa.hypa_tools import (
    build_find_command,
    build_grep_command,
    build_ls_command,
    build_read_command,
    limit_stdout_lines,
    register_hypa_tools,
    shell_quote,
)
from travis.coding_agent.extensions import ExtensionRunner


def test_extension_runner_exec_delegates_to_bound_core_action() -> None:
    runner = ExtensionRunner(cwd="/workspace")
    calls: list[tuple[str, list[str], dict[str, object] | None]] = []

    def execute(command: str, args: list[str], options: dict[str, object] | None = None):
        calls.append((command, args, options))
        return {"stdout": "ok", "stderr": "", "code": 0, "killed": False}

    runner.bind_core({"exec": execute})

    result = runner.exec("hypa", ["rewrite", "--json", "git status"], {"timeout": 5000})

    assert result["stdout"] == "ok"
    assert calls == [("hypa", ["rewrite", "--json", "git status"], {"timeout": 5000})]


def test_hypa_is_optional_extension_and_not_part_of_coding_agent_core(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    extension_dir = root / "travis/resources/extensions/hypa"
    core_dir = root / "travis/coding_agent/builtin_extensions"
    monkeypatch.setenv("TRAVIS234_HYPA_ENABLED", "true")
    from travis.coding_agent.resource_loader import DefaultResourceLoader

    loader = DefaultResourceLoader(
        cwd=str(root),
        agent_dir=str(root / ".test-agent"),
        additional_extension_paths=[str(extension_dir)],
    )
    loader.reload()
    runtime = loader.get_extensions()["runtime"]

    assert (extension_dir / "__init__.py").is_file()
    assert not core_dir.exists()
    assert loader.get_extensions()["errors"] == []
    assert runtime.get_registered_command("hypa") is not None
    assert {tool.definition.name for tool in runtime.get_all_registered_tools()} == {
        "hypa_shell",
        "hypa_read",
        "hypa_grep",
        "hypa_find",
        "hypa_ls",
    }


def test_hypa_config_uses_only_travis234_integration_names(tmp_path) -> None:
    config_path = tmp_path / "hypa.json"
    config_path.write_text(
        '{"mode":"replace","rewriteTimeoutMs":7000,"askNonInteractive":"allow"}',
        encoding="utf-8",
    )

    config = load_config(
        {
            "TRAVIS234_HYPA_ENABLED": "true",
            "TRAVIS234_HYPA_CONFIG": str(config_path),
            "TRAVIS234_HYPA_MODE": "additive",
            "TRAVIS234_HYPA_REWRITE_TIMEOUT_MS": "9000",
            "HYPA_BIN": "/opt/hypa",
        }
    )

    assert config.enabled is True
    assert config.mode == "additive"
    assert config.binary == "/opt/hypa"
    assert config.rewrite_timeout_ms == 9000
    assert config.ask_non_interactive == "allow"
    assert config.config_path == config_path


def test_hypa_rewrite_contract_is_strict_and_prevents_recursion() -> None:
    payload = parse_rewrite_json(
        '{"schemaVersion":1,"input":"git status","outcome":"GenericWrapper",'
        '"command":"hypa git status"}'
    )

    assert map_rewrite_result(payload) == {
        "kind": "rewritten",
        "outcome": "GenericWrapper",
        "input": "git status",
        "command": "hypa git status",
    }
    assert is_hypa_command("   hypa git status") is True
    assert is_hypa_command("echo hypa") is False
    with pytest.raises(ValueError, match="unknown outcome"):
        parse_rewrite_json('{"input":"x","outcome":"Maybe","command":"x"}')


def test_hypa_replace_mode_only_filters_replaced_builtins() -> None:
    tools = ["bash", "read", "edit", "write", "grep", "find", "ls", "hypa_shell", "hypa_read"]

    assert apply_replace_mode_filter(tools, "replace") == ["edit", "write", "hypa_shell", "hypa_read"]
    assert apply_replace_mode_filter(tools, "additive") == tools


def _hypa_config(**overrides: object) -> HypaConfig:
    values: dict[str, object] = {
        "enabled": True,
        "mode": "additive",
        "binary": "hypa",
        "rewrite_timeout_ms": 5000,
        "ask_non_interactive": "deny",
        "mcp_proxy_enabled": False,
        "mcp_proxy_timeout_ms": 10000,
        "mcp_config_path": None,
        "config_path": None,
    }
    values.update(overrides)
    return HypaConfig(**values)


def test_hypa_rewrite_uses_extension_exec_and_fails_open_on_invalid_output() -> None:
    runner = ExtensionRunner()
    calls: list[tuple[str, list[str], dict[str, object] | None]] = []

    def execute(command: str, args: list[str], options: dict[str, object] | None = None):
        calls.append((command, args, options))
        return {"stdout": "not-json", "stderr": "", "code": 0, "killed": False}

    runner.bind_core({"exec": execute})

    status = rewrite_command(runner, _hypa_config(), "git status")

    assert status["kind"] == "error"
    assert calls == [("hypa", ["rewrite", "--json", "git status"], {"timeout": 5000})]


def test_hypa_extension_mutates_bash_and_keeps_non_bash_calls_unchanged() -> None:
    runner = ExtensionRunner()

    def execute(command: str, args: list[str], options: dict[str, object] | None = None):
        return {
            "stdout": '{"input":"git status","outcome":"Rewritten","command":"hypa git status"}',
            "stderr": "",
            "code": 0,
            "killed": False,
        }

    runner.bind_core({"exec": execute})
    install_hypa_extension(runner, _hypa_config())
    bash_event = {"type": "tool_call", "toolName": "bash", "toolCallId": "b1", "input": {"command": "git status"}}
    read_event = {"type": "tool_call", "toolName": "read", "toolCallId": "r1", "input": {"path": "README.md"}}

    assert runner.emit_tool_call(bash_event) is None
    assert bash_event["input"]["command"] == "hypa git status"
    assert runner.emit_tool_call(read_event) is None
    assert read_event["input"] == {"path": "README.md"}


def test_hypa_extension_keeps_external_deny_policy_optional_and_extension_owned() -> None:
    runner = ExtensionRunner()
    runner.bind_core(
        {
            "exec": lambda command, args, options=None: {
                "stdout": '{"input":"sudo reboot","outcome":"Deny","command":"sudo reboot"}',
                "stderr": "",
                "code": 1,
                "killed": False,
            }
        }
    )
    install_hypa_extension(runner, _hypa_config())
    event = {"type": "tool_call", "toolName": "bash", "toolCallId": "b1", "input": {"command": "sudo reboot"}}

    result = runner.emit_tool_call(event)

    assert result == {"block": True, "reason": "Command blocked by Hypa policy: sudo reboot"}


def test_hypa_replace_mode_filters_active_tools_on_every_agent_start() -> None:
    runner = ExtensionRunner()
    active = ["bash", "read", "edit", "write", "hypa_shell", "hypa_read"]
    runner.bind_core(
        {
            "getActiveTools": lambda: list(active),
            "setActiveTools": lambda names: active.__setitem__(slice(None), names),
        }
    )
    install_hypa_extension(runner, _hypa_config(mode="replace"))

    runner.emit_before_agent_start("inspect", None, "system")

    assert active == ["edit", "write", "hypa_shell", "hypa_read"]


def test_hypa_tool_command_builders_quote_data_and_avoid_shell_pipelines() -> None:
    assert shell_quote("it's") == "'it'\"'\"'s'"
    assert build_read_command("src/File.py", 10, 5) == "sed -n 10,14p -- src/File.py"
    assert build_grep_command(
        {
            "pattern": "--help",
            "path": "src",
            "glob": "*.py",
            "ignoreCase": True,
            "literal": True,
            "context": 2,
            "limit": 3,
        }
    ) == (
        "rg --heading --line-number --color=never --ignore-case --fixed-strings "
        "--context 2 --max-count 3 --glob '*.py' -e --help -- src"
    )
    assert build_find_command({"pattern": "*.py", "path": "src", "limit": 2}) == "rg --files --glob '*.py' src"
    assert build_ls_command({"path": ".", "all": True}) == "ls -la -- ."
    assert limit_stdout_lines("a\n\nb\nc\n", 2) == "a\nb\n"


def test_hypa_registers_context_efficient_tools_and_recovers_truncated_output(tmp_path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    full_output = "\n".join(f"line-{index}" for index in range(2105))
    calls: list[tuple[str, list[str], dict[str, object] | None]] = []

    def execute(command: str, args: list[str], options: dict[str, object] | None = None):
        calls.append((command, args, options))
        return {"stdout": full_output, "stderr": "", "code": 0, "killed": False}

    runner.bind_core({"exec": execute})
    register_hypa_tools(runner, _hypa_config())
    tools = {item.definition.name: item.definition for item in runner.get_all_registered_tools()}

    result = tools["hypa_shell"].execute("h1", {"command": "pytest -q"})

    assert set(tools) == {"hypa_shell", "hypa_read", "hypa_grep", "hypa_find", "hypa_ls"}
    assert calls == [("hypa", ["-c", "pytest -q"], {"signal": None, "timeout": None})]
    assert "Output truncated" in result.content[0].text
    output_path = Path(result.details["fullOutputPath"])
    assert output_path.is_file()
    assert "travis234-hypa-" in str(output_path)
    assert output_path.read_text(encoding="utf-8") == full_output
