"""Interactive components mapping AgentEvent to TUI components.

Port of pi/packages/coding-agent/src/modes/interactive components (subset):
AssistantMessageComponent + ToolExecutionComponent + an event->component bridge.
"""

from __future__ import annotations

from typing import Any

from appv22.ai.types import TextContent, ToolCall
from appv22.tui.component import Text
from appv22.tui.tui import TUI


class AssistantMessageComponent(Text):
    def update_content(self, message: Any) -> None:
        parts: list[str] = []
        for block in getattr(message, "content", []) or []:
            if isinstance(block, TextContent):
                parts.append(block.text)
            elif isinstance(block, ToolCall):
                parts.append(f"-> {block.name}({_short_args(block.arguments)})")
        self.set_text("".join(parts) or "")


class ToolExecutionComponent(Text):
    def __init__(self, tool_name: str, args: Any) -> None:
        super().__init__(f"$ {tool_name} {_short_args(args)}")
        self.tool_name = tool_name

    def update_result(self, result: Any, is_error: bool) -> None:
        text = ""
        content = getattr(result, "content", None)
        if content:
            text = "".join(b.text for b in content if isinstance(b, TextContent))
        prefix = "x" if is_error else "ok"
        head = self._text.split("\n", 1)[0]
        self.set_text(f"{head}\n  [{prefix}] {text}".rstrip())


def _short_args(args: Any) -> str:
    rendered = str(args)
    return rendered if len(rendered) <= 60 else rendered[:57] + "..."


class InteractiveRenderer:
    """Reduces AgentEvent into TUI components (pi interactive-mode handle_event)."""

    def __init__(self, tui: TUI) -> None:
        self.tui = tui
        self._current_assistant: AssistantMessageComponent | None = None
        self._tool_components: dict[str, ToolExecutionComponent] = {}

    def handle_event(self, event: Any) -> None:
        etype = event.type
        if etype == "message_start" and getattr(event.message, "role", None) == "assistant":
            self._current_assistant = AssistantMessageComponent("")
            self.tui.add(self._current_assistant)
        elif etype == "message_update" and self._current_assistant is not None:
            self._current_assistant.update_content(event.message)
        elif etype == "message_end" and getattr(event.message, "role", None) == "assistant":
            if self._current_assistant is not None:
                self._current_assistant.update_content(event.message)
            self._current_assistant = None
        elif etype == "tool_execution_start":
            component = ToolExecutionComponent(event.tool_name, event.args)
            self._tool_components[event.tool_call_id] = component
            self.tui.add(component)
        elif etype == "tool_execution_end":
            component = self._tool_components.get(event.tool_call_id)
            if component is not None:
                component.update_result(event.result, event.is_error)
        self.tui.request_render()
