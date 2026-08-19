"""Narrow structural ports used by coding-session collaborators."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Protocol


class SessionEventPort(Protocol):
    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]: ...

    def emit(self, event: object) -> None: ...


class SessionModelSettingsPort(Protocol):
    @property
    def model(self) -> object: ...

    @property
    def thinking_level(self) -> str: ...

    def set_model(self, model: object) -> None: ...


class SessionPersistencePort(Protocol):
    @property
    def session_path(self) -> str | None: ...

    def append_custom_entry(self, custom_type: str, data: object = None) -> str: ...

    def get_session_entry(self, entry_id: str) -> Mapping[str, object] | None: ...

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str: ...


class SessionMessageStatePort(Protocol):
    @property
    def messages(self) -> Sequence[object]: ...

    def replace_messages(self, messages: Sequence[object]) -> None: ...


class SessionToolRegistryPort(Protocol):
    def get_active_tool_names(self) -> list[str]: ...

    def get_tool_definition(self, name: str) -> object | None: ...

    def refresh_tools(self) -> None: ...


class SessionPolicyPort(Protocol):
    def evaluate_tool_call(self, name: str, arguments: Mapping[str, object]) -> object: ...


class SessionExtensionPort(Protocol):
    def call(self, event_name: str, payload: object) -> object: ...


class SessionProcessContextPort(Protocol):
    def resolve(self, process_id: str) -> object | None: ...


class SessionSubagentPort(Protocol):
    def snapshot(self) -> object: ...

    def cancel(self, task_id: str, reason: str | None = None) -> object: ...


class SessionTurnMailboxPort(Protocol):
    def enqueue(self, kind: str, text: str, images: Iterable[object] | None = None) -> object: ...

    def drain(self, kind: str, *, mode: str) -> list[object]: ...

    def clear(self, kind: str) -> list[object]: ...


class SessionCancellationPort(Protocol):
    @property
    def aborted(self) -> bool: ...

    def abort(self, reason: str | None = None) -> None: ...


__all__ = [
    "SessionCancellationPort",
    "SessionEventPort",
    "SessionExtensionPort",
    "SessionMessageStatePort",
    "SessionModelSettingsPort",
    "SessionPersistencePort",
    "SessionPolicyPort",
    "SessionProcessContextPort",
    "SessionSubagentPort",
    "SessionToolRegistryPort",
    "SessionTurnMailboxPort",
]
