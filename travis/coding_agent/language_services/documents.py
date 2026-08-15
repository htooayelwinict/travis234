"""Workspace-contained document versions and position-encoding conversion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from travis.coding_agent.language_services.types import DocumentPosition

PositionEncoding = Literal["utf-8", "utf-16", "utf-32"]


@dataclass(frozen=True)
class DocumentSnapshot:
    path: Path
    uri: str
    text: str
    version: int
    sha256: str
    saved_hash: str | None


@dataclass
class _TrackedDocument:
    snapshot: DocumentSnapshot


def _line_text(text: str, line: int) -> str:
    lines = text.split("\n")
    if line < 0 or line >= len(lines):
        raise ValueError("document position line is out of range")
    value = lines[line]
    return value[:-1] if value.endswith("\r") else value


def _units(character: str, encoding: PositionEncoding) -> int:
    if encoding == "utf-8":
        return len(character.encode("utf-8"))
    if encoding == "utf-16":
        return len(character.encode("utf-16-le")) // 2
    if encoding == "utf-32":
        return 1
    raise ValueError(f"unsupported position encoding: {encoding}")


def _index_from_units(line_text: str, offset: int, encoding: PositionEncoding) -> int:
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("document position character must be a nonnegative integer")
    consumed = 0
    for index, character in enumerate(line_text):
        if consumed == offset:
            return index
        next_consumed = consumed + _units(character, encoding)
        if offset < next_consumed:
            raise ValueError("document position is not on a character boundary")
        consumed = next_consumed
    if consumed == offset:
        return len(line_text)
    raise ValueError("document position character is out of range")


def _offset_for_index(line_text: str, index: int, encoding: PositionEncoding) -> int:
    return sum(_units(character, encoding) for character in line_text[:index])


def position_to_server(
    text: str,
    position: DocumentPosition,
    encoding: PositionEncoding,
) -> DocumentPosition:
    line = _line_text(text, position.line)
    index = _index_from_units(line, position.character, "utf-16")
    return DocumentPosition(position.line, _offset_for_index(line, index, encoding))


def position_from_server(
    text: str,
    position: DocumentPosition,
    encoding: PositionEncoding,
) -> DocumentPosition:
    line = _line_text(text, position.line)
    index = _index_from_units(line, position.character, encoding)
    return DocumentPosition(position.line, _offset_for_index(line, index, "utf-16"))


class DocumentTracker:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._documents: dict[Path, _TrackedDocument] = {}

    def resolve_path(self, path: str | Path) -> Path:
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else self.workspace / raw
        resolved = candidate.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("document path escapes the workspace")
        return resolved

    def open_or_update(self, path: str | Path) -> DocumentSnapshot:
        resolved = self.resolve_path(path)
        if not resolved.is_file():
            raise ValueError("document path must be an existing regular file")
        raw = resolved.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("language-service documents must be UTF-8") from error
        digest = hashlib.sha256(raw).hexdigest()
        existing = self._documents.get(resolved)
        if existing is not None and existing.snapshot.sha256 == digest:
            return existing.snapshot
        snapshot = DocumentSnapshot(
            path=resolved,
            uri=resolved.as_uri(),
            text=text,
            version=1 if existing is None else existing.snapshot.version + 1,
            sha256=digest,
            saved_hash=existing.snapshot.saved_hash if existing is not None else None,
        )
        self._documents[resolved] = _TrackedDocument(snapshot)
        return snapshot

    def mark_saved(self, path: str | Path) -> DocumentSnapshot:
        snapshot = self.open_or_update(path)
        saved = DocumentSnapshot(
            path=snapshot.path,
            uri=snapshot.uri,
            text=snapshot.text,
            version=snapshot.version,
            sha256=snapshot.sha256,
            saved_hash=snapshot.sha256,
        )
        self._documents[snapshot.path] = _TrackedDocument(saved)
        return saved

    def close(self, path: str | Path) -> DocumentSnapshot | None:
        resolved = self.resolve_path(path)
        tracked = self._documents.pop(resolved, None)
        return tracked.snapshot if tracked is not None else None

    def snapshot(self, path: str | Path) -> DocumentSnapshot:
        resolved = self.resolve_path(path)
        tracked = self._documents.get(resolved)
        if tracked is None:
            raise KeyError(str(resolved))
        return tracked.snapshot


__all__ = [
    "DocumentSnapshot",
    "DocumentTracker",
    "PositionEncoding",
    "position_from_server",
    "position_to_server",
]
