"""Internal contracts for optional model-backed decompression.

The decompressor depends on this small protocol instead of provider SDKs. Unit
tests should satisfy it with fake clients and canned JSON; live provider setup
belongs outside this package.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import ExactLiteral


GENERIC_INPUT_TYPES = frozenset(
    {
        "request",
        "task",
        "input",
        "payload",
        "data",
        "object",
        "unknown",
        "general",
        "other",
        "question",
        "mutation_request",
        "ambiguous_request",
    }
)


class PromptChainModelClient(Protocol):
    """Minimal JSON-completion client accepted by the LLM prompt chain."""

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        """Return a JSON string matching the supplied stage schema."""


class DecompressedEnvelope(BaseModel):
    """LLM-emitted descriptive envelope content without runtime-owned fields."""

    model_config = ConfigDict(extra="forbid")

    normalized_input: str = Field(description="Concise rewrite preserving the user's meaning.")
    user_goal: str | None = Field(default=None, description="Concrete goal if inferable, otherwise null.")
    input_type: str = Field(
        description=(
            "Specific open-ended request descriptor. Do not use generic placeholders like "
            "request, task, input, payload, data, object, unknown, general, or other. "
            "Examples: docker_concept_question, python_file_fix_request, "
            "infra_config_debug_request, sdk_async_performance_refactor_request, "
            "ambiguous_app_fix_request."
        )
    )
    intents: list[str] = Field(default_factory=list, description="Open-ended semantic intent strings.")
    domains: list[str] = Field(default_factory=list, description="Open-ended domain strings.")
    risks: list[str] = Field(default_factory=list, description="Descriptive risk strings.")
    artifacts: list[dict[str, Any]] = Field(default_factory=list, description="Concrete nouns from the request.")
    context_needed: list[str] = Field(default_factory=list, description="Facts needed before safe planning.")
    constraints: list[str] = Field(default_factory=list, description="Safety/correctness invariants, not steps.")
    complexity_hint: str = Field(default="medium", description="low, medium, or high.")
    confidence: float = Field(default=0.0, description="0.0 to 1.0 confidence in the decomposition.")
    ambiguity: list[str] = Field(default_factory=list, description="Uncertainties or missing facts.")
    assumptions: list[str] = Field(default_factory=list, description="Safe assumptions only.")
    literal_contract: list[ExactLiteral] = Field(
        default_factory=list,
        description="Exact user literals such as JSON keys, paths, filenames, and symbols that later stages must preserve.",
    )

    @field_validator("input_type")
    @classmethod
    def input_type_must_be_specific(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("input_type must be a specific non-empty descriptor")
        if normalized.lower().replace(" ", "_") in GENERIC_INPUT_TYPES:
            raise ValueError("input_type is too generic; use a specific open-ended descriptor")
        return normalized


class RequestClassification(DecompressedEnvelope):
    """Compatibility name for the historical staged classification contract."""
