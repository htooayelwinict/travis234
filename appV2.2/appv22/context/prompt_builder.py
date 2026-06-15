from __future__ import annotations

from copy import deepcopy
from typing import Any

from appv22.state.models import AgentState


class PromptBuilder:
    def build(self, state: AgentState, selected_context: dict[str, Any]) -> dict[str, Any]:
        mode = selected_context["selection"]["mode"]
        return {
            "system": {"identity": "AppV2.2 Pi-Hermes extension runtime"},
            "agent": {
                "mode": mode,
                "request": state.request.user_goal,
                "constraints": list(state.request.constraints),
            },
            "state": deepcopy(selected_context["state"]),
            "skills": deepcopy(selected_context["skills"]),
            "tools": list(selected_context["tools"]),
            "world": deepcopy(selected_context["world"]),
            "selection": deepcopy(selected_context["selection"]),
        }
