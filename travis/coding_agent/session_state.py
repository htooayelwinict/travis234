"""Cohesive mutable state owned by coding-session collaborators."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SessionTurnState:
    active_prompt: str | None = None
    cancel_requested: bool = False
    retry_attempt: int = 0
    retry_enabled: bool = True
    pending_next_turn_messages: list[object] = field(default_factory=list)
    steering_mode: str = "one-at-a-time"
    follow_up_mode: str = "one-at-a-time"


@dataclass(slots=True)
class SessionPresentationState:
    session_name: str | None = None
    thinking_level: str = "off"
    generation_overrides: object | None = None
    last_status: str = "idle"


__all__ = ["SessionPresentationState", "SessionTurnState"]
