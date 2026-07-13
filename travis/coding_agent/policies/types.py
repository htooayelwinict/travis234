"""Domain policy contracts for the coding-agent profile."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TypeAlias


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Block:
    code: str
    reason: str
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class RequireConsent:
    capability: str
    reason: str


PolicyDecision: TypeAlias = Allow | Block | RequireConsent


class TurnCapabilities:
    """Consumable grants scoped to one coding turn."""

    def __init__(self) -> None:
        self._uses: dict[str, int] = {}
        self._lock = threading.Lock()

    def grant(self, name: str, uses: int = 1) -> None:
        if uses < 1:
            raise ValueError("capability uses must be positive")
        with self._lock:
            self._uses[name] = self._uses.get(name, 0) + uses

    def consume(self, name: str) -> bool:
        with self._lock:
            uses = self._uses.get(name, 0)
            if uses < 1:
                return False
            if uses == 1:
                self._uses.pop(name, None)
            else:
                self._uses[name] = uses - 1
            return True

    def clear(self) -> None:
        with self._lock:
            self._uses.clear()

    def remaining(self, name: str) -> int:
        with self._lock:
            return self._uses.get(name, 0)


@dataclass(frozen=True)
class ToolCallView:
    id: str
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class CodingTurnContext:
    cwd: str
    latest_user_message: str
    capabilities: TurnCapabilities
    tool_catalog: tuple[str, ...]
    run_id: str
    turn_id: str


class ToolPolicy(Protocol):
    def evaluate(self, call: ToolCallView, context: CodingTurnContext) -> PolicyDecision: ...


@dataclass(frozen=True)
class CodingPolicyEvent:
    decision: PolicyDecision
    tool_call: ToolCallView
    run_id: str
    turn_id: str
    type: str = "coding_policy_decision"


__all__ = [
    "Allow",
    "Block",
    "CodingPolicyEvent",
    "CodingTurnContext",
    "PolicyDecision",
    "RequireConsent",
    "ToolCallView",
    "ToolPolicy",
    "TurnCapabilities",
]
