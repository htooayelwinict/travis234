"""Coding-profile response requested when the generic loop reaches its budget."""

from travis.agent.types import IterationLimitContext
from travis.ai.types import UserMessage, now_ms


def coding_iteration_limit_message(context: IterationLimitContext) -> UserMessage:
    return UserMessage(
        content=(
            "You've reached the maximum number of tool-calling iterations allowed. "
            "Please provide a final response summarizing what you've found and accomplished so far, "
            "without calling any more tools."
        ),
        timestamp=now_ms(),
    )


__all__ = ["coding_iteration_limit_message"]
