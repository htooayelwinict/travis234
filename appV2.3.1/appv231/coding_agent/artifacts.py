"""Exact session-scoped references to tool-created artifacts."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    path: Path
    kind: str
    access: Literal["read"] = "read"


class ArtifactRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, ArtifactRef] = {}
        self._by_path: dict[Path, ArtifactRef] = {}
        self._lock = threading.RLock()
        self._closed = False

    def register(
        self,
        path: Path,
        kind: str,
        access: Literal["read"] = "read",
    ) -> ArtifactRef:
        resolved = path.expanduser().resolve(strict=False)
        with self._lock:
            if self._closed:
                raise RuntimeError("Artifact registry is closed")
            existing = self._by_path.get(resolved)
            if existing is not None:
                return existing
            ref = ArtifactRef(id=f"artifact-{uuid.uuid4().hex}", path=resolved, kind=kind, access=access)
            self._by_id[ref.id] = ref
            self._by_path[resolved] = ref
            return ref

    def resolve_read(self, path_or_id: str) -> Path | None:
        with self._lock:
            by_id = self._by_id.get(path_or_id)
            if by_id is not None and by_id.access == "read":
                return by_id.path
            try:
                resolved = Path(path_or_id).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                return None
            ref = self._by_path.get(resolved)
            return ref.path if ref is not None and ref.access == "read" else None

    def close(self, remove_files: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            refs = tuple(self._by_id.values())
            self._closed = True
            self._by_id.clear()
            self._by_path.clear()
        if remove_files:
            for ref in refs:
                try:
                    ref.path.unlink()
                except FileNotFoundError:
                    pass


__all__ = ["ArtifactRef", "ArtifactRegistry"]
