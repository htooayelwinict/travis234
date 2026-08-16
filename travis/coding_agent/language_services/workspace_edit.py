"""Pure normalization and expiring review tokens for LSP workspace edits."""

from __future__ import annotations

import difflib
import hashlib
import secrets
import stat
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from travis.coding_agent.language_services.documents import (
    DocumentSnapshot,
    DocumentTracker,
    PositionEncoding,
    position_from_server,
    text_offset_from_position,
)
from travis.coding_agent.language_services.types import DocumentPosition, LanguageServiceLimits
from travis.coding_agent.tools.atomic_file import atomic_replace_bytes
from travis.coding_agent.tools.file_mutation_queue import with_file_mutation_queues


class WorkspaceEditError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedFileEdit:
    path: Path
    relative_path: str
    original_bytes: bytes
    target_bytes: bytes
    original_hash: str
    target_hash: str
    version: int
    mode: int


@dataclass(frozen=True)
class WorkspaceEditPreview:
    token: str
    files: tuple[PreparedFileEdit, ...]
    diff: str
    server_generation: int
    config_generation: int
    created_at: float
    source_path: Path | None = None


@dataclass(frozen=True)
class WorkspaceEditApplyReport:
    applied: bool
    changed: tuple[str, ...]
    restored: tuple[str, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class ActionToken:
    token: str
    action: dict[str, object]
    path: str
    server_generation: int
    config_generation: int
    created_at: float


def _position(value: object) -> DocumentPosition:
    if not isinstance(value, dict):
        raise WorkspaceEditError("workspace edit range position must be an object")
    line = value.get("line")
    character = value.get("character")
    if (
        not isinstance(line, int)
        or isinstance(line, bool)
        or line < 0
        or not isinstance(character, int)
        or isinstance(character, bool)
        or character < 0
    ):
        raise WorkspaceEditError("workspace edit range position is invalid")
    return DocumentPosition(line, character)


def _path_from_uri(uri: object, workspace: Path) -> Path:
    if not isinstance(uri, str):
        raise WorkspaceEditError("workspace edit URI must be a file URI")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise WorkspaceEditError("workspace edit URI must be a file URI")
    try:
        path = Path(unquote(parsed.path)).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkspaceEditError("workspace edit URI is invalid") from error
    if path != workspace and workspace not in path.parents:
        raise WorkspaceEditError("workspace edit path escapes the workspace")
    return path


def _document_entries(edit: object, workspace: Path) -> list[tuple[Path, int | None, list[object]]]:
    if not isinstance(edit, dict):
        raise WorkspaceEditError("workspace edit must be an object")
    has_changes = "changes" in edit
    has_document_changes = "documentChanges" in edit
    if has_changes and has_document_changes:
        raise WorkspaceEditError("workspace edit cannot combine changes and documentChanges")
    entries: list[tuple[Path, int | None, list[object]]] = []
    if has_changes:
        changes = edit.get("changes")
        if not isinstance(changes, dict):
            raise WorkspaceEditError("workspace edit changes must be an object")
        for uri, raw_edits in changes.items():
            if not isinstance(raw_edits, list):
                raise WorkspaceEditError("workspace edit changes must contain edit lists")
            entries.append((_path_from_uri(uri, workspace), None, raw_edits))
        return entries
    document_changes = edit.get("documentChanges")
    if not isinstance(document_changes, list):
        raise WorkspaceEditError("workspace edit requires changes or documentChanges")
    seen: set[Path] = set()
    for entry in document_changes:
        if not isinstance(entry, dict):
            raise WorkspaceEditError("workspace edit documentChanges entries must be objects")
        if entry.get("kind") in {"create", "rename", "delete"}:
            raise WorkspaceEditError("workspace edit resource operations are unsupported")
        text_document = entry.get("textDocument")
        raw_edits = entry.get("edits")
        if not isinstance(text_document, dict) or not isinstance(raw_edits, list):
            raise WorkspaceEditError("workspace edit documentChanges entry is invalid")
        path = _path_from_uri(text_document.get("uri"), workspace)
        if path in seen:
            raise WorkspaceEditError("workspace edit contains a duplicate document URI")
        seen.add(path)
        version = text_document.get("version")
        if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 0):
            raise WorkspaceEditError("workspace edit document version is invalid")
        entries.append((path, version, raw_edits))
    return entries


