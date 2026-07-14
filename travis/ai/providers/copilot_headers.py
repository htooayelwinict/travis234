"""Request-state headers required by GitHub Copilot transports."""

from __future__ import annotations

from collections.abc import Iterable

from travis.ai.types import ImageContent, Message


def infer_copilot_initiator(messages: Iterable[Message]) -> str:
    last = None
    for last in messages:
        pass
    return "agent" if last is not None and last.role != "user" else "user"


def has_copilot_vision_input(messages: Iterable[Message]) -> bool:
    for message in messages:
        if message.role not in {"user", "toolResult"} or not isinstance(message.content, list):
            continue
        if any(isinstance(block, ImageContent) for block in message.content):
            return True
    return False


def build_copilot_dynamic_headers(messages: list[Message]) -> dict[str, str]:
    headers = {
        "X-Initiator": infer_copilot_initiator(messages),
        "Openai-Intent": "conversation-edits",
    }
    if has_copilot_vision_input(messages):
        headers["Copilot-Vision-Request"] = "true"
    return headers


__all__ = [
    "build_copilot_dynamic_headers",
    "has_copilot_vision_input",
    "infer_copilot_initiator",
]
