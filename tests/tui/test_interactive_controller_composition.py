from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from travis.tui.interactive_controllers import (
    INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES,
    InteractiveControllers,
)
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState
from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.tui.interactive_mode import InteractiveMode
from travis.tui.interactive_mode import _InteractiveRuntime
from travis.tui.interactive_services import InteractiveServices
from travis.runtime_facade import RuntimeFacade


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
    assert type(runtime.controllers.shutdown).__name__ == "InteractiveShutdown"


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


def test_model_and_parameter_owners_are_owned_collaborators(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    owners = (
        runtime.controllers.model_auth,
        runtime.controllers.params,
    )

    assert all(owner is not runtime for owner in owners)
    assert all(type(owner) not in type(runtime).__bases__ for owner in owners)


def test_process_and_inspection_owners_are_owned_collaborators(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    owners = (
        runtime.controllers.processes,
        runtime.controllers.lsp,
        runtime.controllers.memory,
        runtime.controllers.operations,
    )

    assert all(owner is not runtime for owner in owners)
    assert all(type(owner) not in type(runtime).__bases__ for owner in owners)


def test_subagent_and_session_owners_are_owned_collaborators(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    owners = (runtime.controllers.subagents, runtime.controllers.sessions)

    assert all(owner is not runtime for owner in owners)
    assert all(type(owner) not in type(runtime).__bases__ for owner in owners)


def test_interactive_runtime_is_plain_composition_after_lifecycle_extraction(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime

    assert _InteractiveRuntime.__bases__ == (object,)
    assert runtime.controllers.extensions is not runtime
    assert runtime.controllers.turns is not runtime
    assert runtime.controllers.shutdown is not runtime


def test_interactive_controller_methods_remain_bound_to_the_controller(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime

    assert runtime.controllers.command_dispatch.run.__self__ is runtime.controllers.command_dispatch
    assert runtime.run.__self__ is runtime.controllers.command_dispatch
    assert runtime.controllers.view.init.__self__ is runtime.controllers.view


def test_interactive_controllers_do_not_retain_a_runtime_or_public_facade(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    facade = InteractiveMode(app)
    runtime = facade._runtime

    for controller in tuple(getattr(runtime.controllers, name) for name in INTERACTIVE_CONTROLLER_NAMES):
        retained = [
            value
            for cls in type(controller).__mro__
            for slot in getattr(cls, "__slots__", ())
            if isinstance(slot, str) and hasattr(controller, slot)
            for value in (object.__getattribute__(controller, slot),)
        ]
        assert runtime not in retained
        assert not any(isinstance(value, RuntimeFacade) for value in retained)
        dependencies = controller.dependencies
        assert is_dataclass(dependencies)
        assert runtime not in (getattr(dependencies, field.name) for field in fields(dependencies))

    for name in INTERACTIVE_CONTROLLER_NAMES:
        port = getattr(runtime.controllers, name).dependencies.port
        assert port.declared_names == frozenset(INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES[name])


def test_declared_interactive_state_and_services_are_real_controller_dependencies(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime

    assert isinstance(runtime.state, InteractiveState)
    assert isinstance(runtime.lifecycle, InteractiveLifecycleState)
    assert isinstance(runtime.services, InteractiveServices)
    assert runtime.controllers.view.dependencies.state is runtime.state
    assert runtime.controllers.shutdown.dependencies.lifecycle is runtime.lifecycle
    runtime.editor_text = "bound state"
    runtime._shutdown_requested = True
    assert runtime.state.editor_text == "bound state"
    assert runtime.lifecycle.shutdown_requested is True
    assert runtime.controllers.view.dependencies.services.history is runtime.history


class _RebindRecorder:
    def __init__(self, binding: object, *, fail_on: object | None = None) -> None:
        self.binding = binding
        self.fail_on = fail_on
        self.calls: list[object] = []

    def rebind_session(self, binding: object) -> object:
        self.calls.append(binding)
        if binding is self.fail_on:
            raise RuntimeError("third rebind failed")
        previous = self.binding
        self.binding = binding
        return previous


def test_interactive_session_rebind_rolls_back_earlier_controllers() -> None:
    old_binding = object()
    new_binding = object()
    first = _RebindRecorder(old_binding)
    second = _RebindRecorder(old_binding)
    third = _RebindRecorder(old_binding, fail_on=new_binding)
    controllers = InteractiveControllers(
        command_dispatch=object(),
        view=first,
        model_auth=object(),
        params=second,
        processes=third,
        lsp=object(),
        memory=object(),
        operations=object(),
        subagents=object(),
        sessions=object(),
        extensions=object(),
        turns=object(),
        shutdown=object(),
        motion=object(),
    )

    with pytest.raises(RuntimeError, match="third rebind failed"):
        controllers.rebind_session(new_binding)

    assert first.binding is old_binding
    assert second.binding is old_binding
    assert first.calls == [new_binding, old_binding]
    assert second.calls == [new_binding, old_binding]
