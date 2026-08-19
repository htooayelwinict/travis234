from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from travis.coding_agent.session_controllers import SessionControllers
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


def test_declared_session_state_records_are_real_controller_dependencies(tmp_path) -> None:
    runtime = AgentSession(cwd=str(tmp_path), model=faux_model())._runtime

    assert isinstance(runtime.turn_state, SessionTurnState)
    assert isinstance(runtime.presentation_state, SessionPresentationState)
    assert runtime.controllers.turns.dependencies.turn_state is runtime.turn_state
    assert runtime.controllers.models.dependencies.presentation_state is runtime.presentation_state
