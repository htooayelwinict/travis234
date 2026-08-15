from __future__ import annotations

import json

import pytest

from travis.coding_agent.subagents import (
    CallableSubagentBackend,
    SubagentSupervisor,
    SubagentTask,
)
from travis.coding_agent.subagent_trace import _expanded_subagent_result_details


def _run(tmp_path, response: str, schema: dict[str, object]):
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda _task: response))
    task = SubagentTask(
        role="typed",
        goal="return data",
        cwd=str(tmp_path),
        result_schema=schema,
        role_definition_name="typed",
        artifact_policy="declared",
    )
    task_id = supervisor.spawn(task)
    return supervisor.wait(task_id, timeout=2)


@pytest.mark.parametrize("output", [{"ok": True}, [1, 2], "done", 7, False, None])
def test_typed_result_accepts_valid_json_values(tmp_path, output: object) -> None:
    result = _run(
        tmp_path,
        json.dumps({"summary": "validated", "output": output, "artifacts": []}),
        {},
    )

    assert result.status == "completed"
    assert result.summary == "validated"
    assert result.structured_output == output
    assert result.validation_errors == []


@pytest.mark.parametrize(
    "response,expected",
    [
        ("not-json", "valid JSON"),
        (json.dumps([1, 2]), "envelope must be an object"),
        (json.dumps({"summary": 1, "output": {}}), "summary must be a string"),
        (json.dumps({"summary": "x"}), "output is required"),
        (json.dumps({"summary": "x", "output": {}, "unknown": 1}), "unknown envelope"),
    ],
)
def test_typed_result_malformed_envelope_settles_as_failure(
    tmp_path, response: str, expected: str
) -> None:
    result = _run(tmp_path, response, {"type": "object"})

    assert result.status == "failed"
    assert any(expected in item for item in result.validation_errors)
    assert result.validation_errors == result.errors[-len(result.validation_errors) :]


def test_typed_result_rejects_oversized_envelope_and_bounds_schema_errors(tmp_path) -> None:
    oversized = _run(
        tmp_path,
        json.dumps({"summary": "x", "output": "x" * (256 * 1024)}),
        {},
    )
    mismatch = _run(
        tmp_path,
        json.dumps({"summary": "bad", "output": {"items": [1, 2, 3]}}),
        {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}}
            },
        },
    )

    assert oversized.status == "failed"
    assert any("256 KiB" in item for item in oversized.validation_errors)
    assert mismatch.status == "failed"
    assert 1 <= len(mismatch.validation_errors) <= 8
    assert all(len(item) <= 300 for item in mismatch.validation_errors)
    assert any("$.items" in item for item in mismatch.validation_errors)


def test_legacy_plain_text_result_remains_valid_without_schema(tmp_path) -> None:
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda _task: "plain text"))
    task_id = supervisor.spawn(SubagentTask(role="legacy", goal="work", cwd=str(tmp_path)))

    result = supervisor.wait(task_id, timeout=2)

    assert result.status == "completed"
    assert result.summary == "plain text"
    assert result.structured_output is None


def test_existing_expand_result_can_page_structured_output(tmp_path) -> None:
    result = _run(
        tmp_path,
        json.dumps({"summary": "ok", "output": {"approved": True}}),
        {"type": "object"},
    )

    expanded = _expanded_subagent_result_details(
        result, section="output", budget="short", offset=0
    )

    assert "output" in expanded["availableSections"]
    assert expanded["text"] == '{"approved": true}'
