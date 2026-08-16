"""Typed value contracts for the capability registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Protocol


def _require_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


class CapabilityKind(StrEnum):
    CONTEXT_FILE = "context_file"
    SKILL = "skill"
    PROMPT_TEMPLATE = "prompt_template"
    THEME = "theme"
    EXTENSION = "extension"
    TOOL = "tool"
    AGENT_ROLE = "agent_role"


@dataclass(frozen=True)
class CapabilityLoadContext:
    cwd: str
    agent_dir: str
    project_trusted: bool
    offline: bool
    generation: int
    data: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class CapabilitySource:
    provider: str
    path: str | None = None
    source: str = "local"
    scope: str = "temporary"
    origin: str = "top-level"

    def __post_init__(self) -> None:
        _require_name(self.provider, "capability provider")


@dataclass(frozen=True)
class CapabilityRecord:
    kind: CapabilityKind
    key: str
    value: object
    source: CapabilitySource
    priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        _require_name(self.key, "capability key")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("capability priority must be an integer")


@dataclass(frozen=True)
class CapabilityDiagnostic:
    severity: Literal["warning", "error", "collision"]
    provider: str
    code: str
    message: str
    source: CapabilitySource | None = None

    def __post_init__(self) -> None:
        _require_name(self.provider, "capability provider")


@dataclass(frozen=True)
class CapabilityProviderResult:
    records: tuple[CapabilityRecord, ...] = ()
    diagnostics: tuple[CapabilityDiagnostic, ...] = ()
    state: object | None = None
    dispose: Callable[[], None] | None = None


class CapabilityProvider(Protocol):
    name: str
    priority: int

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        raise NotImplementedError


__all__ = [
    "CapabilityDiagnostic",
    "CapabilityKind",
    "CapabilityLoadContext",
    "CapabilityProvider",
    "CapabilityProviderResult",
    "CapabilityRecord",
    "CapabilitySource",
]
