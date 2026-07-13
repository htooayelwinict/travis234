"""Discovery and resolution for app-owned persistent coding sessions."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from travis.coding_agent.config import ENV_SESSION_DIR
from travis.coding_agent.session_index import SessionIndex, SessionIndexRecord, SessionScanStats


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

    def __init__(
        self,
        agent_dir: str,
        *,
        session_dir: str | None = None,
        index: SessionIndex | None = None,
    ) -> None:
        self.agent_dir = Path(agent_dir).expanduser().resolve()
        configured = session_dir or os.environ.get(ENV_SESSION_DIR)
        self._configured_session_dir = Path(configured).expanduser().resolve() if configured else None
        root = self._configured_session_dir or self.agent_dir / "sessions"
        self.index = index or SessionIndex(root / "catalog.sqlite3")
        self._owns_index = index is None
        self._diagnostics: tuple[str, ...] = ()
        self.scan_stats = SessionScanStats()

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
        self._refresh_index()
        return [_session_info(record) for record in self.index.query(resolved_cwd)]

    def list_all(self) -> list[SessionInfo]:
        self._refresh_index()
        return [_session_info(record) for record in self.index.query()]

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

    def _refresh_index(self) -> None:
        root = self._configured_session_dir or self.agent_dir / "sessions"
        paths = self._candidate_paths(root)
        self.scan_stats = self.index.reconcile(paths)
        self.index.remove_missing(paths)
        self._diagnostics = self.index.diagnostics

    def _read_info(self, path: Path) -> SessionInfo:
        resolved = path.expanduser().resolve()
        self.scan_stats = self.index.reconcile([resolved])
        self._diagnostics = self.index.diagnostics
        record = next((item for item in self.index.query() if item.path == resolved), None)
        if record is None:
            detail = self._diagnostics[0] if self._diagnostics else "session metadata could not be indexed"
            raise InvalidSessionError(resolved, detail)
        return _session_info(record)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    @staticmethod
    def _id_matches(value: str, sessions: list[SessionInfo]) -> list[SessionInfo]:
        suffix = f"_{value}.jsonl"
        return [
            info
            for info in sessions
            if info.session_id == value or info.path.name.endswith(suffix)
        ]


def _session_info(record: SessionIndexRecord) -> SessionInfo:
    return SessionInfo(
        path=record.path,
        session_id=record.session_id,
        cwd=record.cwd,
        created_at=record.created_at,
        modified_at=datetime.fromtimestamp(record.modified_ns / 1_000_000_000, timezone.utc),
        _modified_ns=record.modified_ns,
        name=record.name,
        preview=record.preview,
        model=record.model,
    )
