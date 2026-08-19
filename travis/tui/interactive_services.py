"""Narrow service ports and immutable service bundle for TUI collaborators."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class InteractiveRenderPort(Protocol):
    def add(self, component: object) -> None: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def request_render(self, force: bool = False) -> object | None: ...


class InteractiveStatusPort(Protocol):
    def set_message(self, message: str) -> None: ...

    def set_visible(self, visible: bool) -> None: ...

    def set_indicator(self, indicator: str | None, *, position: str = "suffix") -> None: ...


class InteractiveHistoryPort(Protocol):
    def add(self, component: object) -> None: ...

    def clear(self) -> None: ...


class InteractiveSessionBindingPort(Protocol):
    @property
    def session(self) -> object: ...

    def replace_session(self, session: object) -> object: ...


class InteractiveOwnerThreadPort(Protocol):
    def is_owner_thread(self) -> bool: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def call_later(self, delay: float, callback: Callable[[], None]) -> object: ...


class InteractiveTerminalInputPort(Protocol):
    def read(self, prompt: str) -> str: ...

    def select(self, title: str, choices: Sequence[str]) -> str | None: ...


class InteractiveThemePort(Protocol):
    def role(self, name: str) -> object: ...


@dataclass(frozen=True, slots=True)
class InteractiveServices:
    render: InteractiveRenderPort
    status: InteractiveStatusPort
    history: InteractiveHistoryPort
    sessions: InteractiveSessionBindingPort
    owner_thread: InteractiveOwnerThreadPort
    terminal_input: InteractiveTerminalInputPort
    theme: InteractiveThemePort


__all__ = [
    "InteractiveHistoryPort",
    "InteractiveOwnerThreadPort",
    "InteractiveRenderPort",
    "InteractiveServices",
    "InteractiveSessionBindingPort",
    "InteractiveStatusPort",
    "InteractiveTerminalInputPort",
    "InteractiveThemePort",
]
