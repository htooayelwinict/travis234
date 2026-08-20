"""Direct characterization of process argument and terminal-result ownership."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import pytest

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifact_store import ArtifactPromotionError
from travis.coding_agent.artifacts import ArtifactRef, ArtifactRegistry
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner, ProcessSnapshot, ProcessState
from travis.coding_agent.tools import process as process_tool_module
from travis.coding_agent.tools.truncate import TruncationResult


_SESSION_ID = "proc_0123456789abcdef0123456789abcdef"
_COLLAPSED_SESSION_ID = "proc0123456789abcdef0123456789abcdef"


def _result_text(result: AgentToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _valid_arguments(action: str) -> dict[str, object]:
    if action == "poll":
        return {
            "action": "poll",
            "session_id": _SESSION_ID,
            "cursor": 3,
            "yield_time_ms": 0,
            "max_bytes": 51_200,
        }
    if action == "wait":
        return {
            "action": "wait",
            "session_id": _SESSION_ID,
            "cursor": 3,
            "wait_time_ms": 60_000,
            "max_bytes": 1_024,
        }
    if action in {"write", "write_raw"}:
        return {
            "action": action,
            "session_id": _SESSION_ID,
            "input": "payload",
            "eof": False,
            "yield_time_ms": 30_000,
        }
    if action == "resize":
        return {
            "action": "resize",
            "session_id": _SESSION_ID,
            "rows": 24,
            "cols": 80,
        }
    if action in {"interrupt", "terminate"}:
        return {
            "action": action,
            "session_id": _SESSION_ID,
            "yield_time_ms": 1_000,
        }
    if action == "kill":
        return {"action": "kill", "session_id": _SESSION_ID}
    return {"action": "list"}


@pytest.mark.parametrize("value", (None, 7, "poll", ["action", "poll"], object()))
def test_prepare_non_mapping_returns_the_identical_value(value: object) -> None:
    assert process_tool_module.prepare_process_arguments(value) is value


def test_prepare_copies_non_dict_mapping_without_mutating_source() -> None:
    source = {
        "action": "poll",
        "sessionId": _COLLAPSED_SESSION_ID,
        "nextCursor": "4",
        "yieldTimeMs": "1000",
    }
    mapping = MappingProxyType(source)

    prepared = process_tool_module.prepare_process_arguments(mapping)

    assert prepared == {
        "action": "poll",
        "session_id": _SESSION_ID,
        "cursor": 4,
        "yield_time_ms": 1_000,
    }
    assert prepared is not mapping
    assert source == {
        "action": "poll",
        "sessionId": _COLLAPSED_SESSION_ID,
        "nextCursor": "4",
        "yieldTimeMs": "1000",
    }


def test_prepare_mutates_and_returns_plain_dict_only_after_success() -> None:
    arguments: dict[str, object] = {
        "action": "poll",
        "nextCursor": "4",
        "sessionId": _COLLAPSED_SESSION_ID,
        "maxBytes": "1024",
        "futureField": "retained",
    }

    prepared = process_tool_module.prepare_process_arguments(arguments)

    assert prepared is arguments
    assert arguments == {
        "action": "poll",
        "futureField": "retained",
        "cursor": 4,
        "session_id": _SESSION_ID,
        "max_bytes": 1_024,
    }
    assert list(arguments) == [
        "action",
        "futureField",
        "cursor",
        "session_id",
        "max_bytes",
    ]


@pytest.mark.parametrize(
    ("alias", "canonical", "value", "expected"),
    (
        ("sessionid", "session_id", _COLLAPSED_SESSION_ID, _SESSION_ID),
        ("process-id", "session_id", _COLLAPSED_SESSION_ID, _SESSION_ID),
        ("nextCursor", "cursor", "7", 7),
        ("yield-time-ms", "yield_time_ms", "8", 8),
        ("waitTimeMs", "wait_time_ms", "900", 900),
        ("maxBytes", "max_bytes", "2048", 2_048),
    ),
)
def test_prepare_normalizes_every_compatibility_alias(
    alias: str,
    canonical: str,
    value: object,
    expected: object,
) -> None:
    arguments: dict[str, object] = {"action": "list", alias: value}

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "list",
        canonical: expected,
    }


def test_prepare_alias_conflict_uses_iteration_order_and_leaves_input_unchanged() -> None:
    arguments: dict[str, object] = {
        "action": "poll",
        "sessionId": _SESSION_ID,
        "process-id": "proc_other",
        "cursor": 0,
    }
    before = dict(arguments)

    with pytest.raises(ValueError) as raised:
        process_tool_module.prepare_process_arguments(arguments)

    assert str(raised.value) == "conflicting process fields: session_id and process-id"
    assert arguments == before


def test_prepare_deduplicates_equivalent_aliases_after_value_normalization() -> None:
    arguments: dict[str, object] = {
        "action": "wait",
        "session_id": _SESSION_ID,
        "sessionId": _COLLAPSED_SESSION_ID,
        "cursor": 2,
        "wait_time_ms": 60_000,
        "waittimems": "60000",
    }

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "wait",
        "session_id": _SESSION_ID,
        "cursor": 2,
        "wait_time_ms": 60_000,
    }


def test_prepare_repairs_integer_fields_and_only_exact_collapsed_session_ids() -> None:
    arguments: dict[str, object] = {
        "action": "list",
        "session_id": _COLLAPSED_SESSION_ID,
        "cursor": " -4 ",
        "yield_time_ms": " 0 ",
        "wait_time_ms": "1.5",
        "max_bytes": True,
        "rows": "24",
        "cols": 80,
    }

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "list",
        "session_id": _SESSION_ID,
        "cursor": -4,
        "yield_time_ms": 0,
        "wait_time_ms": "1.5",
        "max_bytes": True,
        "rows": 24,
        "cols": 80,
    }
    assert process_tool_module.prepare_process_arguments(
        {"action": "list", "session_id": "procABCDEF0123456789ABCDEF0123456789"}
    ) == {"action": "list", "session_id": "procABCDEF0123456789ABCDEF0123456789"}


def test_prepare_start_rejection_is_exact_and_precedes_input_mutation() -> None:
    arguments: dict[str, object] = {
        "action": "start",
        "yieldTime-ms": "12",
        "command": "python worker.py",
    }
    before = dict(arguments)

    with pytest.raises(ValueError) as raised:
        process_tool_module.prepare_process_arguments(arguments)

    assert str(raised.value) == (
        "process has no start action; start the command with bash using yield_time_ms and "
        "stdin=open, then control the returned session_id with process"
    )
    assert arguments == before


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            {"action": "write_line", "session_id": _SESSION_ID, "input": "line"},
            {"action": "write", "session_id": _SESSION_ID, "input": "line"},
        ),
        (
            {"action": "write", "session_id": _SESSION_ID, "data": "line"},
            {"action": "write", "session_id": _SESSION_ID, "input": "line"},
        ),
        (
            {"action": "write", "session_id": _SESSION_ID, "content": "line\n"},
            {"action": "write_raw", "session_id": _SESSION_ID, "input": "line\n"},
        ),
        (
            {"action": "write", "session_id": _SESSION_ID, "input": "line\r"},
            {"action": "write_raw", "session_id": _SESSION_ID, "input": "line\r"},
        ),
        (
            {"action": "write", "session_id": _SESSION_ID, "input": 7},
            {"action": "write", "session_id": _SESSION_ID, "input": 7},
        ),
        (
            {"action": "write_raw", "session_id": _SESSION_ID, "data": "line\n"},
            {"action": "write_raw", "session_id": _SESSION_ID, "input": "line\n"},
        ),
    ),
)
def test_prepare_write_alias_payload_and_newline_rules(
    arguments: dict[str, object],
    expected: dict[str, object],
) -> None:
    assert process_tool_module.prepare_process_arguments(arguments) == expected


def test_prepare_rejects_multiple_payload_fields_before_mutating_input() -> None:
    arguments: dict[str, object] = {
        "action": "write",
        "session_id": _SESSION_ID,
        "input": "one",
        "data": "two",
        "content": "three",
    }
    before = dict(arguments)

    with pytest.raises(ValueError) as raised:
        process_tool_module.prepare_process_arguments(arguments)

    assert str(raised.value) == ("process write received multiple stdin payload fields; use only input")
    assert arguments == before


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": "4",
                "yield_time_ms": "120000",
            },
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": 4,
                "wait_time_ms": 60_000,
            },
        ),
        (
            {
                "action": "poll",
                "session_id": _SESSION_ID,
                "cursor": 4,
                "yield_time_ms": 500,
                "wait_time_ms": 70_000,
            },
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": 4,
                "wait_time_ms": 60_000,
            },
        ),
        (
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": 4,
                "wait_time_ms": -5,
            },
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": 4,
                "wait_time_ms": -5,
            },
        ),
    ),
)
def test_prepare_wait_poll_conversion_and_upper_clamping(
    arguments: dict[str, object],
    expected: dict[str, object],
) -> None:
    assert process_tool_module.prepare_process_arguments(arguments) == expected


def test_prepare_rejects_ambiguous_wait_timing_before_mutating_input() -> None:
    arguments: dict[str, object] = {
        "action": "wait",
        "session_id": _SESSION_ID,
        "cursor": 4,
        "yield_time_ms": 1_000,
        "wait_time_ms": 2_000,
    }
    before = dict(arguments)

    with pytest.raises(ValueError) as raised:
        process_tool_module.prepare_process_arguments(arguments)

    assert str(raised.value) == "wait action received both wait_time_ms and yield_time_ms"
    assert arguments == before


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            {"action": "poll", "cursor": 0},
            (
                "poll requires session_id; use tool process with "
                '{"action":"poll","session_id":"<id>","cursor":<nextCursor>,"yield_time_ms":1000}'
            ),
        ),
        (
            {"action": "wait", "session_id": "", "cursor": 0},
            (
                "wait requires session_id; use tool process with "
                '{"action":"wait","session_id":"<id>","cursor":<nextCursor>,"wait_time_ms":60000}'
            ),
        ),
        (
            {"action": "poll", "session_id": _SESSION_ID, "cursor": True},
            (
                "cursor must be a nonnegative integer for the poll action; use tool process with "
                '{"action":"poll","session_id":"<id>","cursor":<nextCursor>,"yield_time_ms":1000}'
            ),
        ),
        (
            {"action": "wait", "session_id": _SESSION_ID, "cursor": -1},
            (
                "cursor must be a nonnegative integer for the wait action; use tool process with "
                '{"action":"wait","session_id":"<id>","cursor":<nextCursor>,"wait_time_ms":60000}'
            ),
        ),
    ),
)
def test_prepare_session_and_cursor_errors_are_exact_and_nonmutating(
    arguments: dict[str, object],
    expected: str,
) -> None:
    before = dict(arguments)

    with pytest.raises(ValueError) as raised:
        process_tool_module.prepare_process_arguments(arguments)

    assert str(raised.value) == expected
    assert arguments == before


@pytest.mark.parametrize("action", process_tool_module.PROCESS_ACTIONS)
def test_validate_accepts_every_action_and_returns_an_equal_copy(action: str) -> None:
    arguments = _valid_arguments(action)

    validated = process_tool_module._validate_args(arguments)

    assert validated == arguments
    assert validated is not arguments


def test_validate_normalizes_plain_dict_before_returning_a_separate_copy() -> None:
    arguments: dict[str, object] = {
        "action": "poll",
        "sessionId": _COLLAPSED_SESSION_ID,
        "nextCursor": "4",
        "yieldTimeMs": "1000",
    }

    validated = process_tool_module._validate_args(arguments)

    assert arguments == {
        "action": "poll",
        "session_id": _SESSION_ID,
        "cursor": 4,
        "yield_time_ms": 1_000,
    }
    assert validated == arguments
    assert validated is not arguments


def test_validate_keeps_non_dict_mapping_unchanged_and_returns_a_dict() -> None:
    source = {"action": "kill", "session_id": _SESSION_ID}
    mapping = MappingProxyType(source)

    validated = process_tool_module._validate_args(mapping)

    assert validated == source
    assert isinstance(validated, dict)
    assert source == {"action": "kill", "session_id": _SESSION_ID}


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (None, "process arguments must be an object"),
        (
            {},
            "action must be one of: poll, wait, write, write_raw, resize, interrupt, terminate, kill, list",
        ),
        (
            {"action": "unknown", "z": 1},
            "action must be one of: poll, wait, write, write_raw, resize, interrupt, terminate, kill, list",
        ),
        (
            {"action": "poll", "session_id": _SESSION_ID, "cursor": 0, "z": 1, "a": 2},
            "poll does not accept a",
        ),
    ),
)
def test_validate_object_action_and_unexpected_field_errors_are_exact(
    arguments: object,
    expected: str,
) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == expected


@pytest.mark.parametrize(
    ("action", "expected"),
    (
        (
            "poll",
            (
                "poll requires session_id; use tool process with "
                '{"action":"poll","session_id":"<id>","cursor":<nextCursor>,"yield_time_ms":1000}'
            ),
        ),
        (
            "wait",
            (
                "wait requires session_id; use tool process with "
                '{"action":"wait","session_id":"<id>","cursor":<nextCursor>,"wait_time_ms":60000}'
            ),
        ),
        (
            "write",
            'write requires session_id; use tool process with {"action":"write","session_id":"<id>","input":"<line>"}',
        ),
        (
            "write_raw",
            (
                "write_raw requires session_id; use tool process with "
                '{"action":"write_raw","session_id":"<id>","input":"<exact-input>"}'
            ),
        ),
        (
            "resize",
            'resize requires session_id; use tool process with {"action":"resize","session_id":"<id>","rows":24,"cols":80}',
        ),
        (
            "interrupt",
            'interrupt requires session_id; use tool process with {"action":"interrupt","session_id":"<id>"}',
        ),
        (
            "terminate",
            'terminate requires session_id; use tool process with {"action":"terminate","session_id":"<id>"}',
        ),
        (
            "kill",
            'kill requires session_id; use tool process with {"action":"kill","session_id":"<id>"}',
        ),
    ),
)
def test_validate_requires_session_string_for_every_non_list_action(
    action: str,
    expected: str,
) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args({"action": action})

    assert str(raised.value) == expected


@pytest.mark.parametrize("session_id", (None, "", 7, True))
def test_validate_rejects_invalid_required_session_values(session_id: object) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args({"action": "kill", "session_id": session_id})

    assert str(raised.value) == (
        'kill requires session_id; use tool process with {"action":"kill","session_id":"<id>"}'
    )


@pytest.mark.parametrize("action", ("poll", "wait"))
@pytest.mark.parametrize("cursor", (None, -1, True, 1.5, "not-an-integer"))
def test_validate_cursor_errors_are_exact(action: str, cursor: object) -> None:
    arguments: dict[str, object] = {
        "action": action,
        "session_id": _SESSION_ID,
        "cursor": cursor,
    }

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    example = process_tool_module.PROCESS_POLL_EXAMPLE if action == "poll" else process_tool_module.PROCESS_WAIT_EXAMPLE
    assert str(raised.value) == (
        f"cursor must be a nonnegative integer for the {action} action; use tool process with {example}"
    )


def test_validate_direct_cursor_branch_keeps_exact_compatibility_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = {"action": "poll", "session_id": _SESSION_ID, "cursor": -1}
    monkeypatch.setattr(
        process_tool_module,
        "prepare_process_arguments",
        lambda value: value,
    )

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == "cursor must be a nonnegative integer"


@pytest.mark.parametrize("action", ("write", "write_raw"))
@pytest.mark.parametrize("input_value", (None, 7, True))
def test_validate_requires_string_write_input(
    action: str,
    input_value: object,
) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args({"action": action, "session_id": _SESSION_ID, "input": input_value})

    expected_input = "<line>" if action == "write" else "<exact-input>"
    assert str(raised.value) == (
        f"{action} requires input; use tool process with "
        f'{{"action":"{action}","session_id":"<id>","input":"{expected_input}"}}'
    )


@pytest.mark.parametrize("action", ("write", "write_raw"))
def test_validate_allows_empty_write_input(action: str) -> None:
    arguments = {"action": action, "session_id": _SESSION_ID, "input": ""}

    assert process_tool_module._validate_args(arguments) == arguments


def test_validate_promotes_newline_write_before_validation() -> None:
    arguments = {"action": "write", "session_id": _SESSION_ID, "input": "one\ntwo"}

    assert process_tool_module._validate_args(arguments) == {
        "action": "write_raw",
        "session_id": _SESSION_ID,
        "input": "one\ntwo",
    }
    assert arguments["action"] == "write_raw"


def test_validate_direct_write_newline_branch_keeps_exact_compatibility_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_tool_module, "prepare_process_arguments", lambda value: value)

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args({"action": "write", "session_id": _SESSION_ID, "input": "one\ntwo"})

    assert str(raised.value) == (
        "write input must contain exactly one line without a newline; use write_raw for exact input"
    )


@pytest.mark.parametrize("eof", (None, 0, 1, "false"))
def test_validate_rejects_non_boolean_eof(eof: object) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(
            {
                "action": "write_raw",
                "session_id": _SESSION_ID,
                "input": "payload",
                "eof": eof,
            }
        )

    assert str(raised.value) == "eof must be a boolean"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            {"action": "resize", "session_id": _SESSION_ID, "cols": 80},
            'resize requires rows; use tool process with {"action":"resize","session_id":"<id>","rows":24,"cols":80}',
        ),
        (
            {"action": "resize", "session_id": _SESSION_ID, "rows": True, "cols": 80},
            'resize requires rows; use tool process with {"action":"resize","session_id":"<id>","rows":24,"cols":80}',
        ),
        (
            {"action": "resize", "session_id": _SESSION_ID, "rows": 24},
            'resize requires cols; use tool process with {"action":"resize","session_id":"<id>","rows":24,"cols":80}',
        ),
        (
            {
                "action": "resize",
                "session_id": _SESSION_ID,
                "rows": 24,
                "cols": "eighty",
            },
            'resize requires cols; use tool process with {"action":"resize","session_id":"<id>","rows":24,"cols":80}',
        ),
    ),
)
def test_validate_resize_required_integer_errors_are_exact(
    arguments: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == expected


def test_validate_private_resize_keeps_integer_values_outside_schema_bounds() -> None:
    arguments = {
        "action": "resize",
        "session_id": _SESSION_ID,
        "rows": 1,
        "cols": 501,
    }

    assert process_tool_module._validate_args(arguments) == arguments


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("yield_time_ms", -1, "yield_time_ms must be an integer between 0 and 30000"),
        ("yield_time_ms", 30_001, "yield_time_ms must be an integer between 0 and 30000"),
        ("yield_time_ms", True, "yield_time_ms must be an integer between 0 and 30000"),
        ("wait_time_ms", 999, "wait_time_ms must be an integer between 1000 and 60000"),
        ("wait_time_ms", True, "wait_time_ms must be an integer between 1000 and 60000"),
        ("max_bytes", 1_023, "max_bytes must be an integer between 1024 and 51200"),
        ("max_bytes", 51_201, "max_bytes must be an integer between 1024 and 51200"),
        ("max_bytes", False, "max_bytes must be an integer between 1024 and 51200"),
    ),
)
def test_validate_range_and_bool_errors_are_exact(
    field: str,
    value: object,
    expected: str,
) -> None:
    arguments = _valid_arguments("wait" if field == "wait_time_ms" else "poll")
    arguments[field] = value

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == expected


def test_validate_direct_wait_upper_bound_branch_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_tool_module, "prepare_process_arguments", lambda value: value)

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": 0,
                "wait_time_ms": 60_001,
            }
        )

    assert str(raised.value) == "wait_time_ms must be an integer between 1000 and 60000"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            {"action": "unknown", "a": 1},
            "action must be one of: poll, wait, write, write_raw, resize, interrupt, terminate, kill, list",
        ),
        (
            {"action": "poll", "cursor": -1, "yield_time_ms": -1, "extra": True},
            (f"poll requires session_id; use tool process with {process_tool_module.PROCESS_POLL_EXAMPLE}"),
        ),
        (
            {"action": "poll", "cursor": -1, "yield_time_ms": -1},
            (
                "poll requires session_id; use tool process with "
                '{"action":"poll","session_id":"<id>","cursor":<nextCursor>,"yield_time_ms":1000}'
            ),
        ),
        (
            {
                "action": "poll",
                "session_id": _SESSION_ID,
                "cursor": -1,
                "yield_time_ms": -1,
            },
            (
                "cursor must be a nonnegative integer for the poll action; "
                f"use tool process with {process_tool_module.PROCESS_POLL_EXAMPLE}"
            ),
        ),
        (
            {
                "action": "write_raw",
                "session_id": _SESSION_ID,
                "eof": "false",
                "yield_time_ms": -1,
            },
            (
                "write_raw requires input; use tool process with "
                '{"action":"write_raw","session_id":"<id>","input":"<exact-input>"}'
            ),
        ),
        (
            {
                "action": "write_raw",
                "session_id": _SESSION_ID,
                "input": "payload",
                "eof": "false",
                "yield_time_ms": -1,
            },
            "eof must be a boolean",
        ),
        (
            {
                "action": "resize",
                "session_id": _SESSION_ID,
                "cols": True,
            },
            'resize requires rows; use tool process with {"action":"resize","session_id":"<id>","rows":24,"cols":80}',
        ),
        (
            {
                "action": "wait",
                "session_id": _SESSION_ID,
                "cursor": 0,
                "wait_time_ms": 999,
                "max_bytes": 0,
            },
            "wait_time_ms must be an integer between 1000 and 60000",
        ),
        (
            {
                "action": "poll",
                "session_id": _SESSION_ID,
                "cursor": 0,
                "yield_time_ms": -1,
                "max_bytes": 0,
            },
            "yield_time_ms must be an integer between 0 and 30000",
        ),
    ),
)
def test_validate_first_error_order_and_text_are_exact(
    arguments: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == expected


def test_validate_error_after_preparation_keeps_normalized_input_mutation() -> None:
    arguments: dict[str, object] = {
        "action": "poll",
        "sessionId": _COLLAPSED_SESSION_ID,
        "nextCursor": "0",
        "futureField": True,
    }

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == "poll does not accept futureField"
    assert arguments == {
        "action": "poll",
        "futureField": True,
        "session_id": _SESSION_ID,
        "cursor": 0,
    }


def test_validate_preparation_error_leaves_original_input_unchanged() -> None:
    arguments: dict[str, object] = {
        "action": "wait",
        "session_id": _SESSION_ID,
        "sessionId": "proc_other",
        "cursor": 0,
    }
    before = dict(arguments)

    with pytest.raises(ValueError) as raised:
        process_tool_module._validate_args(arguments)

    assert str(raised.value) == "conflicting process fields: session_id and sessionId"
    assert arguments == before


def _tail(*, content: str = "tail output", truncated: bool = False) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=truncated,
        truncated_by="bytes" if truncated else None,
        output_lines=1,
        total_lines=4 if truncated else 1,
        first_line_exceeds_limit=False,
        total_bytes=999 if truncated else len(content.encode("utf-8")),
        output_bytes=len(content.encode("utf-8")),
        last_line_partial=truncated,
        max_lines=2_000,
        max_bytes=51_200,
    )


def _snapshot(*, full_output_path: str | None = None) -> ProcessSnapshot:
    return ProcessSnapshot(
        session_id=_SESSION_ID,
        state=ProcessState.EXITED,
        output="incremental output must be replaced",
        cursor=7,
        next_cursor=11,
        output_size=99,
        exit_code=3,
        tty=True,
        elapsed_ms=123,
        command="python worker.py",
        cwd="/workspace",
        suggested_poll_delay_ms=250,
        durable_output=True,
        full_output_path=full_output_path,
        failure_code="recorded-failure-code",
    )


class _RecordedProcessService(ProcessSessionService):
    def __init__(
        self,
        directory: Path,
        tail: TruncationResult,
        events: list[tuple[object, ...]],
        *,
        export_path: Path | None = None,
    ) -> None:
        super().__init__(directory=directory)
        self.recorded_tail = tail
        self.events = events
        self.export_path = export_path

    def tail_snapshot(
        self,
        owner: ProcessOwner,
        session_id: str,
    ) -> TruncationResult:
        self.events.append(("tail", owner, session_id))
        return self.recorded_tail

    def export_output(
        self,
        owner: ProcessOwner,
        session_id: str,
        directory: str | Path,
    ) -> Path:
        self.events.append(("export", owner, session_id, str(directory)))
        if self.export_path is None:
            raise AssertionError("unexpected export")
        self.export_path.write_text("complete output", encoding="utf-8")
        return self.export_path


_PromotionFailure = Literal["artifact", "os"]


class _RecordedArtifacts(ArtifactRegistry):
    def __init__(
        self,
        *,
        durable: bool,
        events: list[tuple[object, ...]],
        failure: _PromotionFailure | None = None,
    ) -> None:
        super().__init__()
        self.recorded_durable = durable
        self.events = events
        self.failure = failure

    @property
    def is_durable(self) -> bool:
        return self.recorded_durable

    def register(
        self,
        path: Path,
        kind: str,
        access: Literal["read"] = "read",
        remove_on_close: bool = True,
    ) -> ArtifactRef:
        self.events.append(("register", path, kind, access, remove_on_close))
        return ArtifactRef(
            id="artifact-transient",
            path=path,
            kind=kind,
            access=access,
            remove_on_close=remove_on_close,
        )

    def promote(
        self,
        path: Path,
        kind: str,
        *,
        session_entry_id: str | None = None,
        tool_call_id: str | None = None,
        retained: bool = False,
    ) -> ArtifactRef:
        del session_entry_id, retained
        self.events.append(("promote", path, kind, tool_call_id, path.exists()))
        if self.failure == "artifact":
            raise ArtifactPromotionError(
                "physical_limit",
                " Artifact   storage\nlimit reached ",
            )
        if self.failure == "os":
            raise OSError("disk unavailable")
        return ArtifactRef(
            id="artifact-durable",
            path=path,
            kind=kind,
            remove_on_close=False,
        )


def test_terminal_result_uses_tail_snapshot_and_reconstructs_exact_terminal_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    full_output = tmp_path / "service-output.txt"
    full_output.write_text("complete output", encoding="utf-8")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(tmp_path / "processes", _tail(), events)
    observed_snapshots: list[ProcessSnapshot] = []
    original_snapshot_result = process_tool_module._snapshot_result

    def record_snapshot(
        snapshot: ProcessSnapshot,
        *,
        include_poll_hint: bool = True,
    ) -> AgentToolResult:
        observed_snapshots.append(snapshot)
        return original_snapshot_result(snapshot, include_poll_hint=include_poll_hint)

    monkeypatch.setattr(process_tool_module, "_snapshot_result", record_snapshot)
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(full_output_path=str(full_output)),
            None,
            tool_call_id="terminal-call",
        )
    finally:
        service.close()

    assert events == [("tail", owner, _SESSION_ID)]
    assert observed_snapshots == [
        ProcessSnapshot(
            session_id=_SESSION_ID,
            state=ProcessState.EXITED,
            output="tail output",
            cursor=7,
            next_cursor=99,
            output_size=99,
            exit_code=3,
            tty=True,
            elapsed_ms=123,
            command="python worker.py",
            cwd="/workspace",
            suggested_poll_delay_ms=250,
            durable_output=True,
            full_output_path=str(full_output),
            failure_code="recorded-failure-code",
        )
    ]
    assert result.details == {
        "status": "exited",
        "sessionId": _SESSION_ID,
        "cursor": 7,
        "nextCursor": 99,
        "outputSize": 99,
        "exitCode": 3,
        "tty": True,
        "elapsedMs": 123,
        "suggestedPollDelayMs": 250,
        "durableOutput": True,
        "fullOutputPath": str(full_output),
        "failureCode": "recorded-failure-code",
    }
    assert _result_text(result) == (
        f"tail output\n\nProcess {_SESSION_ID} exited with code 3; next cursor 99, output size 99."
    )


def test_terminal_result_without_path_or_truncation_has_no_artifact_details(
    tmp_path: Path,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(tmp_path / "processes", _tail(), events)
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(),
            None,
        )
    finally:
        service.close()

    assert events == [("tail", owner, _SESSION_ID)]
    assert "fullOutputPath" not in result.details
    assert "artifactId" not in result.details
    assert "artifactUnavailable" not in result.details
    assert "truncation" not in result.details


def test_terminal_result_exports_truncated_output_without_artifact_registry(
    tmp_path: Path,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    exported = tmp_path / "exported-output.txt"
    tail = _tail(content="bounded tail", truncated=True)
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        tail,
        events,
        export_path=exported,
    )
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(),
            None,
        )
    finally:
        service.close()

    assert events == [
        ("tail", owner, _SESSION_ID),
        ("export", owner, _SESSION_ID, tempfile.gettempdir()),
    ]
    assert exported.exists()
    assert result.details["fullOutputPath"] == str(exported)
    assert result.details["truncation"] == {
        "content": "bounded tail",
        "truncated": True,
        "truncatedBy": "bytes",
        "totalLines": 4,
        "totalBytes": 999,
        "outputLines": 1,
        "outputBytes": 12,
        "lastLinePartial": True,
        "firstLineExceedsLimit": False,
        "maxLines": 2_000,
        "maxBytes": 51_200,
    }
    assert "artifactId" not in result.details


def test_terminal_result_truncated_without_registry_replaces_preexisting_path(
    tmp_path: Path,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    preexisting = tmp_path / "service-output.txt"
    exported = tmp_path / "exported-output.txt"
    preexisting.write_text("service output", encoding="utf-8")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(truncated=True),
        events,
        export_path=exported,
    )
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(full_output_path=str(preexisting)),
            None,
        )
    finally:
        service.close()

    assert events == [
        ("tail", owner, _SESSION_ID),
        ("export", owner, _SESSION_ID, tempfile.gettempdir()),
    ]
    assert result.details["fullOutputPath"] == str(exported)
    assert preexisting.exists()
    assert exported.exists()


@pytest.mark.parametrize("preexisting", (True, False))
def test_terminal_result_transient_registration_preserves_artifact_ownership_flags(
    tmp_path: Path,
    preexisting: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    output_path = tmp_path / ("service-output.txt" if preexisting else "exported-output.txt")
    if preexisting:
        output_path.write_text("complete output", encoding="utf-8")
    tail = _tail(truncated=not preexisting)
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        tail,
        events,
        export_path=None if preexisting else output_path,
    )
    artifacts = _RecordedArtifacts(durable=False, events=events)
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(full_output_path=str(output_path) if preexisting else None),
            artifacts,
            tool_call_id="transient-call",
        )
    finally:
        service.close()
        artifacts.close(remove_files=False)

    expected_events: list[tuple[object, ...]] = [("tail", owner, _SESSION_ID)]
    if not preexisting:
        expected_events.append(("export", owner, _SESSION_ID, tempfile.gettempdir()))
    expected_events.append(("register", output_path, "process-output", "read", not preexisting))
    assert events == expected_events
    assert result.details["fullOutputPath"] == str(output_path)
    assert result.details["artifactId"] == "artifact-transient"
    assert _result_text(result).endswith(
        "[Full output artifact: artifact-transient. Use read with path=artifact-transient, "
        "byte_offset=0, byte_limit=51200.]"
    )


@pytest.mark.parametrize("preexisting", (True, False))
def test_terminal_result_durable_promotion_hides_path_and_forwards_tool_call(
    tmp_path: Path,
    preexisting: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    output_path = tmp_path / ("service-output.txt" if preexisting else "exported-output.txt")
    if preexisting:
        output_path.write_text("complete output", encoding="utf-8")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(truncated=not preexisting),
        events,
        export_path=None if preexisting else output_path,
    )
    artifacts = _RecordedArtifacts(durable=True, events=events)
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(full_output_path=str(output_path) if preexisting else None),
            artifacts,
            tool_call_id="durable-call",
        )
    finally:
        service.close()
        artifacts.close(remove_files=False)

    expected_events: list[tuple[object, ...]] = [("tail", owner, _SESSION_ID)]
    if not preexisting:
        expected_events.append(("export", owner, _SESSION_ID, tempfile.gettempdir()))
    expected_events.append(("promote", output_path, "process-output", "durable-call", True))
    assert events == expected_events
    assert result.details["artifactId"] == "artifact-durable"
    assert "fullOutputPath" not in result.details
    assert "artifactUnavailable" not in result.details
    assert output_path.exists() is preexisting
    assert _result_text(result).endswith(
        "[Full output artifact: artifact-durable. Use read with path=artifact-durable, "
        "byte_offset=0, byte_limit=51200.]"
    )


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (
            "artifact",
            {
                "code": "physical_limit",
                "message": "Artifact storage limit reached",
            },
        ),
        (
            "os",
            {
                "code": "unavailable",
                "message": "Artifact storage is unavailable",
            },
        ),
    ),
)
def test_terminal_result_durable_failure_shapes_error_and_cleans_temporary_export(
    tmp_path: Path,
    failure: _PromotionFailure,
    expected: dict[str, str],
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    exported = tmp_path / "exported-output.txt"
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(content="bounded tail", truncated=True),
        events,
        export_path=exported,
    )
    artifacts = _RecordedArtifacts(
        durable=True,
        events=events,
        failure=failure,
    )
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(),
            artifacts,
            tool_call_id="failed-call",
        )
    finally:
        service.close()
        artifacts.close(remove_files=False)

    assert events == [
        ("tail", owner, _SESSION_ID),
        ("export", owner, _SESSION_ID, tempfile.gettempdir()),
        ("promote", exported, "process-output", "failed-call", True),
    ]
    assert not exported.exists()
    assert result.details["artifactUnavailable"] == expected
    assert "artifactId" not in result.details
    assert "fullOutputPath" not in result.details
    assert _result_text(result).endswith(f"[Full output artifact unavailable ({expected['code']}).]")


@pytest.mark.parametrize("preexisting", (True, False))
def test_terminal_result_real_transient_registry_cleanup_matches_path_ownership(
    tmp_path: Path,
    preexisting: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    output_path = tmp_path / ("service-output.txt" if preexisting else "exported-output.txt")
    if preexisting:
        output_path.write_text("complete output", encoding="utf-8")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(truncated=not preexisting),
        events,
        export_path=None if preexisting else output_path,
    )
    artifacts = ArtifactRegistry()
    try:
        result = process_tool_module._terminal_process_result(
            service,
            owner,
            _snapshot(full_output_path=str(output_path) if preexisting else None),
            artifacts,
        )
        assert artifacts.resolve_read(result.details["artifactId"]) == output_path
    finally:
        service.close()
        artifacts.close(remove_files=True)

    assert output_path.exists() is preexisting
