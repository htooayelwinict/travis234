"""Core runtime schemas for Phase 1."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


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

    permissions: dict[str, Any] = Field(default_factory=dict)


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


class Task(BaseModel):
    task_id: str
    run_id: str
    step_id: str

    worker_type: str
    instruction: str

    input_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)

    max_tool_calls: int = 3
    max_model_calls: int = 1

    permissions: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    run_id: str
    producer: str

    status: str
    summary: str

    artifacts: list[dict[str, Any]] = Field(default_factory=list)

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
