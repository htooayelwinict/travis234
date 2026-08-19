from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from typing import get_type_hints

import pytest

from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.controller_ports import ControllerBinding
from travis.runtime_facade import RuntimeFacade
from travis.tui.interactive_controllers import (
    INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES,
    InteractiveControllers,
)
from travis.tui.interactive_mode import InteractiveMode, _InteractiveRuntime
from travis.tui.interactive_rebind import InteractiveProcessSession
from travis.tui.interactive_services import InteractiveServices
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState
from travis.tui.user_commands import (
    UserCommandBinding,
    UserCommandExtensionPort,
    UserCommandSessionPort,
)

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
        dependencies = getattr(runtime.controllers, name).dependencies
        binding_names = {
            field.name
            for field in fields(dependencies)
            if isinstance(getattr(dependencies, field.name), ControllerBinding)
        }
        assert binding_names == set(INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES[name])


def test_interactive_controllers_receive_distinct_explicit_dependency_records(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    dependencies = [
        getattr(runtime.controllers, name).dependencies
        for name in INTERACTIVE_CONTROLLER_NAMES
    ]

    assert len({type(record) for record in dependencies}) == len(dependencies)
    assert all(is_dataclass(record) for record in dependencies)
    assert all(not hasattr(record, "port") for record in dependencies)
    assert all(not hasattr(controller, "__dict__") for controller in (
        getattr(runtime.controllers, name) for name in INTERACTIVE_CONTROLLER_NAMES
    ))


def test_interactive_controllers_receive_an_app_port_not_the_complete_app(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime

    for name in INTERACTIVE_CONTROLLER_NAMES:
        controller = getattr(runtime.controllers, name)
        if "app" in INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES[name]:
            assert controller.app is not app
            assert type(controller.app).__name__ == "InteractiveAppAdapter"
    assert runtime.services.sessions is not app
    assert type(runtime.services.sessions).__name__ == "InteractiveAppAdapter"


def test_user_command_binding_declares_the_narrow_rebindable_session_port() -> None:
    assert get_type_hints(UserCommandBinding)["session"] is UserCommandSessionPort
    getter = InteractiveProcessSession.extension_runner.fget
    assert getter is not None
    assert get_type_hints(getter)["return"] is UserCommandExtensionPort


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


def test_interactive_session_rebind_changes_services_consumed_by_real_controllers(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    new_session = AgentSession(cwd=str(tmp_path / "new"), model=faux_model())
    new_overrides = new_session.set_generation_param_override("temperature", 0.7)

    runtime.controllers.rebind_session(new_session)

    assert not isinstance(runtime.controllers.view.session, RuntimeFacade)
    assert not isinstance(runtime.controllers.params.session, RuntimeFacade)
    assert not isinstance(runtime.controllers.processes.session, RuntimeFacade)
    assert runtime.controllers.view.session.model is new_session.model
    assert runtime.controllers.processes.session.session_id == new_session.session_id
    assert runtime.controllers.params._session_generation_param_overrides() is new_overrides


def test_interactive_session_rebind_rolls_back_real_controller_bindings(
    tmp_path,
    monkeypatch,
) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    old_session = app.session
    new_session = AgentSession(cwd=str(tmp_path / "new"), model=faux_model())

    def fail_third_rebind(_controller, _session):
        raise RuntimeError("third rebind failed")

    monkeypatch.setattr(
        type(runtime.controllers.processes),
        "rebind_session",
        fail_third_rebind,
    )

    with pytest.raises(RuntimeError, match="third rebind failed"):
        runtime.controllers.rebind_session(new_session)

    assert runtime.controllers.view.session.model is old_session.model
    assert runtime.controllers.params.session.model is old_session.model
    assert not isinstance(runtime.controllers.view.session, RuntimeFacade)
    assert not isinstance(runtime.controllers.params.session, RuntimeFacade)
    assert runtime.controllers.params._session_generation_param_overrides() == (
        old_session.generation_param_overrides
    )
