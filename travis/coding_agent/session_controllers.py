"""Typed ownership bundle for coding-session collaborators."""

from __future__ import annotations

from dataclasses import dataclass

from travis.coding_agent.session_bash import SessionBashController
from travis.coding_agent.session_events import SessionEventController
from travis.coding_agent.session_extensions import SessionExtensionController
from travis.coding_agent.session_generation_params import SessionGenerationParams
from travis.coding_agent.session_models import SessionModelController
from travis.coding_agent.session_operations import SessionOperationController
from travis.coding_agent.session_persistence import SessionPersistence
from travis.coding_agent.session_policy_controller import SessionPolicyController
from travis.coding_agent.session_subagents import SessionSubagentController
from travis.coding_agent.session_tooling import SessionToolController
from travis.coding_agent.session_turns import SessionTurnController
from travis.coding_agent.subagent_trace import SessionSubagentTraceController


@dataclass(frozen=True, slots=True)
class SessionControllers:
    events: SessionEventController
    models: SessionModelController
    generation: SessionGenerationParams
    persistence: SessionPersistence
    bash: SessionBashController
    policy: SessionPolicyController
    operations: SessionOperationController
    tools: SessionToolController
    extensions: SessionExtensionController
    subagents: SessionSubagentController
    subagent_trace: SessionSubagentTraceController
    turns: SessionTurnController


__all__ = ["SessionControllers"]
