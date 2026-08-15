"""Approval protocol and session-only grant storage."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, Protocol

from travis.agent.types import AbortSignal
from travis.coding_agent.policy.types import ToolEffect, normalize_effects

ApprovalScope = Literal["once", "session", "deny"]


@dataclass(frozen=True)
class ApprovalResponse:
    scope: ApprovalScope

    def __post_init__(self) -> None:
        if self.scope not in {"once", "session", "deny"}:
            raise ValueError(f"Unknown approval scope: {self.scope!r}")


@dataclass(frozen=True)
class ToolApprovalRequest:
    tool_name: str
    effects: frozenset[ToolEffect]
    argument_fingerprint: str
    safe_context: Mapping[str, str]
    reason_code: str
    child_role: str | None = None
    child_task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", normalize_effects(self.effects))
        object.__setattr__(self, "safe_context", MappingProxyType(dict(self.safe_context)))


class ToolApprovalBroker(Protocol):
    async def request(
        self,
        request: ToolApprovalRequest,
        signal: AbortSignal | None,
    ) -> ApprovalResponse: ...


class SessionGrantSet:
    """Thread-safe exact grants owned by one AgentSession."""

    def __init__(self) -> None:
        self._grants: set[tuple[str, frozenset[ToolEffect]]] = set()
        self._lock = threading.RLock()

    def add(self, tool_name: str, effects: frozenset[ToolEffect]) -> None:
        key = (str(tool_name), normalize_effects(effects))
        with self._lock:
            self._grants.add(key)

    def contains(self, tool_name: str, effects: frozenset[ToolEffect]) -> bool:
        key = (str(tool_name), normalize_effects(effects))
        with self._lock:
            return key in self._grants


__all__ = [
    "ApprovalResponse",
    "ApprovalScope",
    "SessionGrantSet",
    "ToolApprovalBroker",
    "ToolApprovalRequest",
]
