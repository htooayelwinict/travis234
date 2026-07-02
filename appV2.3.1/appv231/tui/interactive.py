"""Interactive components mapping AgentEvent to TUI components.

Port of pi/packages/coding-agent/src/modes/interactive components (subset):
AssistantMessageComponent + ToolExecutionComponent + an event->component bridge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from appv231.agent.types import AgentToolResult
from appv231.ai.types import ImageContent, TextContent, ThinkingContent, ToolCall
from appv231.tui.component import Box, Component, Container, Markdown, Spacer, Text
from appv231.tui.tui import TUI
from appv231.tui.utils import truncate_to_width, visible_width

OSC133_ZONE_START = "\x1b]133;A\x07"
OSC133_ZONE_END = "\x1b]133;B\x07"
OSC133_ZONE_FINAL = "\x1b]133;C\x07"
_SKILL_BLOCK_RE = re.compile(
    r'^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n</skill>(?:\n\n([\s\S]+))?$'
)


@dataclass(frozen=True)
class ParsedSkillBlock:
    name: str
    location: str
    content: str
    user_message: str | None = None

    @property
    def userMessage(self) -> str | None:
        return self.user_message


def parse_skill_block(text: str) -> ParsedSkillBlock | None:
    match = _SKILL_BLOCK_RE.match(text)
    if not match:
        return None
    user_message = (match.group(4) or "").strip() or None
    return ParsedSkillBlock(
        name=match.group(1),
        location=match.group(2),
        content=match.group(3),
        user_message=user_message,
    )


parseSkillBlock = parse_skill_block


class AssistantMessageComponent(Container):
    def __init__(
        self,
        message: Any | None = None,
        *,
        hide_thinking_block: bool = False,
        hidden_thinking_label: str = "Thinking...",
    ) -> None:
        super().__init__()
        self._message = None
        self.hide_thinking_block = hide_thinking_block
        self.hidden_thinking_label = hidden_thinking_label
        if isinstance(message, str) or message is None:
            self.add(Text(message or ""))
        else:
            self.update_content(message)

    def set_hide_thinking_block(self, hidden: bool) -> None:
        self.hide_thinking_block = bool(hidden)
        if self._message is not None:
            self.update_content(self._message)

    setHideThinkingBlock = set_hide_thinking_block

    def set_hidden_thinking_label(self, label: str) -> None:
        self.hidden_thinking_label = str(label)
        if self._message is not None:
            self.update_content(self._message)

    setHiddenThinkingLabel = set_hidden_thinking_label

    def update_content(self, message: Any) -> None:
        self._message = message
        self.clear()
        for block in getattr(message, "content", []) or []:
            if isinstance(block, TextContent):
                if block.text.strip():
                    self.add(Markdown(block.text.strip()))
            elif isinstance(block, ThinkingContent):
                if block.thinking.strip():
                    if self.hide_thinking_block:
                        self.add(Text(self.hidden_thinking_label))
                    else:
                        self.add(Markdown(f"Thinking:\n{block.thinking.strip()}"))
        if getattr(message, "stop_reason", None) == "error":
            self.add(Text(f"Error: {getattr(message, 'error_message', None) or 'Unknown error'}"))
        elif getattr(message, "stop_reason", None) == "aborted":
            self.add(Text(getattr(message, "error_message", None) or "Operation aborted"))
        if not self.children:
            self.add(Text(""))


class ToolExecutionComponent(Container):
    def __init__(
        self,
        tool_name: str,
        tool_call_id_or_args: Any = "",
        args: Any | None = None,
        *,
        tool_definition=None,
        cwd: str = "",
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        if args is None:
            self.tool_call_id = ""
            self.args = tool_call_id_or_args
        else:
            self.tool_call_id = str(tool_call_id_or_args)
            self.args = args
        self.tool_definition = tool_definition
        self.cwd = cwd
        self.expanded = False
        self.result = None
        self.is_error = False

    def update_result(self, result: Any, is_error: bool) -> None:
        self.result = result
        self.is_error = is_error

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded

    def render(self, width: int) -> list[str]:
        lines = [_render_tool_call_header(self._render_call(), width)]
        result_value = self._render_result()
        if _has_tool_ui_value(result_value):
            result_width = max(1, width - 2)
            for line in _render_tool_ui_value(result_value, result_width, markdown=True):
                lines.append(f"  {line}")
        return [truncate_to_width(line, width) for line in lines]

    def _render_call(self) -> Any:
        if self.tool_definition and self.tool_definition.render_call:
            try:
                return self.tool_definition.render_call(
                    self.args,
                    {"cwd": self.cwd, "expanded": self.expanded, "tool_call_id": self.tool_call_id},
                )
            except Exception:  # noqa: BLE001 - rendering must not crash tool execution
                return f"$ {self.tool_name} {_short_args(self.args)}"
        return f"$ {self.tool_name} {_short_args(self.args)}"

    def _render_result(self) -> Any:
        if self.result is None:
            return ""
        if self.tool_definition and self.tool_definition.render_result:
            try:
                return self.tool_definition.render_result(
                    self.result,
                    {"expanded": self.expanded},
                    {"cwd": self.cwd, "is_error": self.is_error, "args": self.args},
                )
            except Exception:  # noqa: BLE001 - rendering must not crash tool execution
                pass
        result = self.result
        content = getattr(result, "content", None)
        if isinstance(result, AgentToolResult) and content:
            text = "\n".join(_render_result_block(block) for block in content)
        else:
            text = str(result)
        if not self.expanded and not self.is_error:
            text = _collapse_result_text(text)
        prefix = "x" if self.is_error else "ok"
        return f"[{prefix}] {text}".rstrip()


def _has_tool_ui_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return True


def _render_tool_call_header(value: Any, width: int) -> str:
    width = max(1, int(width))
    text = _tool_call_header_text(value, width)
    return _truncate_tool_call_header(text, width)


def _tool_call_header_text(value: Any, width: int) -> str:
    if isinstance(value, Text):
        text = value.text
    elif isinstance(value, Component):
        rendered = value.render(max(width, 160))
        text = " ".join(line.strip() for line in rendered if line.strip())
    else:
        text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def _truncate_tool_call_header(text: str, width: int) -> str:
    if visible_width(text) <= width:
        return text
    expand_hint = " (to expand)"
    if text.endswith(expand_hint) and visible_width(expand_hint) < width:
        prefix = text[: -len(expand_hint)].rstrip()
        compact_hint = " to expand"
        hint = compact_hint if visible_width(prefix) + visible_width(compact_hint) <= width else expand_hint
        prefix_width = width - visible_width(hint)
        return truncate_to_width(prefix, prefix_width, "...") + hint
    command, separator, detail = text.partition(" ")
    if separator and "/" in detail:
        detail_width = width - visible_width(command) - 1
        if detail_width >= 8:
            detail = _truncate_path_suffix(detail, detail_width)
            candidate = f"{command} {detail}"
            if visible_width(candidate) <= width:
                return candidate
    return truncate_to_width(text, width, "...")


def _truncate_path_suffix(path: str, width: int) -> str:
    if visible_width(path) <= width:
        return path
    suffix = path.rstrip("/").rsplit("/", 1)[-1] or path
    marker = ".../"
    marker_width = visible_width(marker)
    if width <= marker_width:
        return truncate_to_width(path, width, "...")
    suffix_width = width - marker_width
    if visible_width(suffix) > suffix_width:
        suffix = _right_visible_slice(suffix, suffix_width)
    return marker + suffix


def _right_visible_slice(text: str, width: int) -> str:
    width = max(1, int(width))
    result = ""
    used = 0
    for char in reversed(text):
        char_width = visible_width(char)
        if used + char_width > width:
            break
        result = char + result
        used += char_width
    return result


def _render_tool_ui_value(value: Any, width: int, *, markdown: bool) -> list[str]:
    if isinstance(value, Component):
        return value.render(width)
    text = "" if value is None else str(value)
    if text == "":
        return []
    if markdown:
        lines: list[str] = []
        for raw in text.split("\n"):
            lines.extend(Markdown(raw).render(width))
        return lines
    return Text(text).render(width)


class UserMessageComponent(Container):
    """Pi-style user message renderer with OSC 133 prompt zones."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
        self.add(Box(Markdown(text)))

    def render(self, width: int) -> list[str]:
        lines = super().render(width)
        if not lines:
            return lines
        lines[0] = OSC133_ZONE_START + lines[0]
        lines[-1] = OSC133_ZONE_END + OSC133_ZONE_FINAL + lines[-1]
        return lines


