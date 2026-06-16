from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RuntimeMode = Literal[
    "START",
    "THINK",
    "OBSERVE",
    "ACT",
    "VERIFY",
    "COMPACT",
    "PAUSE",
    "FINALIZE",
    "FAILED",
]


@dataclass
class RequestEnvelope:
    request_id: str
    user_goal: str
    root_path: str
    constraints: list[str] = field(default_factory=list)
    active_user_request: str = ""
    ui_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentState:
    session_id: str
    run_id: str
    request: RequestEnvelope
    mode: RuntimeMode = "START"
    active_skill_ids: list[str] = field(default_factory=list)
    active_extension_ids: list[str] = field(default_factory=list)
    active_tool_ids: list[str] = field(default_factory=list)
    world_refs: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    conversation_messages: list[dict[str, Any]] = field(default_factory=list)
    context_summary: dict[str, Any] = field(default_factory=dict)
    turn_feedback: list[str] = field(default_factory=list)
    context_metrics: list[dict[str, Any]] = field(default_factory=list)
    mutation_seq: int = 0
    terminal: bool = False
    result: dict[str, Any] | None = None
