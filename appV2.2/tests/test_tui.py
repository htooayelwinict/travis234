from __future__ import annotations

from appv22.tui import (
    Container,
    FakeTerminal,
    InteractiveRenderer,
    TUI,
    Text,
    strip_ansi,
    truncate_to_width,
    visible_width,
    wrap_text,
)
from appv22.agent.types import (
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from appv22.agent.types import AgentToolResult
from appv22.ai.types import AssistantMessage, TextContent, empty_usage, now_ms


def test_visible_width_strips_ansi() -> None:
    assert visible_width("\x1b[31mred\x1b[0m") == 3
    assert visible_width("plain") == 5


def test_truncate_to_width_passes_ansi() -> None:
    assert truncate_to_width("hello world", 5) == "hello"
    styled = "\x1b[31mhello world\x1b[0m"
    assert visible_width(truncate_to_width(styled, 5)) == 5


def test_wrap_text_wraps_to_width() -> None:
    assert wrap_text("the quick brown fox", 9) == ["the quick", "brown fox"]
    assert wrap_text("", 10) == [""]
    assert wrap_text("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_text_component_caches_and_wraps() -> None:
    text = Text("a b c d e")
    assert text.render(3) == ["a b", "c d", "e"]
    assert text.render(3) == ["a b", "c d", "e"]  # cached


def test_container_concatenates_children() -> None:
    container = Container([Text("one"), Text("two")])
    assert container.render(10) == ["one", "two"]


def test_tui_full_then_diff_single_line() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    line1 = Text("first")
    line2 = Text("second")
    tui.add(line1)
    tui.add(line2)
    info = tui.request_render()
    assert info.full is True
    assert info.lines == ["first", "second"]

    line2.set_text("changed")
    info2 = tui.request_render()
    assert info2.full is False
    assert info2.first_changed == 1
    assert info2.last_changed == 1
    # only the changed line was rewritten
    assert "changed" in terminal.writes[-1]
    assert "first" not in terminal.writes[-1]


def test_tui_no_change_yields_empty_diff() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    tui.add(Text("static"))
    tui.request_render()
    info = tui.request_render()
    assert info.first_changed == -1


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)], api="faux", provider="faux", model="m",
        usage=empty_usage(), stop_reason="stop", timestamp=now_ms(),
    )


def test_interactive_renderer_assistant_and_tool() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    renderer = InteractiveRenderer(tui)

    msg = _assistant("")
    renderer.handle_event(MessageStartEvent(message=msg))
    streamed = _assistant("Hello")
    renderer.handle_event(MessageUpdateEvent(message=streamed, assistant_message_event=None))
    renderer.handle_event(MessageEndEvent(message=streamed))

    renderer.handle_event(ToolExecutionStartEvent(tool_call_id="c1", tool_name="read", args={"path": "a.txt"}))
    result = AgentToolResult(content=[TextContent(text="file body")], details={})
    renderer.handle_event(ToolExecutionEndEvent(tool_call_id="c1", tool_name="read", result=result, is_error=False))

    lines = tui.render(80)
    assert "Hello" in "\n".join(lines)
    assert any("read" in line for line in lines)
    assert any("file body" in line for line in lines)


def test_strip_ansi_helper() -> None:
    assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"
