from __future__ import annotations

from travis.ai.providers.faux import faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.session_contracts import AGENT_SESSION_PUBLIC_MEMBERS


def test_agent_session_composes_bounded_runtime_owners(tmp_path) -> None:
    from travis.coding_agent.session_bash import SessionBashController
    from travis.coding_agent.session_events import SessionEventController
    from travis.coding_agent.session_extensions import SessionExtensionController
    from travis.coding_agent.session_models import SessionModelController
    from travis.coding_agent.session_persistence import SessionPersistence
    from travis.coding_agent.session_policy_controller import SessionPolicyController
    from travis.coding_agent.session_subagents import SessionSubagentController
    from travis.coding_agent.session_tooling import SessionToolController
    from travis.coding_agent.session_turns import SessionTurnController
    from travis.coding_agent.subagent_trace import SessionSubagentTraceController

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    runtime = session._runtime

    assert isinstance(runtime.controllers.models, SessionModelController)
    assert isinstance(runtime.controllers.bash, SessionBashController)
    assert isinstance(runtime.controllers.tools, SessionToolController)
    assert isinstance(runtime.controllers.persistence, SessionPersistence)
    assert isinstance(runtime.controllers.extensions, SessionExtensionController)
    assert isinstance(runtime.controllers.subagents, SessionSubagentController)
    assert isinstance(runtime.controllers.subagent_trace, SessionSubagentTraceController)
    assert isinstance(runtime.controllers.turns, SessionTurnController)
    assert isinstance(runtime.controllers.policy, SessionPolicyController)
    assert isinstance(runtime.controllers.events, SessionEventController)


def test_agent_session_forwards_runtime_overrides(tmp_path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    session._max_retries = 7
    session.compact = lambda: "replacement"

    assert session._runtime._max_retries == 7
    assert session.compact() == "replacement"


def test_agent_session_declares_lifecycle_contract_explicitly() -> None:
    assert "dispose" in AgentSession.__dict__
    assert "shutdown" in AgentSession.__dict__
    assert {"dispose", "shutdown"} <= AGENT_SESSION_PUBLIC_MEMBERS