def _prepare_file(
    path: Path,
    expected_version: int | None,
    raw_edits: list[object],
    *,
    workspace: Path,
    tracker: DocumentTracker,
    encoding: PositionEncoding,
) -> PreparedFileEdit:
    try:
        snapshot = tracker.open_or_update(path)
    except ValueError as error:
        message = str(error)
        if "regular file" in message:
            raise WorkspaceEditError("workspace edit target must be an existing regular file") from error
        raise WorkspaceEditError(message) from error
    if expected_version is not None and expected_version != snapshot.version:
        raise WorkspaceEditError("workspace edit document version does not match the open document")

    normalized: list[tuple[int, int, int, str]] = []
    for index, raw_edit in enumerate(raw_edits):
        if not isinstance(raw_edit, dict) or not isinstance(raw_edit.get("newText"), str):
            raise WorkspaceEditError("workspace edit text edit is invalid")
        range_value = raw_edit.get("range")
        if not isinstance(range_value, dict):
            raise WorkspaceEditError("workspace edit range is invalid")
        try:
            server_start = _position(range_value.get("start"))
            server_end = _position(range_value.get("end"))
            stable_start = position_from_server(snapshot.text, server_start, encoding)
            stable_end = position_from_server(snapshot.text, server_end, encoding)
            start = text_offset_from_position(snapshot.text, stable_start)
            end = text_offset_from_position(snapshot.text, stable_end)
        except ValueError as error:
            raise WorkspaceEditError(f"workspace edit range is invalid: {error}") from error
        if end < start:
            raise WorkspaceEditError("workspace edit range end precedes its start")
        normalized.append((start, end, index, raw_edit["newText"]))

    ordered = sorted(normalized, key=lambda item: (item[0], item[1], item[2]))
    previous_start = previous_end = -1
    for start, end, _index, _text in ordered:
        if previous_end > start or (
            previous_start == start and previous_end != previous_start and end != start
        ):
            raise WorkspaceEditError("workspace edit ranges overlap")
        previous_start, previous_end = start, max(previous_end, end)

    target = snapshot.text
    for start, end, index, new_text in sorted(normalized, key=lambda item: (item[0], item[1], item[2]), reverse=True):
        del index
        target = target[:start] + new_text + target[end:]
    original_bytes = snapshot.text.encode("utf-8")
    target_bytes = target.encode("utf-8")
    return PreparedFileEdit(
        path=snapshot.path,
        relative_path=snapshot.path.relative_to(workspace).as_posix(),
        original_bytes=original_bytes,
        target_bytes=target_bytes,
        original_hash=snapshot.sha256,
        target_hash=hashlib.sha256(target_bytes).hexdigest(),
        version=snapshot.version,
        mode=snapshot.path.stat().st_mode,
    )


def _render_diff(files: list[PreparedFileEdit]) -> str:
    chunks: list[str] = []
    for file in files:
        chunks.extend(
            difflib.unified_diff(
                file.original_bytes.decode("utf-8").splitlines(keepends=True),
                file.target_bytes.decode("utf-8").splitlines(keepends=True),
                fromfile=f"a/{file.relative_path}",
                tofile=f"b/{file.relative_path}",
            )
        )
    return "".join(chunks)


