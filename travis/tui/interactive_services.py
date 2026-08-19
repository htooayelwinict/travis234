"""Narrow service ports and immutable service bundle for TUI collaborators."""

from __future__ import annotations

import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from travis.controller_ports import ExplicitController


class PortBoundController[DependenciesT](ExplicitController[DependenciesT]):
    """Own a responsibility-specific dependency record."""

    __slots__ = ()


class InteractiveRenderPort(Protocol):
    def add(self, component: object) -> None: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def request_render(self, force: bool = False) -> object | None: ...


class InteractiveStatusPort(Protocol):
    def set_message(self, message: str) -> None: ...

    def set_visible(self, visible: bool) -> None: ...

    def set_indicator(
        self,
        indicator: str | None,
        *,
        position: str = "suffix",
    ) -> None: ...


class InteractiveHistoryPort(Protocol):
    def add(self, component: object) -> None: ...

    def clear(self) -> None: ...


class InteractiveSessionBindingPort(Protocol):
    @property
    def session(self) -> object: ...


class _InteractiveAppSourcePort(Protocol):
    session: object
    compaction: object
    event_trace: object
    messages: object
    cwd: object
    session_runtime: object
    process_service: object
    renderer: object
    session_catalog: object
    _project_trust_override: object

    def process_owner(self, *, origin: str = "agent") -> object: ...

    def user_command_transport(self, request: object) -> object: ...

    def user_command_request(
        self,
        command: str,
        *,
        session: object,
        command_prefix: str | None = None,
        shell_path: str | None = None,
    ) -> object: ...

    def switch_session(self, path: str, *, cwd_override: str | None = None) -> object: ...

    def new_session(self) -> object: ...

    def rename_session(self, name: str | None) -> object: ...

    def fork_session(self, entry_id: str, *, position: str = "before") -> object: ...

    def clone_session(self) -> object: ...

    def session_tree(self) -> object: ...

    def navigate_session_tree(self, target_id: str, options: dict | None = None) -> object: ...

    def export_session_jsonl(self, output_path: str | None = None) -> object: ...

    def import_session(self, input_path: str, *, cwd_override: str | None = None) -> object: ...

    def run_turn(
        self,
        prompt: str,
        stream_fn: object | None = None,
        on_post_response_compaction_start: Callable[[], object] | None = None,
        image_paths: list[str] | tuple[str, ...] | None = None,
        input_source: str = "interactive",
    ) -> object: ...


class InteractiveAppAdapter:
    """Expose named TUI application operations without retaining the app facade."""

    __slots__ = ("_app_ref",)

    def __init__(self, app: object) -> None:
        try:
            self._app_ref = weakref.ref(app)
        except TypeError as error:
            raise TypeError("interactive app dependency must support weak references") from error

    def _app(self) -> _InteractiveAppSourcePort:
        app = self._app_ref()
        if app is None:
            raise RuntimeError("interactive application is no longer available")
        return cast(_InteractiveAppSourcePort, app)

    @property
    def session(self) -> object:
        return self._app().session

    @property
    def compaction(self) -> object:
        return self._app().compaction

    @property
    def event_trace(self) -> object:
        return self._app().event_trace

    @property
    def messages(self) -> object:
        return self._app().messages

    @property
    def cwd(self) -> object:
        return self._app().cwd

    @property
    def session_runtime(self) -> object:
        return self._app().session_runtime

    @property
    def process_service(self) -> object:
        return self._app().process_service

    @property
    def renderer(self) -> object:
        return self._app().renderer

    @property
    def session_catalog(self) -> object:
        return self._app().session_catalog

    @property
    def _project_trust_override(self) -> object:
        return self._app()._project_trust_override

    @_project_trust_override.setter
    def _project_trust_override(self, value: object) -> None:
        self._app()._project_trust_override = value

    def process_owner(self, *, origin: str = "agent") -> object:
        return self._app().process_owner(origin=origin)

    def user_command_transport(self, request: object) -> object:
        return self._app().user_command_transport(request)

    def user_command_request(
        self,
        command: str,
        *,
        session: object,
        command_prefix: str | None = None,
        shell_path: str | None = None,
    ) -> object:
        return self._app().user_command_request(
            command,
            session=session,
            command_prefix=command_prefix,
            shell_path=shell_path,
        )

    def switch_session(self, path: str, *, cwd_override: str | None = None) -> object:
        return self._app().switch_session(path, cwd_override=cwd_override)

    def new_session(self) -> object:
        return self._app().new_session()

    def rename_session(self, name: str | None) -> object:
        return self._app().rename_session(name)

    def fork_session(self, entry_id: str, *, position: str = "before") -> object:
        return self._app().fork_session(entry_id, position=position)

    def clone_session(self) -> object:
        return self._app().clone_session()

    def session_tree(self) -> object:
        return self._app().session_tree()

    def navigate_session_tree(self, target_id: str, options: dict | None = None) -> object:
        return self._app().navigate_session_tree(target_id, options)

    def export_session_jsonl(self, output_path: str | None = None) -> object:
        return self._app().export_session_jsonl(output_path)

    def import_session(self, input_path: str, *, cwd_override: str | None = None) -> object:
        return self._app().import_session(input_path, cwd_override=cwd_override)

    def run_turn(
        self,
        prompt: str,
        stream_fn: object | None = None,
        on_post_response_compaction_start: Callable[[], object] | None = None,
        image_paths: list[str] | tuple[str, ...] | None = None,
        input_source: str = "interactive",
    ) -> object:
        return self._app().run_turn(
            prompt,
            stream_fn=stream_fn,
            on_post_response_compaction_start=on_post_response_compaction_start,
            image_paths=image_paths,
            input_source=input_source,
        )


class InteractiveSessionRebindController(Protocol):
    def rebind_session(self, session: object) -> object: ...


class InteractiveOwnerThreadPort(Protocol):
    def is_owner_thread(self) -> bool: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def call_later(
        self,
        delay: float,
        callback: Callable[[], None],
    ) -> object: ...


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
    "InteractiveAppAdapter",
    "InteractiveHistoryPort",
    "InteractiveOwnerThreadPort",
    "InteractiveRenderPort",
    "InteractiveServices",
    "InteractiveSessionBindingPort",
    "InteractiveSessionRebindController",
    "InteractiveStatusPort",
    "InteractiveTerminalInputPort",
    "InteractiveThemePort",
    "PortBoundController",
]
