from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from appv22_ui.events import UIEvent, event_from_runtime_event


@dataclass
class LiveEventBuffer:
    max_events: int = 30
    events: list[UIEvent] = field(default_factory=list)

    def on_event(self, event: dict[str, Any]) -> str:
        ui_event = event_from_runtime_event(event)
        self.events.append(ui_event)
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events :]
        return self.render()

    def render(self) -> str:
        lines = []
        for index, event in enumerate(self.events, start=1):
            detail = f" :: {event.detail}" if event.detail else ""
            lines.append(f"{index:02d} {event.title}{detail}")
        return _panel("LIVE AGENT LOOP", lines or ["waiting for events"])


def make_printing_event_sink(buffer: LiveEventBuffer):
    def sink(event: dict[str, Any]) -> None:
        print(buffer.on_event(event), flush=True)

    return sink


def _panel(title: str, lines: list[str]) -> str:
    width = max([len(title) + 4, *(len(line) + 4 for line in lines), 48])
    top = "+" + "-" * (width - 2) + "+"
    heading = f"| {title.ljust(width - 4)} |"
    separator = "+" + "-" * (width - 2) + "+"
    body = [f"| {line[: width - 4].ljust(width - 4)} |" for line in lines]
    bottom = "+" + "-" * (width - 2) + "+"
    return "\n".join([top, heading, separator, *body, bottom])
