"""Discovery and resolution for app-owned persistent coding sessions."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from appv231.coding_agent.config import ENV_SESSION_DIR


_PREVIEW_LIMIT = 120


class SessionCatalogError(ValueError):
    """Base class for user-facing session selection failures."""


class SessionNotFoundError(SessionCatalogError):
    pass


class InvalidSessionError(SessionCatalogError):
    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"Session file {path} is invalid: {detail}")


class SessionAmbiguousError(SessionCatalogError):
    def __init__(self, value: str, paths: list[Path]) -> None:
        self.value = value
        self.paths = tuple(paths)
        rendered = "\n".join(f"- {path}" for path in paths)
        super().__init__(f"Session ID is ambiguous: {value}\n{rendered}")


@dataclass(frozen=True)
class SessionInfo:
    path: Path
    session_id: str
    cwd: Path
    created_at: datetime
    modified_at: datetime
    _modified_ns: int = field(repr=False, compare=False)
    name: str | None
    preview: str
    model: str | None


class SessionCatalog:
    """Lists and resolves JSONL sessions without mutating them."""

    def __init__(self, agent_dir: str, *, session_dir: str | None = None) -> None:
        self.agent_dir = Path(agent_dir).expanduser().resolve()
        configured = session_dir or os.environ.get(ENV_SESSION_DIR)
        self._configured_session_dir = Path(configured).expanduser().resolve() if configured else None
        self._diagnostics: tuple[str, ...] = ()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._diagnostics

    def workspace_directory(self, cwd: str) -> Path:
        if self._configured_session_dir is not None:
            return self._configured_session_dir
        resolved_cwd = str(Path(cwd).expanduser().resolve())
        safe_path = resolved_cwd.lstrip("/\\")
        for separator in ("/", "\\", ":"):
            safe_path = safe_path.replace(separator, "-")
        return self.agent_dir / "sessions" / f"--{safe_path}--"

    def new_session_path(self, cwd: str, session_id: str | None = None) -> tuple[str, str]:
        resolved_session_id = session_id or uuid.uuid4().hex
        session_dir = self.workspace_directory(cwd)
        session_dir.mkdir(parents=True, exist_ok=True)
        while True:
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            file_timestamp = timestamp.replace(":", "-").replace(".", "-")
            path = session_dir / f"{file_timestamp}_{resolved_session_id}.jsonl"
            if not path.exists():
                return str(path), resolved_session_id
            if session_id is None:
                resolved_session_id = uuid.uuid4().hex

    def list_for_cwd(self, cwd: str) -> list[SessionInfo]:
        resolved_cwd = Path(cwd).expanduser().resolve()
        candidates = self._candidate_paths(self.workspace_directory(str(resolved_cwd)))
        sessions = self._read_candidates(candidates)
        return [info for info in sessions if info.cwd == resolved_cwd]

    def list_all(self) -> list[SessionInfo]:
        root = self._configured_session_dir or self.agent_dir / "sessions"
        return self._read_candidates(self._candidate_paths(root))

    def continue_recent(self, cwd: str) -> SessionInfo:
        sessions = self.list_for_cwd(cwd)
        if not sessions:
            raise SessionNotFoundError("No previous session for this workspace.")
        return sessions[0]

    def resolve(self, value: str, *, cwd: str, launch_dir: str) -> SessionInfo:
        path_value = Path(value).expanduser()
        candidate = path_value if path_value.is_absolute() else Path(launch_dir).expanduser() / path_value
        if candidate.exists():
            return self._read_info(candidate.resolve())

        current_matches = self._id_matches(value, self.list_for_cwd(cwd))
        if len(current_matches) == 1:
            return current_matches[0]
        if len(current_matches) > 1:
            raise SessionAmbiguousError(value, [info.path for info in current_matches])

        all_matches = self._id_matches(value, self.list_all())
        if len(all_matches) == 1:
            return all_matches[0]
        if len(all_matches) > 1:
            raise SessionAmbiguousError(value, [info.path for info in all_matches])
        raise SessionNotFoundError(f"Session not found: {value}")

    def _candidate_paths(self, root: Path) -> list[Path]:
        if not root.exists():
            return []
        return sorted(path for path in root.rglob("*.jsonl") if path.is_file())

    def _read_candidates(self, paths: list[Path]) -> list[SessionInfo]:
        diagnostics: list[str] = []
        sessions: list[SessionInfo] = []
        for path in paths:
            try:
                sessions.append(self._read_info(path))
            except (InvalidSessionError, OSError) as error:
                diagnostics.append(str(error))
        self._diagnostics = tuple(diagnostics)
        return sorted(
            sessions,
            key=lambda info: (info._modified_ns, str(info.path)),
            reverse=True,
        )

    def _read_info(self, path: Path) -> SessionInfo:
        try:
            stat = path.stat()
            entries = _read_jsonl(path)
        except InvalidSessionError:
            raise
        except OSError as error:
            raise InvalidSessionError(path, str(error)) from error
        if not entries:
            raise InvalidSessionError(path, "empty file")
        header = entries[0]
        if header.get("type") != "session":
            raise InvalidSessionError(path, "first record is not a session header")
        session_id = header.get("id")
        cwd = header.get("cwd")
        if not isinstance(session_id, str) or not session_id:
            raise InvalidSessionError(path, "session header has no ID")
        if not isinstance(cwd, str) or not cwd:
            raise InvalidSessionError(path, "session header has no cwd")

        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        created_at = _parse_timestamp(header.get("timestamp"), fallback=modified_at)
        name: str | None = None
        preview = ""
        model: str | None = None
        for entry in entries[1:]:
            entry_type = entry.get("type")
            if entry_type == "session_info":
                raw_name = entry.get("name")
                name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
            elif entry_type == "model_change":
                provider = entry.get("provider")
                model_id = entry.get("modelId")
                if isinstance(provider, str) and isinstance(model_id, str):
                    model = f"{provider}/{model_id}"
            elif entry_type == "message":
                message = entry.get("message")
                if isinstance(message, dict) and message.get("role") == "user":
                    preview = _message_preview(message.get("content"))
        return SessionInfo(
            path=path.resolve(),
            session_id=session_id,
            cwd=Path(cwd).expanduser().resolve(),
            created_at=created_at,
            modified_at=modified_at,
            _modified_ns=stat.st_mtime_ns,
            name=name,
            preview=preview,
            model=model,
        )

    @staticmethod
    def _id_matches(value: str, sessions: list[SessionInfo]) -> list[SessionInfo]:
        suffix = f"_{value}.jsonl"
        return [
            info
            for info in sessions
            if info.session_id == value or info.path.name.endswith(suffix)
        ]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                if not raw_line.endswith((b"\n", b"\r")):
                    break
                raise InvalidSessionError(path, f"line {line_number}: {error}") from error
            if not isinstance(entry, dict):
                raise InvalidSessionError(path, f"line {line_number}: record is not an object")
            entries.append(entry)
    return entries


def _parse_timestamp(value: object, *, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def _message_preview(content: object) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    else:
        text = ""
    normalized = " ".join(text.split())
    if len(normalized) <= _PREVIEW_LIMIT:
        return normalized
    return f"{normalized[:_PREVIEW_LIMIT]}..."
