"""Core runtime schemas for Phase 1."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema


ResultStatus = Literal[
    "completed",
    "failed",
    "blocked",
    "budget_exceeded",
    "needs_replan",
    "kernel_error",
]
WorkerIssueType = Literal["instance_failure", "plan_failure", "kernel_failure"]
TrustLevel = Literal["unknown", "worker_reported", "verified"]


class PermissionSet(BaseModel):
    """Runtime-normalized worker permissions with dict-like compatibility."""

    model_config = ConfigDict(extra="forbid")

    read_files: bool = False
    write_files: bool = False
    run_commands: bool = False
    web_research: bool = False
    write_paths: list[str] = Field(default_factory=list)
    write_paths_from_artifacts: list[str] = Field(default_factory=list)
    provided_keys: SkipJsonSchema[set[str]] = Field(default_factory=set, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_mapping(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if value is None:
            value = {}
        if not isinstance(value, dict):
            return value

        provided_keys = set(value.keys())
        data = dict(value)
        for key in ("read_files", "write_files", "run_commands", "web_research"):
            data.setdefault(key, False)
        for key in ("write_paths", "write_paths_from_artifacts"):
            if data.get(key) is None:
                data[key] = []
        data["provided_keys"] = provided_keys
        return data

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        return default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.provided_keys

    def __getitem__(self, key: str) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in type(self).model_fields:
            raise KeyError(key)
        setattr(self, key, value)
        self.provided_keys.add(key)

    def pop(self, key: str, default: Any = None) -> Any:
        value = self.get(key, default)
        if key in {"read_files", "write_files", "run_commands", "web_research"}:
            setattr(self, key, False)
        elif key in {"write_paths", "write_paths_from_artifacts"}:
            setattr(self, key, [])
        self.provided_keys.discard(key)
        return value

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude={"provided_keys"})


class ArtifactPayload(BaseModel):
    """Runtime artifact with provenance fields and legacy extra-key support."""

    model_config = ConfigDict(extra="allow")

    id: str
    content: Any = None
    kind: str | None = None
    producer: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    trust_level: TrustLevel = "worker_reported"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_artifact(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "id" not in data and "artifact_id" in data:
            data["id"] = data["artifact_id"]
        return data

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        extra = self.__pydantic_extra__ or {}
        return extra.get(key, default)

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, None)
        if value is None and key not in type(self).model_fields and key not in (self.__pydantic_extra__ or {}):
            raise KeyError(key)
        return value


class WorkerIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: WorkerIssueType
    code: str
    message: str
    step_id: str | None = None
    worker_type: str | None = None
    attempt_id: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    failed_step_id: str
    issue_codes: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    partial_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    raw_input: str
    normalized_input: str
    user_goal: str | None = None

    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    complexity_hint: str = "medium"
    confidence: float = 0.0

    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    step_id: str
    worker_type: str
    phase: str | None = None
    mode: Literal["observe_only", "plan_only", "bounded_mutation", "verify_only", "summarize_only"] | None = None
    task_id: str | None = None

    instruction: str

    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)

    max_tool_calls: int = 3
    max_model_calls: int = 1

    permissions: PermissionSet = Field(default_factory=PermissionSet)


class Plan(BaseModel):
    plan_id: str
    request_id: str

    planner: str
    objective: str
    strategy: str
    execution_pattern: str | None = None

    steps: list[PlanStep]
    budget: dict[str, Any] = Field(default_factory=dict)
    global_invariants: list[str] = Field(default_factory=list)

    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanRequest(BaseModel):
    request_id: str
    plan_id: str
    run_id: str
    failed_step_id: str
    reason: str

    worker_result: dict[str, Any] = Field(default_factory=dict)
    completed_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    completed_step_ids: list[str] = Field(default_factory=list)
    remaining_budget: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str | None = None
    issues: list[WorkerIssue] = Field(default_factory=list)
    partial_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    failed_step_artifacts: list[ArtifactPayload] = Field(default_factory=list)


class Task(BaseModel):
    task_id: str
    run_id: str
    step_id: str

    worker_type: str
    instruction: str

    input_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)

    max_tool_calls: int = 3
    max_model_calls: int = 1

    permissions: PermissionSet = Field(default_factory=PermissionSet)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    run_id: str
    producer: str

    status: ResultStatus
    summary: str

    artifacts: list[ArtifactPayload] = Field(default_factory=list)

    usage: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(TypedDict, total=False):
    user_input: str
    envelope: dict[str, Any]
    plan: dict[str, Any]
    result: dict[str, Any]
    errors: list[str]
