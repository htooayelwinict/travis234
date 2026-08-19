from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from travis.coding_agent.session_controllers import SessionControllers
from travis.coding_agent.session_state import SessionPresentationState, SessionTurnState
from travis.ai.providers.faux import faux_model
from travis.coding_agent.agent_session import AgentSession


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
    assert runtime.controllers.events is runtime
    assert runtime.controllers.turns is runtime
