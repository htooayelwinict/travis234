from __future__ import annotations

from copy import deepcopy
from typing import Any

from appv22.prompts.agent_contract import build_system_contract, mode_contract
from appv22.state.models import AgentState


class PromptBuilder:
    def build(self, state: AgentState, selected_context: dict[str, Any]) -> dict[str, Any]:
        mode = selected_context["selection"]["mode"]
        return {
            "system": build_system_contract(),
            "agent": {
                "mode": mode,
                "request": state.request.active_user_request or state.request.user_goal,
                "reference_request_context": state.request.user_goal,
                "ui_context": deepcopy(state.request.ui_context),
                "constraints": list(state.request.constraints),
                "mode_contract": mode_contract(mode),
            },
            "state": deepcopy(selected_context["state"]),
            "skills": deepcopy(selected_context["skills"]),
            "tools": list(selected_context["tools"]),
            "world": deepcopy(selected_context["world"]),
            "selection": deepcopy(selected_context["selection"]),
        }
