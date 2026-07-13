from __future__ import annotations

from travis.ai.providers.faux import faux_model
from travis.coding_agent.agent_session import AgentSession


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

    assert isinstance(runtime, SessionModelController)
    assert isinstance(runtime, SessionBashController)
    assert isinstance(runtime, SessionToolController)
    assert isinstance(runtime, SessionPersistence)
    assert isinstance(runtime, SessionExtensionController)
    assert isinstance(runtime, SessionSubagentController)
    assert isinstance(runtime, SessionSubagentTraceController)
    assert isinstance(runtime, SessionTurnController)
    assert isinstance(runtime, SessionPolicyController)
    assert isinstance(runtime, SessionEventController)


def test_agent_session_forwards_runtime_overrides(tmp_path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    session._max_retries = 7
    session.compact = lambda: "replacement"

    assert session._runtime._max_retries == 7
    assert session.compact() == "replacement"
