"""Narrow service ports and immutable service bundle for TUI collaborators."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from travis.controller_ports import BoundController


class ControllerDelegate:
    """Descriptor that exposes one named method from an owned controller."""

    __slots__ = ("controller_name", "method_name")

    def __init__(self, controller_name: str, method_name: str) -> None:
        self.controller_name = controller_name
        self.method_name = method_name

    def __get__(self, instance: object, owner: type[object]) -> object:
        if instance is None:
            return self
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        return getattr(controller, self.method_name)

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise TypeError("controller delegates must be bound to a runtime instance")


def install_controller_delegates(
    owner: type[object],
    methods: dict[str, tuple[str, ...]],
) -> None:
    for controller_name, names in methods.items():
        for name in names:
            if name in owner.__dict__:
                raise ValueError(f"delegate would replace explicit runtime member: {name}")
            setattr(owner, name, ControllerDelegate(controller_name, name))


class PortBoundController[ControllerPortT](BoundController[ControllerPortT]):
    """Bind legacy-shaped method bodies to an explicit structural port.

    This small bridge lets a controller retain its characterized method body while
    ownership moves off the runtime.  The controller stores only the injected port;
    it does not inherit from, import, or dynamically inspect either public facade.
    """



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


class InteractiveDynamicPort(Protocol):
    def __getattribute__[AttributeT](self, name: str) -> AttributeT: ...


class InteractiveViewPort(Protocol):
    """View-facing state and render services supplied by the composition root."""

    app: InteractiveDynamicPort
    tui: InteractiveDynamicPort
    history: InteractiveHistoryPort
    status: InteractiveStatusPort
    theme_context: InteractiveThemePort

    def __getattribute__[AttributeT](self, name: str) -> AttributeT: ...


class InteractiveMotionPort(Protocol):
    """Motion-facing status state supplied by the composition root."""

    tui: InteractiveDynamicPort
    status: InteractiveStatusPort
    motion_controller: InteractiveDynamicPort
    extension_statuses: dict[str, str]
    extension_status_states: dict[str, str]

    def __getattribute__[AttributeT](self, name: str) -> AttributeT: ...


class InteractiveCommandPort(Protocol):
    """Command-loop state and named handlers supplied by the composition root."""

    app: InteractiveDynamicPort
    tui: InteractiveDynamicPort
    history: InteractiveHistoryPort
    status: InteractiveStatusPort

    def __getattribute__[AttributeT](self, name: str) -> AttributeT: ...


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
    "ControllerDelegate",
    "InteractiveCommandPort",
    "InteractiveMotionPort",
    "InteractiveOwnerThreadPort",
    "PortBoundController",
    "install_controller_delegates",
    "InteractiveRenderPort",
    "InteractiveServices",
    "InteractiveSessionBindingPort",
    "InteractiveStatusPort",
    "InteractiveTerminalInputPort",
    "InteractiveThemePort",
    "InteractiveViewPort",
]
