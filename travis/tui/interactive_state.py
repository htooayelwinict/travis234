"""Cohesive mutable state owned by interactive collaborators."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InteractiveState:
    editor_text: str = ""
    prompt_history: list[str] = field(default_factory=list)
    status_message: str = "Idle"
    selection: object | None = None
    generation_params: object | None = None
    active_editor: object | None = None


@dataclass(slots=True)
class InteractiveLifecycleState:
    shutdown_requested: bool = False
    run_loop_active: bool = False
    active_worker: object | None = None
    active_worker_thread: object | None = None
    queued_after_turn: list[str] = field(default_factory=list)
    agent_abort_requested: bool = False


__all__ = ["InteractiveLifecycleState", "InteractiveState"]
