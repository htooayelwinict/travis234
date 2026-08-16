"""Immutable value types for coding tool policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolEffect = Literal["read", "write", "execute", "network"]
ToolPolicyMode = Literal["disabled", "audit", "enforce"]

TOOL_EFFECT_ORDER: tuple[ToolEffect, ...] = (
    "read",
    "write",
    "execute",
    "network",
)
ALL_TOOL_EFFECTS: frozenset[ToolEffect] = frozenset(TOOL_EFFECT_ORDER)
TOOL_POLICY_MODE_ORDER: tuple[ToolPolicyMode, ...] = (
    "disabled",
    "audit",
    "enforce",
)
TOOL_POLICY_REASON_CODES = frozenset(
    {
        "policy_disabled",
        "auto_allowed",
        "session_grant",
        "approval_required",
        "approval_denied",
        "approval_unavailable",
        "undeclared_effects",
        "approval_cancelled",
    }
)


def normalize_effects(effects: object) -> frozenset[ToolEffect]:
    """Return a validated immutable effect set."""

    if isinstance(effects, str):
        raise ValueError("Tool effects must be a collection, not a string.")
    try:
        normalized = frozenset(effects)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError("Tool effects must be an iterable of known effect names.") from error
    unknown = normalized.difference(TOOL_EFFECT_ORDER)
    if unknown:
        raise ValueError(f"Unknown tool effects: {sorted(str(item) for item in unknown)}")
    return normalized  # type: ignore[return-value]


@dataclass(frozen=True)
class ToolPolicySettings:
    mode: ToolPolicyMode
    auto_allow_effects: frozenset[ToolEffect]

    def __post_init__(self) -> None:
        if self.mode not in TOOL_POLICY_MODE_ORDER:
            raise ValueError(f"Unknown tool policy mode: {self.mode!r}")
        object.__setattr__(self, "auto_allow_effects", normalize_effects(self.auto_allow_effects))


@dataclass(frozen=True)
class ToolPolicyDecision:
    tool_name: str
    effects: frozenset[ToolEffect]
    mode: ToolPolicyMode
    allow: bool
    reason_code: str

    def __post_init__(self) -> None:
        if self.mode not in TOOL_POLICY_MODE_ORDER:
            raise ValueError(f"Unknown tool policy mode: {self.mode!r}")
        if self.reason_code not in TOOL_POLICY_REASON_CODES:
            raise ValueError(f"Unknown tool policy reason code: {self.reason_code!r}")
        object.__setattr__(self, "effects", normalize_effects(self.effects))
