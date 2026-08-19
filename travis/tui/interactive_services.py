"""Narrow service ports and immutable service bundle for TUI collaborators."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import MethodType
from typing import Generic, Protocol, TypeVar


ControllerPortT = TypeVar("ControllerPortT")


class PortBoundController(Generic[ControllerPortT]):
    """Bind legacy-shaped method bodies to an explicit structural port.

    This small bridge lets a controller retain its characterized method body while
    ownership moves off the runtime.  The controller stores only the injected port;
    it does not inherit from, import, or dynamically inspect either public facade.
    """

    __slots__ = ("_port",)

    def __init__(self, port: ControllerPortT) -> None:
        object.__setattr__(self, "_port", port)

    def __getattribute__(self, name: str) -> object:
        attribute = object.__getattribute__(self, name)
        if isinstance(attribute, MethodType) and attribute.__self__ is self:
            port = object.__getattribute__(self, "_port")
            return attribute.__func__.__get__(port, type(port))
        return attribute


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


class InteractiveViewPort(Protocol):
    """View-facing state and render services supplied by the composition root."""

    app: object
    tui: object
    history: object
    status: object
    theme_context: object


class InteractiveMotionPort(Protocol):
    """Motion-facing status state supplied by the composition root."""

    tui: object
    status: object
    motion_controller: object
    extension_statuses: dict[str, str]
    extension_status_states: dict[str, str]


class InteractiveCommandPort(Protocol):
    """Command-loop state and named handlers supplied by the composition root."""

    app: object
    tui: object
    history: object
    status: object


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
    "InteractiveCommandPort",
    "InteractiveMotionPort",
    "InteractiveOwnerThreadPort",
    "PortBoundController",
    "InteractiveRenderPort",
    "InteractiveServices",
    "InteractiveSessionBindingPort",
    "InteractiveStatusPort",
    "InteractiveTerminalInputPort",
    "InteractiveThemePort",
    "InteractiveViewPort",
]
