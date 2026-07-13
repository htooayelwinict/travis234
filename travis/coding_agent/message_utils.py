"""Canonical message inspection and rendering shared by session workflows."""

from __future__ import annotations

from collections.abc import Iterable

from travis.agent.types import AgentMessage
from travis.ai.types import AssistantMessage, ImageContent, TextContent


def last_assistant_message(messages: Iterable[AgentMessage]) -> AssistantMessage | None:
    for message in reversed(list(messages)):
        if isinstance(message, AssistantMessage):
            return message
    return None


def user_message_text(content: str | list[TextContent | ImageContent]) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))


def bash_execution_text(message: object) -> str:
    text = f"Ran `{getattr(message, 'command', '')}`\n"
    output = getattr(message, "output", "")
    text += f"```\n{output}\n```" if output else "(no output)"
    if getattr(message, "cancelled", False):
        text += "\n\n(command cancelled)"
    else:
        exit_code = getattr(message, "exit_code", None)
        if exit_code not in (None, 0):
            text += f"\n\nCommand exited with code {exit_code}"
    if getattr(message, "truncated", False) and getattr(message, "full_output_path", None):
        text += f"\n\n[Output truncated. Full output: {message.full_output_path}]"
    return text
