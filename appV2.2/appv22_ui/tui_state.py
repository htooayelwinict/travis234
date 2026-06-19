from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from appv22_ui.events import UIEvent, event_from_runtime_event, result_summary


@dataclass
class ConversationLine:
    role: str
    text: str


@dataclass
class TuiState:
    workspace: Path
    session_id: str = ""
    status: str = "empty"
    reason: str = "no persisted session"
    running: bool = False
    mode: str = "IDLE"
    world_ref_count: int = 0
    world_refs: list[str] = field(default_factory=list)
    context_summary: dict[str, Any] = field(default_factory=dict)
    conversation_summary: str = ""
    ui_context_metrics: dict[str, Any] = field(default_factory=dict)
    conversation: list[ConversationLine] = field(default_factory=list)
    events: list[UIEvent] = field(default_factory=list)
    notice: str = "Type a message. Commands: /status /context /refs /events /clear /exit"

    @classmethod
    def from_session(cls, workspace: Path, session: dict[str, Any] | None) -> "TuiState":
        state = cls(workspace=workspace)
        if session:
            conversation = session.get("conversation")
            ui_context = session.get("ui_context")
            if isinstance(ui_context, dict):
                summary = ui_context.get("conversation_summary")
                if isinstance(summary, str):
                    state.conversation_summary = summary
                metrics = ui_context.get("metrics")
                if isinstance(metrics, dict):
                    state.ui_context_metrics = dict(metrics)
            if isinstance(conversation, list):
                for item in conversation[-40:]:
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("role"), str)
                        and isinstance(item.get("text"), str)
                        and not _looks_like_pasted_tui_output(item["text"])
                    ):
                        state.conversation.append(ConversationLine(item["role"], item["text"]))
            result = session.get("last_result") if isinstance(session.get("last_result"), dict) else session
            state.apply_result(result)
            state.load_result_events(result)
        return state

    def add_user(self, text: str) -> None:
        self.conversation.append(ConversationLine("user", text))

    def add_notice(self, text: str) -> None:
        self.notice = text

    def clear_transient(self) -> None:
        self.events.clear()
        self.notice = "Screen cleared. Session context is still preserved."

    def apply_event(self, event: dict[str, Any]) -> None:
        ui_event = event_from_runtime_event(event)
        self.events.append(ui_event)
        self.events = self.events[-80:]
        if ui_event.kind == "ModeChanged":
            mode = ui_event.payload.get("mode")
            if isinstance(mode, str):
                self.mode = mode
        elif ui_event.kind == "RunCompleted":
            self.running = False
            self.mode = "FINALIZE"
        elif ui_event.kind == "RunFailed":
            self.running = False
            self.mode = "FAILED"

    def apply_result(self, result: dict[str, Any] | None) -> None:
        summary = result_summary(result)
        self.session_id = str(summary.get("session_id") or self.session_id)
        self.status = str(summary.get("status") or "unknown")
        self.reason = str(summary.get("reason") or "")
        self.world_ref_count = int(summary.get("world_ref_count") or 0)
        refs = summary.get("world_refs")
        self.world_refs = list(refs) if isinstance(refs, list) else []
        context = summary.get("context_summary")
        self.context_summary = dict(context) if isinstance(context, dict) else {}
        if isinstance(result, dict):
            message = result.get("assistant_message")
            if isinstance(message, str) and message.strip():
                stripped = message.strip()
                if not self.conversation or self.conversation[-1] != ConversationLine("assistant", stripped):
                    self.conversation.append(ConversationLine("assistant", stripped))
        self.status = self.status or "completed"
        self.running = False

    def load_result_events(self, result: dict[str, Any] | None) -> None:
        if not isinstance(result, dict):
            return
        events = result.get("events")
        if not isinstance(events, list):
            return
        for event in events[-80:]:
            if isinstance(event, dict):
                self.apply_event(event)


def _looks_like_pasted_tui_output(text: str) -> bool:
    markers = (
        "appv22  ",
        "context refs ",
        "[compaction]",
        "tool_loop_completed",
        "decision proposed:",
        "agent started ::",
    )
    marker_hits = sum(1 for marker in markers if marker in text)
    return marker_hits >= 2 or (text.startswith("|") and marker_hits >= 1)
