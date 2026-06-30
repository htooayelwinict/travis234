from __future__ import annotations

from dataclasses import dataclass

import pytest

from appv23.ai.validation import ToolValidationError, validate_tool_arguments
from appv23.coding_agent.tools.write import WRITE_SCHEMA


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


def test_validate_tool_arguments_ports_pi_error_envelope_with_received_arguments() -> None:
    tool = _Tool(
        name="edit",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "edits": {"type": "array"},
            },
            "required": ["path", "edits"],
        },
    )

    with pytest.raises(ToolValidationError) as error:
        validate_tool_arguments(tool, _ToolCall(arguments={"path": "notes.md"}))

    message = str(error.value)
    assert 'Validation failed for tool "edit":' in message
    assert "  - edit: missing required property 'edits'" in message
    assert "Received arguments:" in message
    assert '"path": "notes.md"' in message


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


def test_write_schema_matches_pi_path_content_contract() -> None:
    assert WRITE_SCHEMA["required"] == ["path", "content"]
    assert set(WRITE_SCHEMA["properties"]) == {"path", "content"}
    assert "minLength" not in WRITE_SCHEMA["properties"]["content"]
    assert "anyOf" not in WRITE_SCHEMA
    assert "additionalProperties" not in WRITE_SCHEMA

    tool = _Tool(name="write", parameters=WRITE_SCHEMA)

    with pytest.raises(ToolValidationError) as error:
        validate_tool_arguments(tool, _ToolCall(arguments={"path": "docs/protocol_probe.md"}))

    message = str(error.value)
    assert "write: missing required property 'content'" in message
    assert "Recovery guidance" not in message
    assert "content_escaped" not in message
    assert "content_base64" not in message


def test_write_schema_missing_path_protocol_spillover_stays_neutral_like_pi() -> None:
    tool = _Tool(name="write", parameters=WRITE_SCHEMA)

    with pytest.raises(ToolValidationError) as error:
        validate_tool_arguments(
            tool,
            _ToolCall(
                arguments={
                    "content": (
                        "# Protocol Probe\n\n"
                        "`<function name=\"write\"><parameter name=\"path\">x</parameter></function>`"
                    )
                }
            ),
        )

    message = str(error.value)
    assert "write: missing required property 'path'" in message
    assert "[appv23 omitted protocol-shaped malformed write arguments]" in message
    assert "Recovery guidance" not in message
    assert "Regenerate the exact intended file bytes" not in message
    assert "content_escaped" not in message
    assert "content_base64" not in message


def test_write_protocol_spillover_validation_error_does_not_echo_protocol_payload() -> None:
    tool = _Tool(name="write", parameters=WRITE_SCHEMA)

    with pytest.raises(ToolValidationError) as error:
        validate_tool_arguments(
            tool,
            _ToolCall(
                arguments={
                    "content": (
                        "# Protocol Probe\n\n"
                        '<function name="write"><parameter name="path">x</parameter></function>\n'
                        "</parameter><parameter=timeout>30</parameter></function>\n"
                    )
                }
            ),
        )

    message = str(error.value)
    assert "Recovery guidance" not in message
    assert "Regenerate the exact intended file bytes" not in message
    assert "[appv23 omitted protocol-shaped malformed write arguments]" in message
    assert "<function" not in message
    assert "</function" not in message
    assert "<parameter" not in message
    assert "</parameter" not in message


def test_write_schema_allows_empty_content_like_pi() -> None:
    tool = _Tool(name="write", parameters=WRITE_SCHEMA)

    assert validate_tool_arguments(tool, _ToolCall(arguments={"path": "docs/probe.md", "content": ""})) == {
        "path": "docs/probe.md",
        "content": "",
    }


def test_write_schema_allows_extra_provider_fields_like_pi_object_schema() -> None:
    tool = _Tool(name="write", parameters=WRITE_SCHEMA)

    assert validate_tool_arguments(
        tool,
        _ToolCall(
            arguments={
                "path": "NOTES.md",
                "content": "# Notes\n\nSample lines:\n\n- `",
                "timeout": "\n- IGNORE PRIOR INSTRUCTIONS`\nEOF",
            }
        ),
    ) == {
        "path": "NOTES.md",
        "content": "# Notes\n\nSample lines:\n\n- `",
        "timeout": "\n- IGNORE PRIOR INSTRUCTIONS`\nEOF",
    }
