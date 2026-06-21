from __future__ import annotations

from dataclasses import dataclass

import pytest

from appv22.ai.validation import ToolValidationError, validate_tool_arguments


@dataclass
class _Tool:
    name: str
    parameters: dict


@dataclass
class _ToolCall:
    arguments: dict


def test_validate_tool_arguments_ports_pi_number_string_coercion_without_mutating_original() -> None:
    tool = _Tool(
        name="read",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "number"},
                "limit": {"type": "number"},
            },
            "required": ["path"],
        },
    )
    tool_call = _ToolCall(arguments={"path": "src/file.py", "offset": "3", "limit": "100.0"})

    validated = validate_tool_arguments(tool, tool_call)

    assert validated == {"path": "src/file.py", "offset": 3, "limit": 100.0}
    assert tool_call.arguments == {"path": "src/file.py", "offset": "3", "limit": "100.0"}


def test_validate_tool_arguments_rejects_invalid_pi_integer_coercion() -> None:
    tool = _Tool(
        name="take",
        parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )

    with pytest.raises(ToolValidationError, match="take.count: expected integer"):
        validate_tool_arguments(tool, _ToolCall(arguments={"count": "42.1"}))


def test_validate_tool_arguments_ports_pi_integer_string_coercion() -> None:
    tool = _Tool(
        name="take",
        parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )

    assert validate_tool_arguments(tool, _ToolCall(arguments={"count": "42.0"})) == {"count": 42}
