"""Static ports and supported surface for the interactive facade."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


INTERACTIVE_MODE_PUBLIC_MEMBERS: frozenset[str] = frozenset(
    {
        "active_editor",
        "add_autocomplete_provider",
        "add_terminal_input_listener",
        "app",
        "autocomplete_provider",
        "create_base_autocomplete_provider",
        "editor_text",
        "extension_statuses",
        "footer",
        "footer_data_provider",
        "generation_param_warnings",
        "generation_params",
        "get_autocomplete_suggestions",
        "get_editor_text",
        "hide_thinking_block",
        "history",
        "init",
        "input_fn",
        "motion_controller",
        "paste_to_editor",
        "prompt_extension_confirm",
        "prompt_extension_custom",
        "prompt_extension_editor",
        "prompt_extension_input",
        "prompt_extension_select",
        "run",
        "set_editor_text",
        "set_extension_footer",
        "set_extension_header",
        "set_extension_status",
        "set_extension_widget",
        "set_hidden_thinking_label",
        "set_terminal_title",
        "set_working_indicator",
        "set_working_message",
        "set_working_visible",
        "setup_autocomplete_provider",
        "status",
        "theme_context",
        "theme_controller",
        "theme_registry",
        "tool_approval_broker",
        "tui",
    }
)


class TerminalRenderPort(Protocol):
    """Terminal rendering operations shared by interactive controllers and fakes."""

    def post(self, callback: Callable[[], None]) -> None: ...

    def request_render(self, force: bool = False) -> object | None: ...


class OwnerThreadDispatcherPort(Protocol):
    """Owner-thread scheduling operations shared across interactive controllers."""

    def is_owner_thread(self) -> bool: ...

    def post(self, callback: Callable[[], None]) -> None: ...

    def call_later(self, delay: float, callback: Callable[[], None]) -> object: ...


class InteractiveSessionPort(Protocol):
    """Session state shared by more than one interactive controller."""

    @property
    def is_streaming(self) -> bool: ...

    @property
    def is_compacting(self) -> bool: ...

    def reload(self) -> None: ...

    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]: ...


class InteractiveAppPort(Protocol):
    """Application access needed by interactive controllers and their fakes."""

    @property
    def session(self) -> InteractiveSessionPort: ...

    @property
    def tui(self) -> TerminalRenderPort: ...


__all__ = [
    "INTERACTIVE_MODE_PUBLIC_MEMBERS",
    "InteractiveAppPort",
    "InteractiveSessionPort",
    "OwnerThreadDispatcherPort",
    "TerminalRenderPort",
]
