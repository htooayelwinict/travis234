"""Immutable normalized contracts for bounded language services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class LanguageServerConfig:
    name: str
    command: str
    args: tuple[str, ...]
    languages: tuple[str, ...]
    extensions: Mapping[str, str]
    root_markers: tuple[str, ...]
    initialization_options: object = field(compare=True)


@dataclass(frozen=True, order=True)
class DocumentPosition:
    line: int
    character: int


@dataclass(frozen=True, order=True)
class DocumentLocation:
    path: str
    start: DocumentPosition
    end: DocumentPosition


@dataclass(frozen=True)
class NormalizedDiagnostic:
    location: DocumentLocation
    message: str
    severity: int | None = None
    source: str | None = None
    code: str | int | None = None


@dataclass(frozen=True)
class NormalizedSymbol:
    name: str
    kind: int | None
    location: DocumentLocation
    container_name: str | None = None


@dataclass(frozen=True)
class NormalizedTextEdit:
    start: DocumentPosition
    end: DocumentPosition
    new_text: str


@dataclass(frozen=True)
class NormalizedFileEdit:
    path: str
    edits: tuple[NormalizedTextEdit, ...]
    version: int | None = None


@dataclass(frozen=True)
class NormalizedWorkspaceEdit:
    files: tuple[NormalizedFileEdit, ...]


@dataclass(frozen=True)
class LanguageServiceLimits:
    max_active_servers: int = 3
    startup_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 20.0
    max_restarts: int = 2
    restart_window_seconds: float = 60.0
    max_frame_bytes: int = 2 * 1024 * 1024
    max_inline_output_bytes: int = 256 * 1024
    max_apply_original_bytes: int = 64 * 1024 * 1024
    token_ttl_seconds: float = 10 * 60.0
    max_preview_tokens: int = 32
    max_action_tokens: int = 32


__all__ = [
    "DocumentLocation",
    "DocumentPosition",
    "LanguageServerConfig",
    "LanguageServiceLimits",
    "NormalizedDiagnostic",
    "NormalizedFileEdit",
    "NormalizedSymbol",
    "NormalizedTextEdit",
    "NormalizedWorkspaceEdit",
]