class WorkspaceEditPreviewStore:
    def __init__(
        self,
        workspace: str | Path,
        *,
        limits: LanguageServiceLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.limits = limits or LanguageServiceLimits()
        self.clock = clock
        self._previews: OrderedDict[str, WorkspaceEditPreview] = OrderedDict()

    def create(
        self,
        edit: object,
        tracker: DocumentTracker,
        *,
        position_encoding: PositionEncoding,
        server_generation: int,
        config_generation: int,
        source_path: str | Path | None = None,
    ) -> WorkspaceEditPreview:
        entries = _document_entries(edit, self.workspace)
        files = [
            _prepare_file(
                path,
                version,
                raw_edits,
                workspace=self.workspace,
                tracker=tracker,
                encoding=position_encoding,
            )
            for path, version, raw_edits in entries
        ]
        files.sort(key=lambda file: file.relative_path)
        now = self.clock()
        preview = WorkspaceEditPreview(
            token=f"lsp-preview-{secrets.token_hex(16)}",
            files=tuple(files),
            diff=_render_diff(files),
            server_generation=server_generation,
            config_generation=config_generation,
            created_at=now,
            source_path=(
                Path(source_path).expanduser().resolve()
                if source_path is not None
                else None
            ),
        )
        self._previews[preview.token] = preview
        while len(self._previews) > self.limits.max_preview_tokens:
            self._previews.popitem(last=False)
        return preview

    def get(
        self,
        token: str,
        *,
        server_generation: int | None = None,
        config_generation: int | None = None,
    ) -> WorkspaceEditPreview:
        preview = self._previews.get(token)
        if preview is None:
            raise WorkspaceEditError("unknown workspace edit preview token")
        if self.clock() - preview.created_at > self.limits.token_ttl_seconds:
            self._previews.pop(token, None)
            raise WorkspaceEditError("workspace edit preview token expired")
        if (
            server_generation is not None
            and preview.server_generation != server_generation
        ) or (
            config_generation is not None
            and preview.config_generation != config_generation
        ):
            raise WorkspaceEditError("workspace edit preview generation is stale")
        return preview

    def consume(self, token: str) -> WorkspaceEditPreview:
        preview = self.get(token)
        self._previews.pop(token, None)
        return preview

    def apply(
        self,
        token: str,
        *,
        server_generation: int,
        config_generation: int,
        signal: object | None = None,
        write_bytes: Callable[[Path, bytes, int], None] = atomic_replace_bytes,
    ) -> WorkspaceEditApplyReport:
        preview = self.get(
            token,
            server_generation=server_generation,
            config_generation=config_generation,
        )
        if bool(getattr(signal, "aborted", False)):
            raise WorkspaceEditError("workspace edit apply was aborted before locking")

        def mutate() -> WorkspaceEditApplyReport:
            staged: list[tuple[PreparedFileEdit, bytes, int]] = []
            total_original_bytes = 0
            for file in preview.files:
                if bool(getattr(signal, "aborted", False)):
                    raise WorkspaceEditError("workspace edit apply was aborted before mutation")
                if file.path.is_symlink():
                    raise WorkspaceEditError("workspace edit containment changed after preview (symlink)")
                try:
                    resolved = file.path.resolve(strict=True)
                except (FileNotFoundError, OSError) as error:
                    raise WorkspaceEditError("workspace edit target must remain an existing regular file") from error
                if resolved != file.path or (
                    resolved != self.workspace and self.workspace not in resolved.parents
                ):
                    raise WorkspaceEditError("workspace edit containment changed after preview")
                if not resolved.is_file():
                    raise WorkspaceEditError("workspace edit target must remain an existing regular file")
                current_mode = stat.S_IMODE(resolved.stat().st_mode)
                parent_mode = stat.S_IMODE(resolved.parent.stat().st_mode)
                if not current_mode & 0o222 or not parent_mode & 0o222:
                    raise WorkspaceEditError("workspace edit target and parent must be writable")
                current = resolved.read_bytes()
                if hashlib.sha256(current).hexdigest() != file.original_hash:
                    raise WorkspaceEditError(
                        f"workspace edit target {file.relative_path!r} changed since preview"
                    )
                total_original_bytes += len(current)
                if total_original_bytes > self.limits.max_apply_original_bytes:
                    raise WorkspaceEditError("workspace edit original bytes exceed the apply limit")
                staged.append((file, current, current_mode))

            if bool(getattr(signal, "aborted", False)):
                raise WorkspaceEditError("workspace edit apply was aborted before mutation")
            self.consume(token)
            changed: list[tuple[PreparedFileEdit, bytes, int]] = []
            try:
                for file, original, mode in staged:
                    if file.target_bytes == original:
                        continue
                    if bool(getattr(signal, "aborted", False)):
                        raise WorkspaceEditError("workspace edit apply was aborted during writes")
                    try:
                        write_bytes(file.path, file.target_bytes, mode=mode)
                    except BaseException:
                        try:
                            current_hash = hashlib.sha256(file.path.read_bytes()).hexdigest()
                        except OSError:
                            current_hash = "unreadable"
                        if current_hash != file.original_hash:
                            changed.append((file, original, mode))
                        raise
                    else:
                        changed.append((file, original, mode))
                    if bool(getattr(signal, "aborted", False)):
                        raise WorkspaceEditError("workspace edit apply was aborted during writes")
                return WorkspaceEditApplyReport(
                    applied=True,
                    changed=tuple(file.relative_path for file, _original, _mode in changed),
                    restored=(),
                    unresolved=(),
                )
            except BaseException:  # noqa: BLE001 - report best-effort restoration explicitly.
                restored: list[str] = []
                unresolved: list[str] = []
                for file, original, mode in reversed(changed):
                    try:
                        write_bytes(file.path, original, mode=mode)
                        restored.append(file.relative_path)
                    except BaseException:  # noqa: BLE001 - unresolved paths are returned to the caller.
                        unresolved.append(file.relative_path)
                return WorkspaceEditApplyReport(
                    applied=False,
                    changed=tuple(file.relative_path for file, _original, _mode in changed),
                    restored=tuple(restored),
                    unresolved=tuple(unresolved),
                )

        return with_file_mutation_queues(
            [str(file.path) for file in preview.files],
            mutate,
        )


class ActionTokenStore:
    def __init__(
        self,
        *,
        limits: LanguageServiceLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or LanguageServiceLimits()
        self.clock = clock
        self._actions: OrderedDict[str, ActionToken] = OrderedDict()

    def create(
        self,
        action: dict[str, object],
        *,
        path: str,
        server_generation: int,
        config_generation: int,
    ) -> ActionToken:
        value = ActionToken(
            token=f"lsp-action-{secrets.token_hex(16)}",
            action=dict(action),
            path=str(path),
            server_generation=server_generation,
            config_generation=config_generation,
            created_at=self.clock(),
        )
        self._actions[value.token] = value
        while len(self._actions) > self.limits.max_action_tokens:
            self._actions.popitem(last=False)
        return value

    def resolve(
        self,
        token: str,
        *,
        server_generation: int,
        config_generation: int,
    ) -> ActionToken:
        value = self._actions.get(token)
        if value is None:
            raise WorkspaceEditError("unknown code-action token")
        if self.clock() - value.created_at > self.limits.token_ttl_seconds:
            self._actions.pop(token, None)
            raise WorkspaceEditError("code-action token expired")
        if (
            value.server_generation != server_generation
            or value.config_generation != config_generation
        ):
            raise WorkspaceEditError("code-action token generation is stale")
        return value

    def peek(self, token: str) -> ActionToken:
        value = self._actions.get(token)
        if value is None:
            raise WorkspaceEditError("unknown code-action token")
        if self.clock() - value.created_at > self.limits.token_ttl_seconds:
            self._actions.pop(token, None)
            raise WorkspaceEditError("code-action token expired")
        return value

    def consume(self, token: str, *, server_generation: int, config_generation: int) -> ActionToken:
        value = self.resolve(
            token,
            server_generation=server_generation,
            config_generation=config_generation,
        )
        self._actions.pop(token, None)
        return value


__all__ = [
    "ActionToken",
    "ActionTokenStore",
    "PreparedFileEdit",
    "WorkspaceEditError",
    "WorkspaceEditApplyReport",
    "WorkspaceEditPreview",
    "WorkspaceEditPreviewStore",
]
