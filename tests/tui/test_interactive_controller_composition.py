from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from travis.tui.interactive_controllers import InteractiveControllers
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState
from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.tui.interactive_mode import InteractiveMode


INTERACTIVE_CONTROLLER_NAMES = (
    "command_dispatch",
    "view",
    "model_auth",
    "params",
    "processes",
    "lsp",
    "memory",
    "operations",
    "subagents",
    "sessions",
    "extensions",
    "turns",
    "shutdown",
    "motion",
)


def test_interactive_controller_bundle_is_frozen_slotted_and_complete() -> None:
    values = {name: object() for name in INTERACTIVE_CONTROLLER_NAMES}
    controllers = InteractiveControllers(**values)

    assert tuple(field.name for field in fields(controllers)) == INTERACTIVE_CONTROLLER_NAMES
    assert not hasattr(controllers, "__dict__")
    with pytest.raises(FrozenInstanceError):
        controllers.view = object()  # type: ignore[misc]


def test_interactive_state_records_keep_ui_and_lifecycle_mutation_explicit() -> None:
    state = InteractiveState()
    lifecycle = InteractiveLifecycleState()

    state.editor_text = "hello"
    state.prompt_history.append("hello")
    lifecycle.shutdown_requested = True

    assert state.editor_text == "hello"
    assert state.prompt_history == ["hello"]
    assert lifecycle.shutdown_requested is True
    assert "session" not in {field.name for field in fields(state)}
    assert "process_service" not in {field.name for field in fields(lifecycle)}


def test_interactive_runtime_owns_the_typed_controller_bundle(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime

    assert isinstance(runtime.controllers, InteractiveControllers)
    assert type(runtime.controllers.view).__name__ == "InteractiveView"
    assert runtime.controllers.shutdown is runtime


def test_view_motion_and_dispatch_are_owned_collaborators(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime

    assert runtime.controllers.view is not runtime
    assert runtime.controllers.motion is not runtime
    assert runtime.controllers.command_dispatch is not runtime
    assert all(
        owner not in type(runtime).__bases__
        for owner in (
            type(runtime.controllers.view),
            type(runtime.controllers.motion),
            type(runtime.controllers.command_dispatch),
        )
    )
