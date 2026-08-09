"""Provider assistant-stream sequencing guards."""

from __future__ import annotations


_UPDATE_EVENTS = {
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "toolcall_start",
    "toolcall_delta",
    "toolcall_end",
}


class ProviderStreamProtocolError(RuntimeError):
    """Raised when a provider emits a context-corrupting event sequence."""


class AssistantStreamProtocol:
    """Validates ordering while retaining terminal-only provider compatibility."""

    def __init__(self) -> None:
        self.started = False
        self.terminal = False

    def accept(self, event_type: str) -> None:
        if self.terminal:
            raise ProviderStreamProtocolError("event emitted after terminal event")
        if event_type == "start":
            if self.started:
                raise ProviderStreamProtocolError("duplicate start event")
            self.started = True
            return
        if event_type in _UPDATE_EVENTS:
            if not self.started:
                raise ProviderStreamProtocolError(f"{event_type} event emitted before start event")
            return
        if event_type in {"done", "error"}:
            self.terminal = True


__all__ = ["AssistantStreamProtocol", "ProviderStreamProtocolError"]
