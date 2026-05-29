"""Internal contracts for optional model-backed decompression.

The decompressor depends on this small protocol instead of provider SDKs. Unit
tests should satisfy it with fake clients and canned JSON; live provider setup
belongs outside this package.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class PromptChainModelClient(Protocol):
    """Minimal JSON-completion client accepted by the LLM prompt chain."""

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        """Return a JSON string matching the supplied stage schema."""


class DecompressedEnvelope(BaseModel):
    """LLM-emitted descriptive envelope content without runtime-owned fields."""

    model_config = ConfigDict(extra="forbid")

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


class RequestClassification(DecompressedEnvelope):
    """Compatibility name for the historical staged classification contract."""
