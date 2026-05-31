"""Planner-specific contracts and shared constants."""

from __future__ import annotations

from typing import Any, Protocol


ALLOWED_WORKER_TYPES: tuple[str, ...] = (
    "direct_worker",
    "repo_worker",
    "code_worker",
    "research_worker",
    "web_research_worker",
    "infra_worker",
    "verify_worker",
)

ALLOWED_MODES: tuple[str, ...] = (
    "observe_only",
    "plan_only",
    "bounded_mutation",
    "verify_only",
    "summarize_only",
)

WRITE_SCOPE_ARTIFACTS: tuple[str, ...] = (
    "mutation_scope",
    "allowed_write_paths",
    "writable_targets",
    "patch_scope",
)

WORKER_CATALOG: dict[str, dict[str, Any]] = {
    "direct_worker": {
        "description": "Direct question answering without file or command access.",
        "can_write": False,
    },
    "repo_worker": {
        "description": "Repository discovery and target/context identification.",
        "can_write": False,
    },
    "code_worker": {
        "description": "Code analysis and code changes when writes are explicitly permitted.",
        "can_write": True,
    },
    "research_worker": {
        "description": "Research and synthesis across available context.",
        "can_write": False,
    },
    "web_research_worker": {
        "description": "External web research and comparative synthesis with source-focused outputs.",
        "can_write": False,
    },
    "infra_worker": {
        "description": "Infrastructure diagnosis and guidance.",
        "can_write": False,
    },
    "verify_worker": {
        "description": "Verification checks after implementation changes.",
        "can_write": False,
    },
}


class PlannerModelClient(Protocol):
    """Minimal JSON-completion client used by planner prompt-chain stages."""

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        """Return a JSON string for the requested stage and schema."""


class PlanCompiler(Protocol):
    """Compiler that transforms an Envelope into a validated Plan."""

    def run(self, envelope: Any) -> Any:
        """Compile a validated plan from envelope context."""


class PlannerValidationError(ValueError):
    """Deterministic planner validation failure."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))
