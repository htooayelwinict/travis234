"""Typed tool definitions and result envelopes for AppV2.1."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolCategory(str, Enum):
    OBSERVE = "observe"
    INSPECT = "inspect"
    SEARCH = "search"
    ANALYZE = "analyze"
    PLAN_HELPER = "plan-helper"
    MUTATE = "mutate"
    VERIFY = "verify"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    category: ToolCategory
    argument_schema: dict[str, Any]
    result_schema: dict[str, Any]
    risk_level: str = "low"
    trust: str = "runtime_observed"
    guidance: str = ""
    cacheable: bool = False


@dataclass(frozen=True)
class ToolResultEnvelope:
    tool_result_id: str
    tool_name: str
    status: str
    trust: str
    payload_ref: str
    prompt_summary: dict[str, Any]
    evidence_refs: list[str] = field(default_factory=list)
    artifacts: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_result_id": self.tool_result_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "trust": self.trust,
            "payload_ref": self.payload_ref,
            "prompt_summary": deepcopy(self.prompt_summary),
            "evidence_refs": list(self.evidence_refs),
            "artifacts": deepcopy(self.artifacts),
        }
