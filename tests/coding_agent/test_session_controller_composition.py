from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from travis.coding_agent.session_controllers import (
    SESSION_CONTROLLER_PORT_ATTRIBUTES,
    SessionControllers,
)
from travis.coding_agent.session_state import SessionPresentationState, SessionTurnState
from travis.ai.providers.faux import faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.agent_session import _SessionRuntime
from travis.runtime_facade import RuntimeFacade


SESSION_CONTROLLER_NAMES = (
    "events",
    "models",
    "generation",
    "persistence",
    "bash",
    "policy",
    "operations",
    "tools",
    "extensions",
    "subagents",
    "subagent_trace",
    "turns",
)


def test_session_controller_bundle_is_frozen_slotted_and_complete() -> None:
    values = {name: object() for name in SESSION_CONTROLLER_NAMES}
    controllers = SessionControllers(**values)

    assert tuple(field.name for field in fields(controllers)) == SESSION_CONTROLLER_NAMES
    assert not hasattr(controllers, "__dict__")
    with pytest.raises(FrozenInstanceError):
        controllers.events = object()  # type: ignore[misc]


def test_session_state_records_keep_only_cohesive_mutable_state() -> None:
    turns = SessionTurnState()
    presentation = SessionPresentationState()

    turns.retry_attempt = 2
    turns.pending_next_turn_messages.append("next")
    presentation.session_name = "review"

    assert turns.retry_attempt == 2
    assert turns.pending_next_turn_messages == ["next"]
    assert presentation.session_name == "review"
    assert "agent_state" not in {field.name for field in fields(turns)}
    assert "session_entries" not in {field.name for field in fields(presentation)}


def test_session_runtime_owns_the_typed_controller_bundle(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime

    assert isinstance(runtime.controllers, SessionControllers)
    assert type(runtime.controllers.events).__name__ == "SessionEventController"
    assert type(runtime.controllers.turns).__name__ == "SessionTurnController"


def test_low_coupling_session_owners_are_owned_collaborators(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime
    owners = (
        runtime.controllers.events,
        runtime.controllers.models,
        runtime.controllers.generation,
        runtime.controllers.bash,
        runtime.controllers.policy,
        runtime.controllers.operations,
    )

    assert all(owner is not runtime for owner in owners)
    assert all(type(owner) not in type(runtime).__bases__ for owner in owners)


def test_persistence_tool_extension_and_subagent_owners_are_collaborators(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime
    owners = (
        runtime.controllers.persistence,
        runtime.controllers.tools,
        runtime.controllers.extensions,
        runtime.controllers.subagents,
        runtime.controllers.subagent_trace,
    )

    assert all(owner is not runtime for owner in owners)
    assert all(type(owner) not in type(runtime).__bases__ for owner in owners)


def test_session_runtime_is_plain_composition_after_turn_extraction(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime

    assert _SessionRuntime.__bases__ == (object,)
    assert runtime.controllers.turns is not runtime


def test_session_controller_methods_remain_bound_to_the_controller(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime

    assert runtime.controllers.turns.prompt.__self__ is runtime.controllers.turns
    assert runtime.prompt.__self__ is runtime.controllers.turns
    assert runtime.controllers.models.set_model.__self__ is runtime.controllers.models


def test_session_controllers_do_not_retain_a_runtime_or_public_facade(tmp_path) -> None:
    facade = AgentSession(cwd=str(tmp_path), model=faux_model())
    runtime = facade._runtime

    for controller in tuple(getattr(runtime.controllers, name) for name in SESSION_CONTROLLER_NAMES):
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

    for name in SESSION_CONTROLLER_NAMES:
        port = getattr(runtime.controllers, name).dependencies.port
        assert port.declared_names == frozenset(SESSION_CONTROLLER_PORT_ATTRIBUTES[name])


def test_declared_session_state_records_are_real_controller_dependencies(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime

    assert isinstance(runtime.turn_state, SessionTurnState)
    assert isinstance(runtime.presentation_state, SessionPresentationState)
    assert runtime.controllers.turns.dependencies.turn_state is runtime.turn_state
    assert runtime.controllers.models.dependencies.presentation_state is runtime.presentation_state
    runtime._retry_attempt = 3
    runtime._session_name = "contract-first"
    assert runtime.turn_state.retry_attempt == 3
    assert runtime.presentation_state.session_name == "contract-first"


def test_session_controller_ports_declare_cross_domain_runtime_dependencies() -> None:
    """Every runtime value read by an extracted owner belongs to its named port."""

    assert {
        "_language_services",
        "_memory_settings",
        "_memory_tool_runtime",
    } <= set(SESSION_CONTROLLER_PORT_ATTRIBUTES["tools"])
    assert "_model_change_listener" in SESSION_CONTROLLER_PORT_ATTRIBUTES["models"]
    assert (
        "_coordination_runtime_guard_active"
        in SESSION_CONTROLLER_PORT_ATTRIBUTES["policy"]
    )
