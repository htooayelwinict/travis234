"""Typed subagent task validation and result settlement."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Literal, Protocol

from jsonschema import Draft202012Validator

from travis.coding_agent.policy import ToolEffect
from travis.coding_agent.policy.types import normalize_effects

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class TypedTask(Protocol):
    role_definition_name: str | None
    allowed_effects: tuple[ToolEffect, ...] | None
    model_role: Literal["worker", "reviewer"] | None
    result_schema: dict[str, object] | None
    artifact_policy: Literal["none", "declared", "declared_and_trace"]


class TypedResult(Protocol):
    status: str
    final_response: str
    summary: str
    errors: list[str]


def validate_typed_task_fields(task: TypedTask) -> tuple[ToolEffect, ...] | None:
    if task.role_definition_name is not None and (
        not isinstance(task.role_definition_name, str)
        or not _TASK_ID_PATTERN.fullmatch(task.role_definition_name)
    ):
        raise ValueError("Subagent role_definition_name is invalid")
    effects = None
    if task.allowed_effects is not None:
        normalized = normalize_effects(task.allowed_effects)
        effects = tuple(
            effect
            for effect in ("read", "write", "execute", "network")
            if effect in normalized
        )
    if task.model_role not in (None, "worker", "reviewer"):
        raise ValueError("Subagent model_role must be worker or reviewer")
    if task.result_schema is not None and not isinstance(task.result_schema, dict):
        raise ValueError("Subagent result_schema must be an object")
    if task.artifact_policy not in ("none", "declared", "declared_and_trace"):
        raise ValueError("Subagent artifact_policy is invalid")
    return effects


def settle_typed_result(task: TypedTask, result: TypedResult):
    if task.result_schema is None or result.status != "completed":
        return result
    text = result.final_response or result.summary
    errors: list[str] = []
    envelope: object | None = None
    parsed = False
    if len(text.encode("utf-8")) > 256 * 1024:
        errors.append("typed result envelope exceeds 256 KiB")
    else:
        try:
            envelope = json.loads(text)
            parsed = True
        except json.JSONDecodeError:
            errors.append("typed result envelope must be valid JSON")
    output: object | None = None
    summary = result.summary[:4096]
    artifacts: list[str] = []
    if parsed:
        if not isinstance(envelope, dict):
            errors.append("typed result envelope must be an object")
        else:
            unknown = sorted(set(envelope).difference({"summary", "output", "artifacts"}))
            if unknown:
                errors.append(f"typed result has unknown envelope keys: {', '.join(unknown)}")
            raw_summary = envelope.get("summary")
            if not isinstance(raw_summary, str):
                errors.append("typed result summary must be a string")
            else:
                summary = raw_summary[:4096]
            if "output" not in envelope:
                errors.append("typed result output is required")
            else:
                output = envelope["output"]
            raw_artifacts = envelope.get("artifacts", [])
            if not isinstance(raw_artifacts, list) or any(
                not isinstance(item, str) for item in raw_artifacts
            ):
                errors.append("typed result artifacts must be a list of strings")
            elif task.artifact_policy != "none":
                artifacts = list(dict.fromkeys(raw_artifacts))[:256]
    if not errors and parsed:
        validator = Draft202012Validator(task.result_schema)
        for error in sorted(validator.iter_errors(output), key=lambda item: list(item.path))[:8]:
            path = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in error.path
            )
            errors.append(f"typed result schema mismatch at {path}: {error.message}"[:300])
    if errors:
        return replace(
            result,
            status="failed",
            summary=summary or "Typed subagent result validation failed.",
            artifacts=[],
            structured_output=None,
            validation_errors=errors,
            errors=[*result.errors, *errors],
        )
    return replace(
        result,
        summary=summary,
        artifacts=artifacts,
        structured_output=output,
        validation_errors=[],
    )


__all__ = ["settle_typed_result", "validate_typed_task_fields"]