class SkillInvocationMessageComponent(Container):
    """Pi-style collapsed/expanded skill invocation renderer."""

    def __init__(self, skill_block: ParsedSkillBlock) -> None:
        super().__init__()
        self.skill_block = skill_block
        self.expanded = False
        self._rebuild()

    def set_expanded(self, expanded: bool) -> None:
        if self.expanded != expanded:
            self.expanded = expanded
            self._rebuild()

    def invalidate(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        body = Container()
        if self.expanded:
            body.add(Text("[skill]"))
            body.add(Markdown(f"**{self.skill_block.name}**\n\n{self.skill_block.content}"))
        else:
            body.add(Text(f"[skill] {self.skill_block.name} (expand to view)"))
        self.clear()
        self.add(Box(body))


class BashExecutionComponent(Container):
    """Pi-style bash execution renderer for ! and !! commands."""

    PREVIEW_LINES = 20

    def __init__(self, command: str, exclude_from_context: bool = False) -> None:
        super().__init__()
        self.command = command
        self.exclude_from_context = exclude_from_context
        self.output_lines: list[str] = []
        self.status = "running"
        self.exit_code: int | None = None
        self.cancelled = False
        self.truncated = False
        self.full_output_path: str | None = None
        self.expanded = False

    def append_output(self, chunk: str) -> None:
        clean = _strip_basic_ansi(chunk).replace("\r\n", "\n").replace("\r", "\n")
        new_lines = clean.split("\n")
        if self.output_lines and new_lines:
            self.output_lines[-1] += new_lines[0]
            self.output_lines.extend(new_lines[1:])
        else:
            self.output_lines.extend(new_lines)

    appendOutput = append_output

    def set_complete(
        self,
        exit_code: int | None,
        cancelled: bool,
        truncated: bool | None = None,
        full_output_path: str | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.cancelled = cancelled
        self.truncated = bool(truncated)
        self.full_output_path = full_output_path
        if cancelled:
            self.status = "cancelled"
        elif exit_code not in (None, 0):
            self.status = "error"
        else:
            self.status = "complete"

    setComplete = set_complete

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded

    def render(self, width: int) -> list[str]:
        body = Container()
        suffix = " [no context]" if self.exclude_from_context else ""
        body.add(Text(f"$ {self.command}{suffix}"))
        output_lines = self.output_lines
        hidden = 0
        if output_lines:
            visible_lines = output_lines if self.expanded else output_lines[-self.PREVIEW_LINES :]
            hidden = max(0, len(output_lines) - len(visible_lines))
            body.add(Text("\n" + "\n".join(visible_lines)))
        status_lines: list[str] = []
        if self.status == "running":
            status_lines.append("Running...")
        elif hidden > 0:
            status_lines.append(f"... {hidden} more lines (expand to view)")
        if self.status == "cancelled":
            status_lines.append("(cancelled)")
        elif self.status == "error":
            status_lines.append(f"(exit {self.exit_code})")
        if self.truncated and self.full_output_path:
            status_lines.append(f"Output truncated. Full output: {self.full_output_path}")
        if status_lines:
            body.add(Text("\n".join(status_lines)))
        return Box(body).render(width)

    def get_output(self) -> str:
        return "\n".join(self.output_lines)

    getOutput = get_output

    def get_command(self) -> str:
        return self.command

    getCommand = get_command


class BranchSummaryMessageComponent(Container):
    """Pi-style collapsed/expanded branch summary renderer."""

    def __init__(self, message: Any) -> None:
        super().__init__()
        self.message = message
        self.expanded = False
        self._rebuild()

    def set_expanded(self, expanded: bool) -> None:
        if self.expanded != expanded:
            self.expanded = expanded
            self._rebuild()

    def invalidate(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        body = Container()
        body.add(Text("[branch]"))
        body.add(Spacer(1))
        if self.expanded:
            body.add(Markdown(f"**Branch Summary**\n\n{getattr(self.message, 'summary', '')}"))
        else:
            body.add(Text("Branch summary (expand to view)"))
        self.clear()
        self.add(Box(body))


class CompactionSummaryMessageComponent(Container):
    """Pi-style collapsed/expanded compaction summary renderer."""

    def __init__(self, message: Any) -> None:
        super().__init__()
        self.message = message
        self.expanded = False
        self._rebuild()

    def set_expanded(self, expanded: bool) -> None:
        if self.expanded != expanded:
            self.expanded = expanded
            self._rebuild()

    def invalidate(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        tokens_before = _message_attr(self.message, "tokensBefore", "tokens_before", default=0)
        token_str = f"{_safe_int(tokens_before):,}"
        body = Container()
        body.add(Text("[compaction]"))
        body.add(Spacer(1))
        if self.expanded:
            body.add(Markdown(f"**Compacted from {token_str} tokens**\n\n{getattr(self.message, 'summary', '')}"))
        else:
            body.add(Text(f"Compacted from {token_str} tokens (expand to view)"))
        self.clear()
        self.add(Box(body))


class CustomMessageComponent(Container):
    """Pi-style renderer for extension-injected custom messages."""

    def __init__(self, message: Any, custom_renderer=None) -> None:
        super().__init__()
        self.message = message
        self.custom_renderer = custom_renderer
        self.expanded = False
        self._rebuild()

    def set_expanded(self, expanded: bool) -> None:
        if self.expanded != expanded:
            self.expanded = expanded
            self._rebuild()

    def invalidate(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear()
        if not bool(getattr(self.message, "display", True)):
            return
        if self.custom_renderer:
            try:
                rendered = self.custom_renderer(self.message, {"expanded": self.expanded})
                if rendered is not None:
                    self.add(rendered)
                    return
            except Exception:  # noqa: BLE001 - extension message rendering must not crash the TUI
                pass

        body = Container()
        label = getattr(self.message, "customType", getattr(self.message, "custom_type", "custom"))
        body.add(Text(f"[{label}]"))
        body.add(Spacer(1))
        body.add(Markdown(_custom_message_text(self.message)))
        self.add(Box(body))


def message_to_component(
    message: Any,
    custom_renderers: dict[str, Any] | None = None,
    *,
    hide_thinking_block: bool = False,
    hidden_thinking_label: str = "Thinking...",
) -> Component | None:
    """Render an existing Pi coding-agent message into a TUI component."""

    role = getattr(message, "role", None)
    if role == "bashExecution":
        component = BashExecutionComponent(
            getattr(message, "command", ""),
            exclude_from_context=bool(getattr(message, "excludeFromContext", False)),
        )
        output = getattr(message, "output", "")
        if output:
            component.append_output(output)
        component.set_complete(
            getattr(message, "exitCode", None),
            bool(getattr(message, "cancelled", False)),
            bool(getattr(message, "truncated", False)),
            getattr(message, "fullOutputPath", None),
        )
        return _with_leading_spacer(component)
    if role == "branchSummary":
        component = BranchSummaryMessageComponent(message)
        return _with_leading_spacer(component)
    if role == "compactionSummary":
        component = CompactionSummaryMessageComponent(message)
        return _with_leading_spacer(component)
    if role == "custom":
        if not bool(getattr(message, "display", True)):
            return None
        custom_type = getattr(message, "customType", getattr(message, "custom_type", ""))
        component = CustomMessageComponent(message, (custom_renderers or {}).get(custom_type))
        return _with_leading_spacer(component)
    if role == "assistant":
        return AssistantMessageComponent(
            message,
            hide_thinking_block=hide_thinking_block,
            hidden_thinking_label=hidden_thinking_label,
        )
    if role == "user":
        return user_message_to_component(_custom_message_text(message))
    return None


def user_message_to_component(text: str) -> Component:
    skill_block = parse_skill_block(text)
    if skill_block is None:
        return UserMessageComponent(text)

    container = Container()
    container.add(SkillInvocationMessageComponent(skill_block))
    if skill_block.user_message:
        container.add(Spacer(1))
        container.add(UserMessageComponent(skill_block.user_message))
    return container


def _short_args(args: Any) -> str:
    rendered = str(args)
    return rendered if len(rendered) <= 60 else rendered[:57] + "..."


def _render_result_block(block: Any) -> str:
    if isinstance(block, TextContent):
        return block.text
    if isinstance(block, ImageContent):
        return f"[image: {block.mime_type}]"
    return str(block)


def _collapse_result_text(text: str, max_lines: int = 10, max_chars: int = 6_000) -> str:
    original_chars = len(text)
    if original_chars > max_chars:
        text = text[:max_chars]
    lines = text.split("\n")
    truncated_chars = original_chars - len(text)
    if len(lines) <= max_lines:
        if truncated_chars > 0:
            return f"{text}\n... ({truncated_chars} more chars, to expand)"
        return text
    remaining = len(lines) - max_lines
    suffix = f"... ({remaining} more lines, to expand)"
    if truncated_chars > 0:
        suffix = f"... ({remaining} more lines, {truncated_chars} more chars, to expand)"
    return "\n".join([*lines[:max_lines], suffix])


def _custom_message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.text for block in content if isinstance(block, TextContent))
    return str(content)


def _message_attr(message: Any, *names: str, default=None):
    for name in names:
        if hasattr(message, name):
            return getattr(message, name)
    return default


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _with_leading_spacer(component: Component) -> Component:
    wrapped = Container()
    wrapped.add(Spacer(1))
    wrapped.add(component)
    return wrapped


def _strip_basic_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)


class InteractiveRenderer:
    """Reduces AgentEvent into TUI components (pi interactive-mode handle_event)."""

    def __init__(
        self,
        tui: TUI,
        *,
        output_container: Container | None = None,
        tool_definitions: dict[str, Any] | None = None,
        cwd: str = "",
    ) -> None:
        self.tui = tui
        self.output_container: Container = output_container or tui
        self.tool_definitions = tool_definitions or {}
        self.cwd = cwd
        self._current_assistant: AssistantMessageComponent | None = None
        self._tool_components: dict[str, ToolExecutionComponent] = {}
        self.hide_thinking_block = False
        self.hidden_thinking_label = "Thinking..."

    def set_output_container(self, output_container: Container) -> None:
        self.output_container = output_container

    def set_hidden_thinking_label(self, label: str) -> None:
        self.hidden_thinking_label = str(label)
        if self._current_assistant is not None:
            self._current_assistant.set_hidden_thinking_label(self.hidden_thinking_label)

    setHiddenThinkingLabel = set_hidden_thinking_label

    def set_hide_thinking_block(self, hidden: bool) -> None:
        self.hide_thinking_block = bool(hidden)
        if self._current_assistant is not None:
            self._current_assistant.set_hide_thinking_block(self.hide_thinking_block)

    setHideThinkingBlock = set_hide_thinking_block

    def _add(self, component: Component) -> None:
        self.output_container.add(component)

    def handle_event(self, event: Any) -> None:
        if isinstance(event, dict):
            return
        etype = getattr(event, "type", None)
        if etype is None:
            return
        needs_render = False
        if etype == "message_start" and getattr(event.message, "role", None) == "assistant":
            self._current_assistant = AssistantMessageComponent(
                "",
                hide_thinking_block=self.hide_thinking_block,
                hidden_thinking_label=self.hidden_thinking_label,
            )
            self._add(self._current_assistant)
            needs_render = True
        elif etype == "message_update" and self._current_assistant is not None:
            self._current_assistant.update_content(event.message)
            if getattr(event.message, "role", None) == "assistant":
                for block in getattr(event.message, "content", []) or []:
                    if not isinstance(block, ToolCall):
                        continue
                    component = self._tool_components.get(block.id)
                    if component is None:
                        component = ToolExecutionComponent(
                            block.name,
                            block.id,
                            block.arguments,
                            tool_definition=self.tool_definitions.get(block.name),
                            cwd=self.cwd,
                        )
                        self._tool_components[block.id] = component
                        self._add(component)
                    else:
                        component.args = block.arguments
            needs_render = True
        elif etype == "message_end" and getattr(event.message, "role", None) == "assistant":
            if self._current_assistant is not None:
                self._current_assistant.update_content(event.message)
                needs_render = True
            self._current_assistant = None
        elif etype == "tool_execution_start":
            component = self._tool_components.get(event.tool_call_id)
            if component is None:
                component = ToolExecutionComponent(
                    event.tool_name,
                    event.tool_call_id,
                    event.args,
                    tool_definition=self.tool_definitions.get(event.tool_name),
                    cwd=self.cwd,
                )
                self._tool_components[event.tool_call_id] = component
                self._add(component)
            else:
                component.tool_name = event.tool_name
                component.args = event.args
                component.tool_definition = self.tool_definitions.get(event.tool_name)
            needs_render = True
        elif etype == "tool_execution_end":
            component = self._tool_components.get(event.tool_call_id)
            if component is not None:
                component.update_result(event.result, event.is_error)
                needs_render = True
        if needs_render:
            self.tui.request_render()
