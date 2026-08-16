"""Immutable contracts for disabled-by-default explicit memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


MemoryScope = Literal["project", "global"]
MemoryProvenance = Literal["user_requested", "agent_explicit", "imported_explicit"]
MEMORY_SCOPES = ("project", "global")
MEMORY_PROVENANCE = frozenset(
    {"user_requested", "agent_explicit", "imported_explicit"}
)
_MEMORY_ID = re.compile(r"^mem_[0-9a-f]{32}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


@dataclass(frozen=True)
class MemorySettings:
    enabled: bool = False
    allowed_scopes: tuple[MemoryScope, ...] = ("project",)
    max_fact_bytes: int = 64 * 1024
    max_facts_per_scope: int = 5_000
    max_total_bytes: int = 1024 * 1024 * 1024
    recall_limit: int = 20
    recall_bytes: int = 32 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        scopes = tuple(self.allowed_scopes)
        if not scopes or len(scopes) != len(set(scopes)):
            raise ValueError("allowed scopes must be unique and non-empty")
        if any(scope not in MEMORY_SCOPES for scope in scopes):
            raise ValueError("unknown memory scope")
        object.__setattr__(
            self,
            "allowed_scopes",
            tuple(scope for scope in MEMORY_SCOPES if scope in scopes),
        )
        for field in (
            "max_fact_bytes",
            "max_facts_per_scope",
            "max_total_bytes",
            "recall_limit",
            "recall_bytes",
        ):
            _positive_int(getattr(self, field), field)


@dataclass(frozen=True)
class MemoryFact:
    memory_id: str
    content: str
    tags: tuple[str, ...]
    scope: MemoryScope
    project_key: str
    provenance: MemoryProvenance
    created_at_ms: int
    updated_at_ms: int
    expires_at_ms: int | None = None
    source_session_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if _MEMORY_ID.fullmatch(self.memory_id) is None:
            raise ValueError("invalid memory id")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("memory content must be non-empty")
        tags = tuple(self.tags)
        if len(tags) > 16 or len(tags) != len(set(tags)):
            raise ValueError("memory tags must be unique and bounded")
        if any(not isinstance(tag, str) or not tag for tag in tags):
            raise ValueError("memory tags must be non-empty strings")
        object.__setattr__(self, "tags", tags)
        if self.scope not in MEMORY_SCOPES:
            raise ValueError("unknown memory scope")
        if _FINGERPRINT.fullmatch(self.project_key) is None:
            raise ValueError("invalid project key")
        if self.provenance not in MEMORY_PROVENANCE:
            raise ValueError("invalid memory provenance")
        for name in ("created_at_ms", "updated_at_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("memory timestamps must be monotonic")
        if self.expires_at_ms is not None and (
            isinstance(self.expires_at_ms, bool)
            or not isinstance(self.expires_at_ms, int)
            or self.expires_at_ms < self.created_at_ms
        ):
            raise ValueError("invalid memory expiry")
        if (
            self.source_session_fingerprint is not None
            and _FINGERPRINT.fullmatch(self.source_session_fingerprint) is None
        ):
            raise ValueError("invalid source session fingerprint")


__all__ = [
    "MEMORY_PROVENANCE",
    "MEMORY_SCOPES",
    "MemoryFact",
    "MemoryProvenance",
    "MemoryScope",
    "MemorySettings",
]
