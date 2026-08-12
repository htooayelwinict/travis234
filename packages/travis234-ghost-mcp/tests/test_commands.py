from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from travis234_ghost_mcp import commands
from travis234_ghost_mcp.commands import (
    doctor_handler,
    parse_setup_argument,
    run_ghost_command,
    setup_handler,
)


def test_doctor_uses_argument_array_safe_environment_and_bound(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "ghost"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"version":"' + ("x" * 20_000) + '"}',
            stderr="",
        )

    monkeypatch.setattr(commands, "ghost_binary", lambda: binary)
    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    monkeypatch.setenv("SECRET_TOKEN", "never-forward")

    result = run_ghost_command("doctor")

    assert seen["argv"] == [str(binary), "doctor", "--json"]
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert set(kwargs["env"]) <= {
        "HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TERM_PROGRAM",
    }
    assert "SECRET_TOKEN" not in kwargs["env"]
    assert kwargs["timeout"] == 30
    assert kwargs["shell"] is False
    assert len(result.text.encode("utf-8")) <= 16_384


@pytest.mark.parametrize(
    ("argument", "expected"),
    [("", False), ("   ", False), ("vision", True), (" vision ", True)],
)
def test_setup_argument_is_narrow(argument: str, expected: bool) -> None:
    assert parse_setup_argument(argument) is expected


@pytest.mark.parametrize("argument", ["--vision", "yes", "vision now", "doctor"])
def test_setup_rejects_all_other_arguments_without_spawning(
    argument: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("spawned rejected setup input"),
    )

    with pytest.raises(ValueError, match="only empty input or 'vision'"):
        parse_setup_argument(argument)


def test_setup_deadlines_make_vision_explicit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "ghost"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    calls: list[tuple[list[str], int]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, 0, stdout="ready", stderr="")

    monkeypatch.setattr(commands, "ghost_binary", lambda: binary)
    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    run_ghost_command("setup")
    run_ghost_command("setup", include_vision=True)

    assert calls == [
        ([str(binary), "setup"], 120),
        ([str(binary), "setup", "--vision"], 1_800),
    ]


def test_handlers_send_displayed_bounded_custom_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, object]] = []
    context = SimpleNamespace(send_message=lambda message: sent.append(message) or message)
    monkeypatch.setattr(
        commands,
        "run_ghost_command",
        lambda mode, include_vision=False: commands.CommandResult(
            ok=True,
            text=f"{mode}:{include_vision}",
            exit_code=0,
        ),
    )

    setup_handler("vision", context)
    doctor_handler("", context)

    assert sent == [
        {
            "customType": "ghost-mcp-status",
            "content": "setup:True",
            "display": True,
            "details": {"operation": "setup", "ok": True, "exitCode": 0},
        },
        {
            "customType": "ghost-mcp-status",
            "content": "doctor:False",
            "display": True,
            "details": {"operation": "doctor", "ok": True, "exitCode": 0},
        },
    ]
