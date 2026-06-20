"""AgentSession runtime host subset ported from Pi coding-agent runtime."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from appv22.coding_agent.agent_session import AgentSession
from appv22.coding_agent.extensions import emit_session_shutdown_event


@dataclass
class CreateAgentSessionRuntimeResult:
    session: AgentSession
    services: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Any] = field(default_factory=list)
    model_fallback_message: str | None = None

    @property
    def modelFallbackMessage(self) -> str | None:
        return self.model_fallback_message


CreateAgentSessionRuntimeFactory = Callable[[dict[str, Any]], CreateAgentSessionRuntimeResult | AgentSession | dict[str, Any]]
RebindSession = Callable[[AgentSession], object]


class SessionImportFileNotFoundError(FileNotFoundError):
    def __init__(self, file_path: str) -> None:
        super().__init__(f"File not found: {file_path}")
        self.file_path = file_path


class AgentSessionRuntime:
    """Owns the current AgentSession and replaces it with Pi-style lifecycle hooks."""

    def __init__(
        self,
        session: AgentSession,
        services: dict[str, Any],
        create_runtime: CreateAgentSessionRuntimeFactory,
        diagnostics: list[Any] | None = None,
        model_fallback_message: str | None = None,
    ) -> None:
        self._session = session
        self._services = services
        self._create_runtime = create_runtime
        self._diagnostics = list(diagnostics or [])
        self._model_fallback_message = model_fallback_message
        self._rebind_session: RebindSession | None = None
        self._before_session_invalidate: Callable[[], object] | None = None

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def services(self) -> dict[str, Any]:
        return self._services

    @property
    def cwd(self) -> str:
        return str(self._services.get("cwd") or self._session.cwd)

    @property
    def diagnostics(self) -> list[Any]:
        return self._diagnostics

    @property
    def model_fallback_message(self) -> str | None:
        return self._model_fallback_message

    @property
    def modelFallbackMessage(self) -> str | None:
        return self._model_fallback_message

    def set_rebind_session(self, rebind_session: RebindSession | None = None) -> None:
        self._rebind_session = rebind_session

    setRebindSession = set_rebind_session

    def set_before_session_invalidate(self, before_session_invalidate: Callable[[], object] | None = None) -> None:
        self._before_session_invalidate = before_session_invalidate

    setBeforeSessionInvalidate = set_before_session_invalidate

    def new_session(self, options: dict[str, Any] | None = None) -> dict[str, bool]:
        options = options or {}
        before_result = self._emit_before_switch("new")
        if before_result["cancelled"]:
            return before_result

        previous_session_file = self._session.session_path
        target_session_file = str(options.get("session_path") or self._next_session_path())
        self._teardown_current("new", target_session_file)
        self._apply(
            self._create_runtime(
                {
                    "cwd": self.cwd,
                    "agentDir": self._services.get("agentDir"),
                    "session_path": target_session_file,
                    "parent_session_path": options.get("parent_session_path") or options.get("parentSession"),
                    "session_start_event": {
                        "type": "session_start",
                        "reason": "new",
                        "previousSessionFile": previous_session_file,
                    },
                }
            )
        )
        self._finish_session_replacement(options.get("with_session") or options.get("withSession"))
        return {"cancelled": False}

    newSession = new_session

    def switch_session(self, session_path: str, options: dict[str, Any] | None = None) -> dict[str, bool]:
        options = options or {}
        target_session_file = str(Path(session_path).expanduser().resolve())
        before_result = self._emit_before_switch("resume", target_session_file)
        if before_result["cancelled"]:
            return before_result

        previous_session_file = self._session.session_path
        self._teardown_current("resume", target_session_file)
        self._apply(
            self._create_runtime(
                {
                    "cwd": str(options.get("cwd") or self.cwd),
                    "agentDir": self._services.get("agentDir"),
                    "session_path": target_session_file,
                    "session_start_event": {
                        "type": "session_start",
                        "reason": "resume",
                        "previousSessionFile": previous_session_file,
                    },
                }
            )
        )
        self._finish_session_replacement(options.get("with_session") or options.get("withSession"))
        return {"cancelled": False}

    switchSession = switch_session

    def fork(self, entry_id: str, options: dict[str, Any] | None = None) -> dict[str, object]:
        options = options or {}
        position = options.get("position") or "before"
        before_result = self._emit_before_fork(entry_id, position)
        if before_result["cancelled"]:
            return {"cancelled": True}

        selected_entry = self._session.get_session_entry(entry_id)
        if selected_entry is None:
            raise ValueError("Invalid entry ID for forking")

        selected_text: str | None = None
        if position == "at":
            target_leaf_id = selected_entry["id"]
        elif position == "before":
            if selected_entry.get("type") != "message" or selected_entry.get("message", {}).get("role") != "user":
                raise ValueError("Invalid entry ID for forking")
            target_leaf_id = selected_entry.get("parentId")
            selected_text = _extract_user_message_text(selected_entry.get("message", {}).get("content"))
        else:
            raise ValueError("position must be 'before' or 'at'")

        previous_session_file = self._session.session_path
        if target_leaf_id:
            target_session_file = self._session.create_branched_session(target_leaf_id)
        else:
            target_session_file = self._next_session_path()

        self._teardown_current("fork", target_session_file)
        self._apply(
            self._create_runtime(
                {
                    "cwd": self.cwd,
                    "agentDir": self._services.get("agentDir"),
                    "session_path": target_session_file,
                    "parent_session_path": previous_session_file if not target_leaf_id else None,
                    "session_start_event": {
                        "type": "session_start",
                        "reason": "fork",
                        "previousSessionFile": previous_session_file,
                    },
                }
            )
        )
        self._finish_session_replacement(options.get("with_session") or options.get("withSession"))
        result: dict[str, object] = {"cancelled": False}
        if selected_text is not None:
            result["selectedText"] = selected_text
        return result

    def import_from_jsonl(self, input_path: str, cwd_override: str | None = None) -> dict[str, bool]:
        resolved_path = Path(input_path).expanduser().resolve()
        if not resolved_path.exists():
            raise SessionImportFileNotFoundError(str(resolved_path))

        session_dir = self._session_dir()
        session_dir.mkdir(parents=True, exist_ok=True)
        destination_path = session_dir / resolved_path.name
        destination = str(destination_path)
        before_result = self._emit_before_switch("resume", destination)
        if before_result["cancelled"]:
            return before_result

        previous_session_file = self._session.session_path
        if destination_path.resolve() != resolved_path:
            shutil.copyfile(resolved_path, destination_path)

        next_cwd = cwd_override or _session_cwd(destination_path) or self.cwd
        self._teardown_current("resume", destination)
        self._apply(
            self._create_runtime(
                {
                    "cwd": next_cwd,
                    "agentDir": self._services.get("agentDir"),
                    "session_path": destination,
                    "session_start_event": {
                        "type": "session_start",
                        "reason": "resume",
                        "previousSessionFile": previous_session_file,
                    },
                }
            )
        )
        self._finish_session_replacement()
        return {"cancelled": False}

    importFromJsonl = import_from_jsonl

    def dispose(self) -> None:
        emit_session_shutdown_event(
            self._session.extension_runner,
            {"type": "session_shutdown", "reason": "quit"},
        )
        if self._before_session_invalidate:
            self._before_session_invalidate()
        self._session.dispose()

    def _emit_before_switch(self, reason: str, target_session_file: str | None = None) -> dict[str, bool]:
        runner = self._session.extension_runner
        if not runner.has_handlers("session_before_switch"):
            return {"cancelled": False}
        result = runner.emit(
            {
                "type": "session_before_switch",
                "reason": reason,
                "targetSessionFile": target_session_file,
            }
        )
        return {"cancelled": _is_cancelled(result)}

    def _emit_before_fork(self, entry_id: str, position: str) -> dict[str, bool]:
        runner = self._session.extension_runner
        if not runner.has_handlers("session_before_fork"):
            return {"cancelled": False}
        result = runner.emit(
            {
                "type": "session_before_fork",
                "entryId": entry_id,
                "position": position,
            }
        )
        return {"cancelled": _is_cancelled(result)}

    def _teardown_current(self, reason: str, target_session_file: str | None = None) -> None:
        emit_session_shutdown_event(
            self._session.extension_runner,
            {
                "type": "session_shutdown",
                "reason": reason,
                "targetSessionFile": target_session_file,
            },
        )
        if self._before_session_invalidate:
            self._before_session_invalidate()
        self._session.dispose()

    def _apply(self, raw_result: CreateAgentSessionRuntimeResult | AgentSession | dict[str, Any]) -> None:
        result = _coerce_result(raw_result)
        self._session = result.session
        self._services = result.services or self._services
        self._diagnostics = result.diagnostics
        self._model_fallback_message = result.model_fallback_message

    def _finish_session_replacement(self, with_session: Callable[[AgentSession], object] | None = None) -> None:
        if self._rebind_session:
            self._rebind_session(self._session)
        if with_session:
            with_session(self._session)

    def _next_session_path(self) -> str:
        session_dir = self._session_dir()
        session_dir.mkdir(parents=True, exist_ok=True)
        while True:
            path = session_dir / f"session-{uuid.uuid4().hex}.jsonl"
            if not path.exists():
                return str(path)

    def _session_dir(self) -> Path:
        current_path = self._session.session_path
        if current_path:
            return Path(current_path).expanduser().resolve().parent
        return Path(self.cwd).expanduser().resolve() / ".appv22-sessions"


def _coerce_result(raw_result: CreateAgentSessionRuntimeResult | AgentSession | dict[str, Any]) -> CreateAgentSessionRuntimeResult:
    if isinstance(raw_result, CreateAgentSessionRuntimeResult):
        return raw_result
    if isinstance(raw_result, AgentSession):
        return CreateAgentSessionRuntimeResult(session=raw_result)
    if isinstance(raw_result, dict):
        return CreateAgentSessionRuntimeResult(
            session=raw_result["session"],
            services=dict(raw_result.get("services") or {}),
            diagnostics=list(raw_result.get("diagnostics") or []),
            model_fallback_message=raw_result.get("model_fallback_message") or raw_result.get("modelFallbackMessage"),
        )
    raise TypeError(f"Unsupported runtime result: {type(raw_result).__name__}")


def _is_cancelled(result: object) -> bool:
    if isinstance(result, dict):
        return result.get("cancel") is True or result.get("cancelled") is True
    return getattr(result, "cancel", False) is True or getattr(result, "cancelled", False) is True


def _extract_user_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _session_cwd(path: Path) -> str | None:
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            entry = json.loads(raw_line)
            if entry.get("type") == "session" and isinstance(entry.get("cwd"), str):
                return entry["cwd"]
            return None
    except (OSError, json.JSONDecodeError):
        return None
    return None
