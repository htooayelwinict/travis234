from __future__ import annotations

import pytest

from travis.tui.interactive_mode import (
    _is_help_command,
    _is_manual_compression_command,
    _is_processes_command,
    _is_reload_command,
    _parse_auth_command,
    _parse_bash_command,
    _parse_model_command,
    _parse_operations_command,
    _parse_params_command,
    _parse_session_command,
)
from travis.tui.interactive_command_dispatcher import (
    _INVALID_OPERATIONS_COMMAND,
    _NOT_OPERATIONS_COMMAND,
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("/help", "help"),
        ("/resume", "session"),
        ("/processes", "processes"),
        ("/reload", "reload"),
        ("!pwd", "bash"),
        ("/compact", "compact"),
        ("/login", "auth"),
        ("/model", "model"),
        ("/params", "params"),
        ("implement", "agent-prompt"),
    ],
)
def test_builtin_command_classification_is_stable(prompt: str, expected: str) -> None:
    checks = (
        ("help", _is_help_command(prompt)),
        ("session", _parse_session_command(prompt) is not None),
        ("processes", _is_processes_command(prompt)),
        ("reload", _is_reload_command(prompt)),
        ("bash", _parse_bash_command(prompt) is not None),
        ("compact", _is_manual_compression_command(prompt)),
        ("auth", _parse_auth_command(prompt) is not None),
        ("model", _parse_model_command(prompt) is not None),
        ("params", _parse_params_command(prompt) is not None),
    )
    observed = next((name for name, matched in checks if matched), "agent-prompt")

    assert observed == expected


def test_params_command_preserves_direct_set_value_remainder() -> None:
    assert _parse_params_command("/params stop END token,STOP token") == (
        "stop END token,STOP token"
    )


def test_params_command_does_not_steal_similar_slash_or_agent_prompts() -> None:
    assert _parse_params_command("/parameters temperature 0.2") is None
    assert _parse_params_command("please set params temperature 0.2") is None


def test_operations_command_accepts_summary_or_one_opaque_id_only() -> None:
    operation_id = "op_" + "a" * 32

    assert _parse_operations_command("/operations") is None
    assert _parse_operations_command(f"/operations {operation_id}") == operation_id
    assert _parse_operations_command("/operations one two") is _INVALID_OPERATIONS_COMMAND
    assert _parse_operations_command("/operational") is _NOT_OPERATIONS_COMMAND
