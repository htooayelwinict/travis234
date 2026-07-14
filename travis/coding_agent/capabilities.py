"""Canonical filesystem capabilities for coding-agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

AccessMode = Literal["read", "write", "execute"]
_UNICODE_SPACES = re.compile(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]")


class CapabilityViolation(PermissionError):
    def __init__(self, code: str, requested_path: str, resolved_path: Path) -> None:
        self.code = code
        self.requested_path = requested_path
        self.resolved_path = resolved_path
        super().__init__(f"{code}: {requested_path} resolves to {resolved_path}")


@dataclass(frozen=True)
class WorkspaceCapability:
    root: Path
    extra_read_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        object.__setattr__(
            self,
            "extra_read_roots",
            tuple(path.expanduser().resolve() for path in self.extra_read_roots),
        )

    def resolve(self, path: str, access: AccessMode) -> Path:
        del access
        normalized = _UNICODE_SPACES.sub(" ", path)
        if normalized.startswith("@"):
            normalized = normalized[1:]
        requested = Path(normalized)
        if normalized == "~" or normalized.startswith("~/"):
            requested = requested.expanduser()
        candidate = requested if requested.is_absolute() else self.root / requested
        return candidate.resolve(strict=False)


__all__ = [
    "AccessMode",
    "CapabilityViolation",
    "WorkspaceCapability",
]
