from __future__ import annotations

import pytest

from travis.tui.interactive_mode import (
    _is_help_command,
    _is_manual_compression_command,
    _is_processes_command,
    _parse_allow_command,
    _parse_auth_command,
    _parse_bash_command,
    _parse_model_command,
    _parse_params_command,
    _parse_session_command,
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("/help", "help"),
        ("/resume", "session"),
        ("/processes", "processes"),
        ("!pwd", "bash"),
        ("/compact", "compact"),
        ("/login", "auth"),
        ("/model", "model"),
        ("/params", "params"),
        ("/allow package_install 1", "allow"),
        ("implement", "agent-prompt"),
    ],
)
def test_builtin_command_classification_is_stable(prompt: str, expected: str) -> None:
    checks = (
        ("help", _is_help_command(prompt)),
        ("session", _parse_session_command(prompt) is not None),
        ("processes", _is_processes_command(prompt)),
        ("bash", _parse_bash_command(prompt) is not None),
        ("compact", _is_manual_compression_command(prompt)),
        ("auth", _parse_auth_command(prompt) is not None),
        ("model", _parse_model_command(prompt) is not None),
        ("params", _parse_params_command(prompt) is not None),
        ("allow", _parse_allow_command(prompt) is not None),
    )
    observed = next((name for name, matched in checks if matched), "agent-prompt")

    assert observed == expected
