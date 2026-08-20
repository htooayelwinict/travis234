from __future__ import annotations

import pytest

from travis.coding_agent.subagent_result_types import SubagentResult


def _result(**overrides: object) -> SubagentResult:
    values: dict[str, object] = {
        "task_id": "subagent-fixed",
        "backend": "internal",
        "role": "reviewer",
        "status": "completed",
        "summary": "done",
    }
    values.update(overrides)
    return SubagentResult(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"tool_trace": "invalid"}, "tool_trace must be a list of dicts"),
        ({"tool_trace": [{"tool": "read"}, "invalid"]}, "tool_trace must be a list of dicts"),
        ({"validation_errors": "invalid"}, "validation_errors must be a list of strings"),
        ({"validation_errors": ["first", 2]}, "validation_errors must be a list of strings"),
    ),
)
def test_subagent_result_rejects_malformed_trace_and_validation_errors(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _result(**overrides)


def test_subagent_result_serializes_full_contract_without_aliasing_collections() -> None:
    result = _result(
        final_response="final",
        files_changed=["app.py"],
        artifacts=["report.md"],
        errors=["warning"],
        usage={"inputTokens": 12},
        child_session_id="child-1",
        raw_log_path="logs/child.jsonl",
        started_at_ms=100,
        ended_at_ms=145,
        tool_trace=[{"tool": "read"}],
        structured_output={"approved": True},
        validation_errors=["schema warning"],
    )

    payload = result.as_dict()

    assert payload == {
        "taskId": "subagent-fixed",
        "backend": "internal",
        "role": "reviewer",
        "status": "completed",
        "summary": "done",
        "finalResponse": "final",
        "filesChanged": ["app.py"],
        "artifacts": ["report.md"],
        "errors": ["warning"],
        "usage": {"inputTokens": 12},
        "childSessionId": "child-1",
        "rawLogPath": "logs/child.jsonl",
        "startedAtMs": 100,
        "endedAtMs": 145,
        "durationMs": 45,
        "toolTrace": [{"tool": "read"}],
        "structuredOutput": {"approved": True},
        "validationErrors": ["schema warning"],
    }
    payload["filesChanged"].append("mutated.py")
    payload["usage"]["inputTokens"] = 99
    payload["toolTrace"][0]["tool"] = "bash"
    assert result.files_changed == ["app.py"]
    assert result.usage == {"inputTokens": 12}
    assert result.tool_trace == [{"tool": "read"}]
