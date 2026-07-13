"""AgentSession runtime host subset ported from Travis coding-agent runtime."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.extensions import emit_session_shutdown_event
from travis.coding_agent.session_catalog import SessionCatalog


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
AgentSessionRuntimeDiagnostic = dict[str, Any]
RebindSession = Callable[[AgentSession], object]


@dataclass(frozen=True)
class SessionCwdIssue:
    session_cwd: str
    fallback_cwd: str
    session_file: str | None = None

    @property
    def sessionCwd(self) -> str:
        return self.session_cwd

    @property
    def fallbackCwd(self) -> str:
        return self.fallback_cwd

    @property
    def sessionFile(self) -> str | None:
        return self.session_file


class SessionImportFileNotFoundError(FileNotFoundError):
    def __init__(self, file_path: str) -> None:
        super().__init__(f"File not found: {file_path}")
        self.file_path = file_path


class MissingSessionCwdError(RuntimeError):
    def __init__(self, issue: SessionCwdIssue) -> None:
        super().__init__(format_missing_session_cwd_error(issue))
        self.issue = issue


class AgentSessionRuntime:
    """Owns the current AgentSession and replaces it with lifecycle hooks."""

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
        replacement = self._create_runtime(
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
                "defer_session_start": True,
            }
        )
        self._activate_replacement(
            replacement,
            reason="new",
            target_session_file=target_session_file,
            with_session=options.get("with_session") or options.get("withSession"),
        )
        return {"cancelled": False}

    newSession = new_session

    def switch_session(self, session_path: str, options: dict[str, Any] | None = None) -> dict[str, bool]:
        options = options or {}
        target_session_file = str(Path(session_path).expanduser().resolve())
        before_result = self._emit_before_switch("resume", target_session_file)
        if before_result["cancelled"]:
            return before_result

        cwd_override = options.get("cwd") or options.get("cwdOverride")
        stored_cwd = _session_cwd(Path(target_session_file))
        if cwd_override is None:
            assert_session_cwd_exists(target_session_file, stored_cwd, self.cwd)
        next_cwd = str(cwd_override or stored_cwd or self.cwd)
        previous_session_file = self._session.session_path
        replacement = self._create_runtime(
            {
                "cwd": next_cwd,
                "agentDir": self._services.get("agentDir"),
                "session_path": target_session_file,
                "session_start_event": {
                    "type": "session_start",
                    "reason": "resume",
                    "previousSessionFile": previous_session_file,
                },
                "defer_session_start": True,
            }
        )
        self._activate_replacement(
            replacement,
            reason="resume",
            target_session_file=target_session_file,
            with_session=options.get("with_session") or options.get("withSession"),
        )
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

        stored_cwd = _session_cwd(destination_path)
        if cwd_override is None:
            assert_session_cwd_exists(destination, stored_cwd, self.cwd)
        next_cwd = cwd_override or stored_cwd or self.cwd
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
        self._services = {**self._services, **result.services}
        self._diagnostics = result.diagnostics
        self._model_fallback_message = result.model_fallback_message

    def _activate_replacement(
        self,
        raw_result: CreateAgentSessionRuntimeResult | AgentSession | dict[str, Any],
        *,
        reason: str,
        target_session_file: str,
        with_session: Callable[[AgentSession], object] | None,
    ) -> None:
        result = _coerce_result(raw_result)
        try:
            self._teardown_current(reason, target_session_file)
        except BaseException:
            result.session.dispose()
            raise
        self._apply(result)
        self._session.emit_deferred_session_start()
        self._finish_session_replacement(with_session)

    def _finish_session_replacement(self, with_session: Callable[[AgentSession], object] | None = None) -> None:
        if self._rebind_session:
            self._rebind_session(self._session)
        if with_session:
            with_session(self._session)

    def _next_session_path(self) -> str:
        catalog = self._services.get("sessionCatalog") or self._services.get("session_catalog")
        if not isinstance(catalog, SessionCatalog):
            catalog = SessionCatalog(
                str(self._services.get("agentDir") or Path.home() / ".travis234" / "agent")
            )
        path, _session_id = catalog.new_session_path(self.cwd)
        return path

    def _session_dir(self) -> Path:
        current_path = self._session.session_path
        if current_path:
            return Path(current_path).expanduser().resolve().parent
        catalog = self._services.get("sessionCatalog") or self._services.get("session_catalog")
        if isinstance(catalog, SessionCatalog):
            return catalog.workspace_directory(self.cwd)
        return SessionCatalog(
            str(self._services.get("agentDir") or Path.home() / ".travis234" / "agent")
        ).workspace_directory(self.cwd)


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


def create_agent_session_runtime(
    create_runtime: CreateAgentSessionRuntimeFactory,
    options: dict[str, Any],
) -> AgentSessionRuntime:
    session_file, session_cwd = _session_source_file_and_cwd(options)
    assert_session_cwd_exists(session_file, session_cwd, str(options.get("cwd") or "."))
    result = _coerce_result(create_runtime(options))
    return AgentSessionRuntime(
        result.session,
        result.services,
        create_runtime,
        result.diagnostics,
        result.model_fallback_message,
    )


createAgentSessionRuntime = create_agent_session_runtime


def get_missing_session_cwd_issue(
    session_file: str | None,
    session_cwd: str | None,
    fallback_cwd: str,
) -> SessionCwdIssue | None:
    if not session_file or not session_cwd or Path(session_cwd).expanduser().exists():
        return None
    return SessionCwdIssue(
        session_file=str(session_file),
        session_cwd=str(session_cwd),
        fallback_cwd=str(fallback_cwd),
    )


def format_missing_session_cwd_error(issue: SessionCwdIssue) -> str:
    session_file = f"\nSession file: {issue.session_file}" if issue.session_file else ""
    return (
        f"Stored session working directory does not exist: {issue.session_cwd}"
        f"{session_file}\nCurrent working directory: {issue.fallback_cwd}"
    )


def format_missing_session_cwd_prompt(issue: SessionCwdIssue) -> str:
    return (
        f"cwd from session file does not exist\n{issue.session_cwd}\n\n"
        f"continue in current cwd\n{issue.fallback_cwd}"
    )


def assert_session_cwd_exists(session_file: str | None, session_cwd: str | None, fallback_cwd: str) -> None:
    issue = get_missing_session_cwd_issue(session_file, session_cwd, fallback_cwd)
    if issue:
        raise MissingSessionCwdError(issue)


getMissingSessionCwdIssue = get_missing_session_cwd_issue
formatMissingSessionCwdError = format_missing_session_cwd_error
formatMissingSessionCwdPrompt = format_missing_session_cwd_prompt
assertSessionCwdExists = assert_session_cwd_exists


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


def _session_source_file_and_cwd(options: dict[str, Any]) -> tuple[str | None, str | None]:
    session_file = options.get("session_path") or options.get("sessionPath")
    session_cwd = options.get("session_cwd") or options.get("sessionCwd")
    session_manager = options.get("sessionManager") or options.get("session_manager")
    if session_file is None and session_manager is not None:
        session_file = _call_optional(session_manager, "getSessionFile", "get_session_file")
    if session_cwd is None and session_manager is not None:
        session_cwd = _call_optional(session_manager, "getCwd", "get_cwd")
    if session_cwd is None and session_file:
        session_cwd = _session_cwd(Path(str(session_file)))
    return (str(session_file) if session_file else None, str(session_cwd) if session_cwd else None)


def _call_optional(target: object, *names: str) -> object | None:
    for name in names:
        method = getattr(target, name, None)
        if callable(method):
            return method()
    return None
