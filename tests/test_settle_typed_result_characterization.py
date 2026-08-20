"""Direct characterization coverage for typed subagent result settlement."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Literal

import pytest
from jsonschema.exceptions import UnknownType

from travis.coding_agent import subagent_results as subagent_results_module
from travis.coding_agent.policy import ToolEffect


@dataclass
class _Task:
    role_definition_name: str | None = "fixture"
    allowed_effects: tuple[ToolEffect, ...] | None = None
    model_role: Literal["worker", "reviewer"] | None = None
    result_schema: dict[str, object] | None = None
    artifact_policy: Literal["none", "declared", "declared_and_trace"] = "none"


@dataclass
class _Result:
    task_id: str
    backend: str
    role: str
    status: str
    summary: str
    final_response: str
    files_changed: list[str]
    artifacts: list[str]
    errors: list[str]
    usage: dict[str, object]
    child_session_id: str | None
    raw_log_path: str | None
    started_at_ms: int
    ended_at_ms: int
    tool_trace: list[dict[str, object]]
    structured_output: object | None
    validation_errors: list[str]

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("Subagent summary is required")


def _result(
    *,
    status: str = "completed",
    summary: str = "original summary",
    final_response: str = "",
    artifacts: list[str] | None = None,
    errors: list[str] | None = None,
    structured_output: object | None = None,
    validation_errors: list[str] | None = None,
) -> _Result:
    return _Result(
        task_id="task-1",
        backend="fixture",
        role="worker",
        status=status,
        summary=summary,
        final_response=final_response,
        files_changed=["kept.py"],
        artifacts=list(artifacts or []),
        errors=list(errors or []),
        usage={"input": 7},
        child_session_id="child-1",
        raw_log_path="trace.jsonl",
        started_at_ms=10,
        ended_at_ms=20,
        tool_trace=[{"toolName": "read"}],
        structured_output=structured_output,
        validation_errors=list(validation_errors or []),
    )


def settle_typed_result(task: _Task, result: _Result) -> _Result:
    settled = subagent_results_module.settle_typed_result(task, result)
    assert isinstance(settled, _Result)
    return settled


def _json_response(
    output: object,
    *,
    summary: object = "settled summary",
    artifacts: object = None,
    extra: dict[str, object] | None = None,
) -> str:
    envelope: dict[str, object] = {"summary": summary, "output": output}
    if artifacts is not None:
        envelope["artifacts"] = artifacts
    if extra:
        envelope.update(extra)
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def _sized_envelope(size: int, *, multibyte: bool) -> str:
    prefix = '{"summary":"s","output":"'
    suffix = '","artifacts":[]}'
    remaining = size - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    assert remaining >= 0
    filler = ("é" * (remaining // 2) + ("a" if remaining % 2 else "")) if multibyte else "x" * remaining
    value = prefix + filler + suffix
    assert len(value.encode("utf-8")) == size
    return value


@pytest.mark.parametrize(
    ("task", "result"),
    [
        (_Task(result_schema=None), _result()),
        (
            _Task(result_schema={"type": "object"}),
            _result(status="failed", final_response="not json"),
        ),
        (
            _Task(result_schema={"type": "object"}),
            _result(status="cancelled", final_response="not json"),
        ),
    ],
)
def test_untyped_or_noncompleted_results_return_the_exact_same_identity(
    task: _Task,
    result: _Result,
) -> None:
    settled = settle_typed_result(task, result)

    assert settled is result


def test_empty_final_response_uses_summary_but_truthy_whitespace_does_not() -> None:
    task = _Task(result_schema={})
    summary_envelope = _json_response({"from": "summary"}, summary="parsed summary")
    from_summary = _result(summary=summary_envelope, final_response="")
    whitespace = _result(summary=summary_envelope, final_response=" ")

    settled = settle_typed_result(task, from_summary)
    failed = settle_typed_result(task, whitespace)

    assert settled.status == "completed"
    assert settled.summary == "parsed summary"
    assert settled.structured_output == {"from": "summary"}
    assert failed.status == "failed"
    assert failed.summary == summary_envelope
    assert failed.validation_errors == ["typed result envelope must be valid JSON"]


@pytest.mark.parametrize("multibyte", [False, True])
def test_utf8_256_kib_boundary_accepts_exact_bytes_and_rejects_one_more(
    multibyte: bool,
) -> None:
    task = _Task(result_schema={})
    exact = _result(final_response=_sized_envelope(256 * 1024, multibyte=multibyte))
    oversized = _result(final_response=_sized_envelope(256 * 1024 + 1, multibyte=multibyte))

    accepted = settle_typed_result(task, exact)
    rejected = settle_typed_result(task, oversized)

    assert accepted.status == "completed"
    assert accepted.summary == "s"
    assert isinstance(accepted.structured_output, str)
    assert rejected.status == "failed"
    assert rejected.validation_errors == ["typed result envelope exceeds 256 KiB"]
    assert rejected.summary == "original summary"


@pytest.mark.parametrize("response", ["not-json", "{", "[1,"])
def test_json_decode_errors_use_the_exact_failure(response: str) -> None:
    result = _result(final_response=response, errors=["prior error"])

    settled = settle_typed_result(_Task(result_schema={}), result)

    assert settled.status == "failed"
    assert settled.validation_errors == ["typed result envelope must be valid JSON"]
    assert settled.errors == ["prior error", "typed result envelope must be valid JSON"]


@pytest.mark.parametrize("envelope", [None, [], "value", 7, False])
def test_parsed_nonobject_envelopes_have_one_exact_error(envelope: object) -> None:
    response = json.dumps(envelope)

    settled = settle_typed_result(
        _Task(result_schema={}),
        _result(final_response=response),
    )

    assert settled.status == "failed"
    assert settled.summary == "original summary"
    assert settled.structured_output is None
    assert settled.artifacts == []
    assert settled.validation_errors == ["typed result envelope must be an object"]


def test_envelope_validation_orders_unknown_summary_output_then_artifacts_errors() -> None:
    response = json.dumps(
        {
            "summary": 7,
            "artifacts": ["ok", 3],
            "zeta": 1,
            "alpha": 2,
        },
        separators=(",", ":"),
    )

    settled = settle_typed_result(
        _Task(result_schema={"type": "object"}, artifact_policy="declared"),
        _result(final_response=response, errors=["existing"]),
    )

    expected = [
        "typed result has unknown envelope keys: alpha, zeta",
        "typed result summary must be a string",
        "typed result output is required",
        "typed result artifacts must be a list of strings",
    ]
    assert settled.validation_errors == expected
    assert settled.errors == ["existing", *expected]


def test_summary_uses_raw_value_truncation_prior_fallback_and_empty_error_fallback() -> None:
    task = _Task(result_schema={})
    raw_summary = "é" * 5000
    success = settle_typed_result(
        task,
        _result(final_response=_json_response({}, summary=raw_summary)),
    )
    malformed = settle_typed_result(
        task,
        _result(summary="s" * 5000, final_response="not json"),
    )
    empty_with_error = settle_typed_result(
        task,
        _result(final_response=_json_response({}, summary="", extra={"unknown": True})),
    )

    assert success.summary == "é" * 4096
    assert malformed.summary == "s" * 4096
    assert empty_with_error.summary == "Typed subagent result validation failed."


def test_successful_empty_summary_preserves_dataclass_validation_exception() -> None:
    result = _result(final_response=_json_response({}, summary=""))

    with pytest.raises(ValueError, match="Subagent summary is required"):
        settle_typed_result(_Task(result_schema={}), result)


@pytest.mark.parametrize("policy", ["declared", "declared_and_trace"])
def test_declared_artifact_policies_dedupe_in_order_and_cap_at_256(
    policy: Literal["declared", "declared_and_trace"],
) -> None:
    artifacts = ["first", "second", "first", *[f"artifact-{index}" for index in range(300)]]
    result = _result(
        final_response=_json_response({"ok": True}, artifacts=artifacts),
        artifacts=["old"],
    )

    settled = settle_typed_result(
        _Task(result_schema={"type": "object"}, artifact_policy=policy),
        result,
    )

    assert settled.artifacts == ["first", "second", *[f"artifact-{index}" for index in range(254)]]
    assert len(settled.artifacts) == 256
    assert settled.structured_output == {"ok": True}


def test_none_artifact_policy_validates_then_discards_declared_paths() -> None:
    valid = settle_typed_result(
        _Task(result_schema={}, artifact_policy="none"),
        _result(final_response=_json_response(1, artifacts=["one", "one", "two"])),
    )
    invalid = settle_typed_result(
        _Task(result_schema={}, artifact_policy="none"),
        _result(final_response=_json_response(1, artifacts=["one", 2])),
    )

    assert valid.status == "completed"
    assert valid.artifacts == []
    assert invalid.status == "failed"
    assert invalid.validation_errors == ["typed result artifacts must be a list of strings"]


def test_absent_artifacts_default_to_empty_for_every_policy() -> None:
    for policy in ("none", "declared", "declared_and_trace"):
        settled = settle_typed_result(
            _Task(result_schema={}, artifact_policy=policy),
            _result(final_response=_json_response({"ok": True})),
        )
        assert settled.artifacts == []


def test_output_identity_and_envelope_nonmutation_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = {"nested": [1, {"ok": True}]}
    envelope: dict[str, object] = {
        "summary": "identity",
        "output": output,
        "artifacts": ["one", "one"],
    }
    before = copy.deepcopy(envelope)

    def loads(text: str) -> object:
        assert text == "fixture-json"
        return envelope

    monkeypatch.setattr(subagent_results_module.json, "loads", loads)

    settled = settle_typed_result(
        _Task(result_schema={}, artifact_policy="declared"),
        _result(final_response="fixture-json"),
    )

    assert settled.structured_output is output
    assert envelope == before
    assert settled.artifacts == ["one"]


def test_schema_errors_sort_by_path_render_arrays_and_cap_at_eight() -> None:
    output = {f"field-{index}": [index] for index in range(10)}
    properties = {
        f"field-{index}": {
            "type": "array",
            "items": {"type": "string"},
        }
        for index in range(10)
    }
    settled = settle_typed_result(
        _Task(result_schema={"type": "object", "properties": properties}),
        _result(final_response=_json_response(output, summary="bad schema")),
    )

    assert len(settled.validation_errors) == 8
    assert [error.split(":", 1)[0] for error in settled.validation_errors] == [
        f"typed result schema mismatch at $.field-{index}[0]" for index in range(8)
    ]
    assert all(error.endswith("is not of type 'string'") for error in settled.validation_errors)


def test_schema_root_and_special_property_paths_use_exact_rendering_and_order() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "required": ["missing"],
        "properties": {
            "a.b": {
                "type": "array",
                "items": {"type": "integer"},
            }
        },
    }

    settled = settle_typed_result(
        _Task(result_schema=schema),
        _result(final_response=_json_response({"a.b": ["wrong"]})),
    )

    assert settled.validation_errors[0].startswith("typed result schema mismatch at $")
    assert settled.validation_errors[1].startswith("typed result schema mismatch at $.a.b[0]:")


def test_schema_error_text_is_capped_after_path_and_message_formatting() -> None:
    settled = settle_typed_result(
        _Task(result_schema={"enum": ["x" * 500]}),
        _result(final_response=_json_response("not-enum")),
    )

    assert len(settled.validation_errors) == 1
    assert len(settled.validation_errors[0]) == 300
    assert settled.validation_errors[0].startswith("typed result schema mismatch at $:")


def test_invalid_schema_exception_is_not_converted_to_a_result_failure() -> None:
    result = _result(final_response=_json_response({"ok": True}))

    with pytest.raises(UnknownType):
        settle_typed_result(_Task(result_schema={"type": "not-a-json-type"}), result)


def test_envelope_errors_prevent_schema_and_semantic_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def semantic(output: object) -> list[str]:
        calls.append(output)
        return ["must not run"]

    monkeypatch.setattr(
        subagent_results_module,
        "_BUILTIN_RESULT_VALIDATORS",
        {"fixture": semantic},
    )
    result = _result(
        final_response=json.dumps({"summary": 1, "output": "wrong"}),
    )

    settled = settle_typed_result(
        _Task(role_definition_name="fixture", result_schema={"type": "object"}),
        result,
    )

    assert settled.validation_errors == ["typed result summary must be a string"]
    assert calls == []


def test_schema_errors_prevent_semantic_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def semantic(output: object) -> list[str]:
        calls.append(output)
        return ["must not run"]

    monkeypatch.setattr(
        subagent_results_module,
        "_BUILTIN_RESULT_VALIDATORS",
        {"fixture": semantic},
    )

    settled = settle_typed_result(
        _Task(role_definition_name="fixture", result_schema={"type": "object"}),
        _result(final_response=_json_response("wrong")),
    )

    assert settled.validation_errors[0].startswith("typed result schema mismatch")
    assert calls == []


def test_semantic_validator_lookup_receives_output_then_caps_and_formats_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    output = {"plan": "value"}

    def semantic(value: object) -> list[str]:
        calls.append(value)
        return [f"semantic-{index}-" + ("x" * 400) for index in range(10)]

    monkeypatch.setattr(
        subagent_results_module,
        "_BUILTIN_RESULT_VALIDATORS",
        {"fixture": semantic},
    )

    settled = settle_typed_result(
        _Task(role_definition_name="fixture", result_schema={}),
        _result(final_response=_json_response(output)),
    )

    assert calls == [output]
    assert len(settled.validation_errors) == 8
    assert all(len(error) == 300 for error in settled.validation_errors)
    assert [error.split("-", 2)[1] for error in settled.validation_errors] == [str(index) for index in range(8)]


@pytest.mark.parametrize("role_name", [None, "", "unknown"])
def test_missing_or_unknown_semantic_validator_returns_success(role_name: str | None) -> None:
    settled = settle_typed_result(
        _Task(role_definition_name=role_name, result_schema={}),
        _result(final_response=_json_response({"ok": True})),
    )

    assert settled.status == "completed"
    assert settled.validation_errors == []
    assert settled.structured_output == {"ok": True}


def test_real_coordination_semantic_validator_runs_after_schema_validation() -> None:
    cyclic_plan = {
        "route": "subagents",
        "tasks": [
            {"id": "a", "owner": "subagent"},
            {"id": "b", "owner": "subagent"},
        ],
        "dependencies": [
            {"before": "a", "after": "b"},
            {"before": "b", "after": "a"},
        ],
        "ownership": [
            {"taskId": "a", "access": "read", "scopes": ["travis"]},
            {"taskId": "b", "access": "read", "scopes": ["tests"]},
        ],
        "verification": [
            {"taskId": "a", "checks": ["inspect"]},
            {"taskId": "b", "checks": ["test"]},
        ],
    }

    settled = settle_typed_result(
        _Task(role_definition_name="coordination-planner", result_schema={"type": "object"}),
        _result(final_response=_json_response(cyclic_plan)),
    )

    assert settled.status == "failed"
    assert settled.validation_errors == ["typed result semantic mismatch: dependencies must be acyclic"]


def test_failure_replace_fields_append_prior_errors_and_preserve_other_fields() -> None:
    original_output = {"old": True}
    result = _result(
        final_response="not json",
        artifacts=["old-artifact"],
        errors=["prior-1", "prior-2"],
        structured_output=original_output,
        validation_errors=["prior validation"],
    )

    settled = settle_typed_result(_Task(result_schema={}), result)

    assert settled is not result
    assert settled.status == "failed"
    assert settled.summary == "original summary"
    assert settled.artifacts == []
    assert settled.structured_output is None
    assert settled.validation_errors == ["typed result envelope must be valid JSON"]
    assert settled.errors == [
        "prior-1",
        "prior-2",
        "typed result envelope must be valid JSON",
    ]
    assert settled.files_changed is result.files_changed
    assert settled.usage is result.usage
    assert settled.tool_trace is result.tool_trace
    assert settled.final_response == "not json"
    assert settled.child_session_id == "child-1"
    assert settled.raw_log_path == "trace.jsonl"
    assert settled.started_at_ms == 10
    assert settled.ended_at_ms == 20


def test_success_replace_fields_preserve_prior_errors_and_unreplaced_values() -> None:
    result = _result(
        final_response=_json_response({"ok": True}, artifacts=["new"]),
        artifacts=["old"],
        errors=["prior"],
        structured_output={"old": True},
        validation_errors=["old validation"],
    )

    settled = settle_typed_result(
        _Task(result_schema={}, artifact_policy="declared"),
        result,
    )

    assert settled is not result
    assert settled.status == "completed"
    assert settled.summary == "settled summary"
    assert settled.artifacts == ["new"]
    assert settled.structured_output == {"ok": True}
    assert settled.validation_errors == []
    assert settled.errors is result.errors
    assert settled.files_changed is result.files_changed
    assert settled.usage is result.usage
    assert settled.tool_trace is result.tool_trace


def test_json_loads_non_decode_exception_preserves_propagation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def loads(text: str) -> object:
        del text
        raise TypeError("decoder failed")

    monkeypatch.setattr(subagent_results_module.json, "loads", loads)

    with pytest.raises(TypeError, match="decoder failed"):
        settle_typed_result(
            _Task(result_schema={}),
            _result(final_response="fixture-json"),
        )


def test_utf8_encoding_exception_preserves_propagation() -> None:
    with pytest.raises(UnicodeEncodeError):
        settle_typed_result(
            _Task(result_schema={}),
            _result(final_response="\ud800"),
        )


def test_nonstring_runtime_final_response_preserves_attribute_error() -> None:
    result = _result(final_response="valid")
    object.__setattr__(result, "final_response", 7)

    with pytest.raises(AttributeError, match="'int' object has no attribute 'encode'"):
        settle_typed_result(_Task(result_schema={}), result)
