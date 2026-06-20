from __future__ import annotations

from appv22.tui import (
    Component,
    Container,
    FooterComponent,
    FakeTerminal,
    Input,
    InteractiveMode,
    InteractiveRenderer,
    Markdown,
    SelectItem,
    SelectList,
    StatusLine,
    TUI,
    Text,
    ToolExecutionComponent,
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
from appv22.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv22.ai.models import get_api_key_for_provider, get_provider_auth_status, reset_models
from appv22.ai.stream import register_api_provider, reset_api_providers
from appv22.ai.types import AssistantMessage, TextContent, ThinkingContent, UserMessage, empty_usage, now_ms
from appv22.app import CodingApp
from appv22.coding_agent import BashResult
from appv22.coding_agent.session_store import BashExecutionMessage, BranchSummaryMessage, CustomMessage
from appv22.coding_agent.tools.bash import BashOperations
from appv22.coding_agent.tools.read import create_read_tool_definition


def setup_function() -> None:
    reset_api_providers()
    reset_models()


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


def test_tui_strips_pi_cursor_marker_and_tracks_cursor_position() -> None:
    terminal = FakeTerminal(columns=40, rows=5)
    tui = TUI(terminal)
    editor = Input(value="hello", prompt="> ")
    editor.cursor = 2
    editor.focused = True
    tui.add(editor)

    info = tui.request_render()

    assert "\x1b_pi:c\x07" not in terminal.output
    assert "\x1b_pi:c\x07" not in "\n".join(info.lines)
    assert [strip_ansi(line).rstrip() for line in info.lines] == ["> hello"]
    assert info.cursor_position == (0, 4)


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


def test_markdown_input_select_and_footer_components() -> None:
    markdown = Markdown("# Title\n\n- one\n**bold** and `code`")
    assert markdown.render(40) == ["Title", "", "- one", "bold and code"]

    submitted: list[str] = []
    input_component = Input(prompt="> ", on_submit=submitted.append)
    input_component.handle_input("hel")
    input_component.handle_input("x")
    input_component.handle_input("\x7f")
    input_component.handle_input("p")
    assert input_component.value == "help"
    assert ">" in input_component.render(20)[0]
    input_component.handle_input("\r")
    assert submitted == ["help"]
    assert input_component.value == ""

    select = SelectList(
        [
            SelectItem(value="alpha", label="Alpha", description="first command"),
            SelectItem(value="beta", label="Beta", description="second command"),
            SelectItem(value="gamma", label="Gamma", description="third command"),
        ],
        max_visible=2,
    )
    select.handle_input("\x1b[B")
    rendered = "\n".join(select.render(40))
    assert "> Beta" in rendered
    assert "(2/3)" in rendered
    select.set_filter("ga")
    assert select.render(40)[0].startswith("> Gamma")
    cancelled: list[bool] = []
    select.on_cancel = lambda: cancelled.append(True)
    select.set_filter("none")
    select.handle_input("\x1b")
    assert cancelled == [True]

    footer = FooterComponent(cwd="/tmp/project", model="faux-model", thinking_level="high", pending=2)
    assert footer.render(80) == ["cwd: /tmp/project | model: faux-model | think: high | pending: 2"]
    footer = FooterComponent(
        cwd="/tmp/project",
        model="faux-model",
        context_tokens=1200,
        context_threshold=16000,
        compression_count=2,
    )
    assert footer.render(120) == [
        "model: faux-model | think: off | ctx: 1,200/16,000 | compactions: 2 | cwd: /tmp/project"
    ]
    status = StatusLine("Retrying\nsoon", kind="info")
    assert status.render(40) == ["info: Retrying soon"]


def test_input_ports_pi_line_movement_and_kill_yank_keybindings() -> None:
    input_component = Input()
    input_component.set_value("foo bar baz")

    input_component.handle_input("\x01")
    assert input_component.cursor == 0

    input_component.handle_input("\x05")
    assert input_component.cursor == len("foo bar baz")

    input_component.handle_input("\x17")
    assert input_component.get_value() == "foo bar "

    input_component.handle_input("\x01")
    input_component.handle_input("\x19")

    assert input_component.get_value() == "bazfoo bar "
    assert input_component.cursor == len("baz")


def test_input_ports_pi_line_kill_and_yank_pop_keybindings() -> None:
    input_component = Input()
    input_component.set_value("hello world")
    input_component.handle_input("\x01")
    for _ in range(6):
        input_component.handle_input("\x1b[C")

    input_component.handle_input("\x15")
    assert input_component.get_value() == "world"

    input_component.handle_input("\x19")
    assert input_component.get_value() == "hello world"

    input_component.set_value("prefix suffix")
    input_component.handle_input("\x01")
    for _ in range(7):
        input_component.handle_input("\x1b[C")

    input_component.handle_input("\x0b")
    assert input_component.get_value() == "prefix "

    input_component.handle_input("\x19")
    assert input_component.get_value() == "prefix suffix"

    input_component.set_value("first")
    input_component.handle_input("\x05")
    input_component.handle_input("\x17")
    input_component.set_value("second")
    input_component.handle_input("\x05")
    input_component.handle_input("\x17")
    input_component.handle_input("\x19")
    assert input_component.get_value() == "second"

    input_component.handle_input("\x1by")
    assert input_component.get_value() == "first"


def test_input_render_scrolls_to_cursor_and_uses_pi_fake_cursor() -> None:
    input_component = Input(value="abcdefghijklmnopqrstuvwxyz", prompt="> ")
    input_component.focused = True
    input_component.cursor = len(input_component.value)

    rendered = input_component.render(12)[0]
    plain = strip_ansi(rendered)

    assert visible_width(rendered) <= 12
    assert "\x1b_pi:c\x07" in rendered
    assert "\x1b[7m \x1b[27m" in rendered
    assert "z" in plain
    assert "abc" not in plain


def test_input_ports_pi_alt_d_delete_word_forward_keybinding() -> None:
    input_component = Input()
    input_component.set_value("hello world")
    input_component.handle_input("\x01")

    input_component.handle_input("\x1bd")
    assert input_component.get_value() == " world"

    input_component.handle_input("\x1bd")
    assert input_component.get_value() == ""

    input_component.handle_input("\x19")
    assert input_component.get_value() == "hello world"


def test_input_ports_pi_bracketed_paste_sanitization() -> None:
    input_component = Input()
    input_component.set_value("prefixsuffix")
    input_component.cursor = len("prefix")

    input_component.handle_input("\x1b[200~one\r\ntwo\tthree\n\x1b[201~")

    assert input_component.get_value() == "prefixonetwo    threesuffix"
    assert input_component.cursor == len("prefixonetwo    three")


def test_input_ports_pi_delete_key_forward_deletion() -> None:
    input_component = Input()
    input_component.set_value("hello")
    input_component.cursor = 1

    input_component.handle_input("\x1b[3~")
    assert input_component.get_value() == "hllo"
    assert input_component.cursor == 1

    input_component.cursor = len(input_component.get_value())
    input_component.handle_input("\x1b[3~")
    assert input_component.get_value() == "hllo"


def test_input_ports_pi_ctrl_d_delete_char_forward_keybinding() -> None:
    input_component = Input()
    input_component.set_value("hello")
    input_component.cursor = 1

    input_component.handle_input("\x04")
    assert input_component.get_value() == "hllo"
    assert input_component.cursor == 1

    input_component.cursor = len(input_component.get_value())
    input_component.handle_input("\x04")
    assert input_component.get_value() == "hllo"


def test_input_ports_pi_ctrl_minus_undo_for_typing_and_delete() -> None:
    input_component = Input()
    for char in "hello world":
        input_component.handle_input(char)

    input_component.handle_input("\x1b[45;5u")
    assert input_component.get_value() == "hello"

    input_component.handle_input("\x1b[45;5u")
    assert input_component.get_value() == ""

    for char in "hello":
        input_component.handle_input(char)
    input_component.handle_input("\x01")
    input_component.handle_input("\x1b[C")
    input_component.handle_input("\x1b[3~")
    assert input_component.get_value() == "hllo"

    input_component.handle_input("\x1b[45;5u")
    assert input_component.get_value() == "hello"
    assert input_component.cursor == 1


def test_input_ports_pi_alt_b_alt_f_word_navigation() -> None:
    input_component = Input()
    input_component.set_value("hello world")

    input_component.handle_input("\x1bb")
    assert input_component.get_value() == "hello world"
    assert input_component.cursor == len("hello ")

    input_component.handle_input("\x1bb")
    assert input_component.cursor == 0

    input_component.handle_input("\x1bf")
    assert input_component.get_value() == "hello world"
    assert input_component.cursor == len("hello")

    input_component.handle_input("\x1bf")
    assert input_component.cursor == len("hello world")


def test_input_ports_pi_alt_backspace_delete_word_backward() -> None:
    input_component = Input()
    input_component.set_value("hello world")

    input_component.handle_input("\x1b\x7f")
    assert input_component.get_value() == "hello "
    assert input_component.cursor == len("hello ")

    input_component.handle_input("\x1b\b")
    assert input_component.get_value() == ""
    assert input_component.cursor == 0

    input_component.handle_input("\x19")
    assert input_component.get_value() == "hello world"


def test_assistant_markdown_thinking_error_and_narrow_wrapping() -> None:
    message = AssistantMessage(
        content=[
            ThinkingContent(thinking="checking **state**"),
            TextContent(text="# Result\n- wrapped text for a narrow terminal"),
        ],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    from appv22.tui import AssistantMessageComponent

    assistant = AssistantMessageComponent(message)
    rendered = assistant.render(18)

    joined = "\n".join(rendered)
    assert "Thinking:" in joined
    assert "checking state" in joined
    assert "Result" in joined
    assert all(visible_width(line) <= 18 for line in rendered)

    error_message = AssistantMessage(
        content=[],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="error",
        error_message="boom",
        timestamp=now_ms(),
    )
    assert "Error: boom" in "\n".join(AssistantMessageComponent(error_message).render(40))


def test_tool_execution_uses_render_hooks_collapsed_expanded_and_narrow_width(tmp_path) -> None:
    definition = create_read_tool_definition(str(tmp_path))
    component = ToolExecutionComponent(
        "read",
        "call-1",
        {"path": str(tmp_path / "attio" / "SKILL.md"), "offset": 12, "limit": 3},
        tool_definition=definition,
        cwd=str(tmp_path),
    )
    collapsed = "\n".join(component.render(30))

    assert "[skill] attio:12-14" in collapsed
    assert "to expand" in collapsed

    result = AgentToolResult(content=[TextContent(text="hidden skill body")], details=None)
    component.update_result(result, is_error=False)
    assert "hidden skill body" not in "\n".join(component.render(30))

    component.set_expanded(True)
    expanded = component.render(30)
    assert "hidden skill body" in "\n".join(expanded)
    assert all(visible_width(line) <= 30 for line in expanded)


def test_read_tool_render_tolerates_unvalidated_model_numeric_strings(tmp_path) -> None:
    definition = create_read_tool_definition(str(tmp_path))
    component = ToolExecutionComponent(
        "read",
        "call-1",
        {"path": "src/agents/facebook_surfer.py", "limit": "100.0"},
        tool_definition=definition,
        cwd=str(tmp_path),
    )

    rendered = "\n".join(component.render(80))

    assert "read src/agents/facebook_surfer.py" in rendered


def test_tool_execution_collapses_long_generic_results_until_expanded() -> None:
    component = ToolExecutionComponent("bash", {"command": "find ."})
    result = AgentToolResult(
        content=[TextContent(text="\n".join(f"line {index}" for index in range(12)))],
        details=None,
    )

    component.update_result(result, is_error=False)
    collapsed = "\n".join(component.render(80))

    assert "line 0" in collapsed
    assert "line 10" not in collapsed
    assert "... (2 more lines, to expand)" in collapsed

    component.set_expanded(True)
    assert "line 11" in "\n".join(component.render(80))


def test_user_and_skill_invocation_components_render_like_pi() -> None:
    from appv22.tui import SkillInvocationMessageComponent, UserMessageComponent, parse_skill_block

    user = UserMessageComponent("hello **user**")
    rendered_user = user.render(80)
    assert rendered_user[0].startswith("\x1b]133;A\x07")
    assert "\x1b]133;B\x07\x1b]133;C\x07" in rendered_user[-1]
    assert "hello user" in strip_ansi("\n".join(rendered_user))
    assert "> hello" not in strip_ansi("\n".join(rendered_user))

    parsed = parse_skill_block(
        '<skill name="python" location="/skills/python/SKILL.md">\n'
        "Use pytest first.\n"
        "</skill>\n\n"
        "Apply it to the TUI."
    )
    assert parsed is not None
    assert parsed.name == "python"
    assert parsed.location.endswith("SKILL.md")
    assert parsed.user_message == "Apply it to the TUI."

    skill = SkillInvocationMessageComponent(parsed)
    collapsed = strip_ansi("\n".join(skill.render(80)))
    assert "[skill] python" in collapsed
    assert "Use pytest first." not in collapsed

    skill.set_expanded(True)
    expanded = strip_ansi("\n".join(skill.render(80)))
    assert "python" in expanded
    assert "Use pytest first." in expanded


def test_message_to_component_splits_skill_block_from_user_message() -> None:
    from appv22.tui import message_to_component

    message = UserMessage(
        content=(
            '<skill name="tui" location="/skills/tui/SKILL.md">\n'
            "Render with boxes.\n"
            "</skill>\n\n"
            "Now update appv22."
        ),
        timestamp=now_ms(),
    )

    component = message_to_component(message)
    assert component is not None
    rendered = strip_ansi("\n".join(component.render(100)))

    assert "[skill] tui" in rendered
    assert "Render with boxes." not in rendered
    assert "Now update appv22." in rendered
    assert "> Now update" not in rendered


def test_bash_execution_component_renders_status_and_output() -> None:
    from appv22.tui import BashExecutionComponent, message_to_component

    component = BashExecutionComponent("printf hi")
    initial = strip_ansi("\n".join(component.render(80)))
    assert "$ printf hi" in initial
    assert "Running" in initial

    component.append_output("line 1\n" + "\n".join(f"line {index}" for index in range(2, 25)))
    component.set_complete(exit_code=2, cancelled=False, truncated=True, full_output_path="/tmp/full.log")
    collapsed = strip_ansi("\n".join(component.render(80)))
    assert "$ printf hi" in collapsed
    assert "line 24" in collapsed
    assert "|line 1 " not in collapsed
    assert "... 4 more lines" in collapsed
    assert "(exit 2)" in collapsed
    assert "Full output: /tmp/full.log" in collapsed

    component.set_expanded(True)
    expanded = strip_ansi("\n".join(component.render(80)))
    assert "line 1" in expanded

    excluded = BashExecutionMessage(
        command="secret",
        output="hidden",
        exit_code=0,
        cancelled=False,
        truncated=False,
        full_output_path=None,
        timestamp=now_ms(),
        exclude_from_context=True,
    )
    mapped = message_to_component(excluded)
    assert mapped is not None
    mapped_rendered = strip_ansi("\n".join(mapped.render(80)))
    assert "$ secret" in mapped_rendered
    assert "[no context]" in mapped_rendered
    assert "hidden" in mapped_rendered


def test_special_message_components_render_collapsed_and_expanded() -> None:
    from appv22.tui import (
        BranchSummaryMessageComponent,
        CompactionSummaryMessageComponent,
        CustomMessageComponent,
    )

    branch = BranchSummaryMessage(summary="Changed `src/app.py` and kept tests green.", from_id="root", timestamp=now_ms())
    branch_component = BranchSummaryMessageComponent(branch)
    branch_collapsed = "\n".join(branch_component.render(80))
    assert "[branch]" in branch_collapsed
    assert "Branch summary" in branch_collapsed
    assert "src/app.py" not in branch_collapsed
    branch_component.set_expanded(True)
    branch_expanded = "\n".join(branch_component.render(80))
    assert "Branch Summary" in branch_expanded
    assert "src/app.py" in branch_expanded

    compaction = type(
        "CompactionSummary",
        (),
        {"role": "compactionSummary", "summary": "Historical context was compacted.", "tokensBefore": 12345},
    )()
    compaction_component = CompactionSummaryMessageComponent(compaction)
    compaction_collapsed = "\n".join(compaction_component.render(80))
    assert "[compaction]" in compaction_collapsed
    assert "12,345" in compaction_collapsed
    assert "Historical context" not in compaction_collapsed
    compaction_component.set_expanded(True)
    assert "Historical context was compacted." in "\n".join(compaction_component.render(80))

    custom = CustomMessage(
        custom_type="note",
        content=[TextContent(text="Remember **this** detail.")],
        display=True,
        details={"source": "extension"},
        timestamp=now_ms(),
    )
    custom_component = CustomMessageComponent(custom)
    custom_rendered = "\n".join(custom_component.render(80))
    assert "[note]" in custom_rendered
    assert "Remember this detail." in custom_rendered


def test_interactive_mode_renders_existing_special_messages(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        BranchSummaryMessage(summary="Returned from old branch.", from_id="root", timestamp=now_ms()),
        type(
            "CompactionSummary",
            (),
            {"role": "compactionSummary", "summary": "Older history compacted.", "tokensBefore": 16000},
        )(),
        CustomMessage(
            custom_type="context",
            content="Extension-provided context",
            display=True,
            details=None,
            timestamp=now_ms(),
        ),
    ]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()

    rendered = "\n".join(app.tui.render(120))
    assert "[branch]" in rendered
    assert "Branch summary" in rendered
    assert "[compaction]" in rendered
    assert "16,000" in rendered
    assert "[context]" in rendered
    assert "Extension-provided context" in rendered


def test_interactive_mode_uses_extension_custom_message_renderer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_message_renderer(
        "context",
        lambda message, options=None, theme=None: Text(f"custom rendered: {message.content}"),
    )
    app.session.agent.state.messages = [
        CustomMessage(
            custom_type="context",
            content="Extension-provided context",
            display=True,
            details=None,
            timestamp=now_ms(),
        ),
    ]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()

    rendered = "\n".join(app.tui.render(120))
    assert "custom rendered: Extension-provided context" in rendered
    assert "[context]" not in rendered


def test_interactive_mode_dispatches_extension_shortcut_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    model = faux_model()
    model.context_window = 1000
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    contexts: list[dict[str, object]] = []

    def handle_shortcut(ctx):
        contexts.append(ctx)
        ctx["ui"].notify("shortcut ran")

    app.session.extension_runner.register_shortcut(
        "ctrl+y",
        {"description": "Run shortcut", "handler": handle_shortcut},
    )
    inputs = iter(["ctrl+y", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "shortcut ran" in rendered
    assert "ctrl+y" not in rendered
    assert len(contexts) == 1
    assert contexts[0]["mode"] == "tui"
    assert contexts[0]["hasUI"] is True
    assert contexts[0]["cwd"] == str(tmp_path)
    assert contexts[0]["isIdle"]() is True
    context_usage = contexts[0]["getContextUsage"]()
    assert context_usage == app.session.get_context_usage()
    assert context_usage is not None
    assert set(context_usage) == {"tokens", "contextWindow", "percent"}
    assert context_usage["contextWindow"] == 1000


def test_interactive_mode_extension_shortcut_can_set_footer_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def handle_shortcut(ctx):
        ctx["ui"].setStatus("ext", "ready")

    app.session.extension_runner.register_shortcut(
        "ctrl+s",
        {"description": "Set status", "handler": handle_shortcut},
    )
    inputs = iter(["ctrl+s", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "ext: ready" in rendered
    assert "ctrl+s" not in rendered


def test_interactive_mode_extension_shortcut_can_set_working_message(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_working(ctx):
        ctx["ui"].setWorkingMessage("Indexing workspace")

    app.session.extension_runner.register_shortcut(
        "ctrl+w",
        {"description": "Set working", "handler": set_working},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+w") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: Indexing workspace" in rendered
    assert "ctrl+w" not in rendered


def test_interactive_mode_extension_shortcut_can_hide_working_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def hide_working(ctx):
        ctx["ui"].setWorkingMessage("Hidden extension status")
        ctx["ui"].setWorkingVisible(False)

    app.session.extension_runner.register_shortcut(
        "ctrl+h",
        {"description": "Hide working", "handler": hide_working},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+h") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: Hidden extension status" not in rendered
    assert "model:" in rendered
    assert "ctrl+h" not in rendered


def test_interactive_mode_extension_shortcut_can_set_working_indicator(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_indicator(ctx):
        ctx["ui"].setWorkingMessage("Indexing workspace")
        ctx["ui"].setWorkingIndicator({"frames": ["*"]})

    app.session.extension_runner.register_shortcut(
        "ctrl+i",
        {"description": "Set indicator", "handler": set_indicator},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+i") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: * Indexing workspace" in rendered
    assert "ctrl+i" not in rendered


def test_interactive_mode_extension_shortcut_can_prompt_for_input(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[str | None] = []

    def ask_for_input(ctx):
        captured.append(ctx["ui"].input("Project name", "appv22"))

    app.session.extension_runner.register_shortcut(
        "ctrl+n",
        {"description": "Ask for input", "handler": ask_for_input},
    )
    inputs = iter(["ported-ui"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+n") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["ported-ui"]
    assert prompts == ["Project name (appv22): "]
    assert "input: Project name" in rendered
    assert "ported-ui" in rendered
    assert "ctrl+n" not in rendered


def test_interactive_mode_extension_shortcut_can_select_option(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[str | None] = []

    def pick_option(ctx):
        captured.append(ctx["ui"].select("Deployment target", ["staging", "production"]))

    app.session.extension_runner.register_shortcut(
        "ctrl+d",
        {"description": "Pick target", "handler": pick_option},
    )
    inputs = iter(["2"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+d") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["production"]
    assert prompts == ["Deployment target [1-2]: "]
    assert "select: Deployment target" in rendered
    assert "1. staging" in rendered
    assert "2. production" in rendered
    assert "production" in rendered
    assert "ctrl+d" not in rendered


def test_interactive_mode_extension_shortcut_can_confirm(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[bool] = []

    def confirm_action(ctx):
        captured.append(ctx["ui"].confirm("Delete deployment?", "This cannot be undone"))

    app.session.extension_runner.register_shortcut(
        "ctrl+delete",
        {"description": "Confirm delete", "handler": confirm_action},
    )
    inputs = iter(["1"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+delete") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == [True]
    assert prompts == ["Delete deployment? This cannot be undone [1-2]: "]
    assert "confirm: Delete deployment? This cannot be undone" in rendered
    assert "1. Yes" in rendered
    assert "2. No" in rendered
    assert "Yes" in rendered
    assert "ctrl+delete" not in rendered


def test_interactive_mode_extension_shortcut_can_listen_to_terminal_input(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "listener reply")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    seen: list[str] = []
    unsubscribe_holder: list[object] = []

    def install_listener(ctx):
        def listener(data):
            seen.append(data)
            if data == "rewrite":
                unsubscribe_holder[0]()
                return {"data": "rewritten prompt"}
            return None

        unsubscribe_holder.append(ctx["ui"].onTerminalInput(listener))

    app.session.extension_runner.register_shortcut(
        "ctrl+l",
        {"description": "Install listener", "handler": install_listener},
    )
    inputs = iter(["ctrl+l", "rewrite", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls["model"] == 1
    assert seen == ["rewrite"]
    assert "rewritten prompt" in rendered
    assert "listener reply" in rendered
    assert "rewrite" not in rendered
    assert "ctrl+l" not in rendered


def test_interactive_mode_extension_shortcut_can_set_hidden_thinking_label(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        AssistantMessage(
            content=[ThinkingContent(thinking="private chain of thought"), TextContent(text="Visible answer")],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
    ]

    def set_label(ctx):
        ctx["ui"].setHiddenThinkingLabel("Reasoning hidden")

    app.session.extension_runner.register_shortcut(
        "ctrl+t",
        {"description": "Hide thinking", "handler": set_label},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.hide_thinking_block = True

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+t") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Reasoning hidden" in rendered
    assert "Visible answer" in rendered
    assert "private chain of thought" not in rendered
    assert "ctrl+t" not in rendered


def test_interactive_mode_extension_shortcut_can_set_terminal_title(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_title(ctx):
        ctx["ui"].setTitle("appv22 - workspace")

    app.session.extension_runner.register_shortcut(
        "ctrl+shift+t",
        {"description": "Set title", "handler": set_title},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+shift+t") is True

    assert "\x1b]0;appv22 - workspace\x07" in terminal.output


def test_interactive_mode_extension_shortcut_can_set_and_clear_widgets(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_widgets(ctx):
        ctx["ui"].setWidget("above", ["Above editor widget"])
        ctx["ui"].setWidget("below", ["Below editor widget"], {"placement": "belowEditor"})

    def replace_widgets(ctx):
        ctx["ui"].setWidget("above", ["Above replacement"])
        ctx["ui"].setWidget("below", None)

    app.session.extension_runner.register_shortcut(
        "ctrl+u",
        {"description": "Set widgets", "handler": set_widgets},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+u",
        {"description": "Replace widgets", "handler": replace_widgets},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+u") is True

    rendered_lines = [strip_ansi(line) for line in app.tui.render(140)]
    above_index = rendered_lines.index("Above editor widget")
    below_index = rendered_lines.index("Below editor widget")
    status_index = next(index for index, line in enumerate(rendered_lines) if line.startswith("status:"))
    assert above_index < below_index < status_index

    assert mode._dispatch_extension_shortcut("ctrl+shift+u") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Above replacement" in rendered
    assert "Above editor widget" not in rendered
    assert "Below editor widget" not in rendered


def test_interactive_mode_extension_shortcut_can_replace_and_restore_footer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    class DynamicFooter(Component):
        def __init__(self, provider) -> None:
            self.provider = provider
            self.disposed = False

        def render(self, width: int) -> list[str]:
            statuses = self.provider.getExtensionStatuses()
            return [f"custom footer: plan={statuses.get('plan', 'missing')}"]

        def dispose(self) -> None:
            self.disposed = True

    custom_footers: list[DynamicFooter] = []

    def set_footer(ctx):
        ctx["ui"].setStatus("plan", "ready")

        def make_footer(tui, theme, footer_data):
            footer = DynamicFooter(footer_data)
            custom_footers.append(footer)
            return footer

        ctx["ui"].setFooter(make_footer)

    def restore_footer(ctx):
        ctx["ui"].setFooter(None)

    app.session.extension_runner.register_shortcut(
        "ctrl+f",
        {"description": "Set footer", "handler": set_footer},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+f",
        {"description": "Restore footer", "handler": restore_footer},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+f") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "custom footer: plan=ready" in rendered
    assert "model: faux-model" not in rendered
    assert custom_footers and custom_footers[-1].disposed is False

    assert mode._dispatch_extension_shortcut("ctrl+shift+f") is True

    restored = strip_ansi("\n".join(app.tui.render(140)))
    assert custom_footers[-1].disposed is True
    assert "custom footer" not in restored
    assert "model:" in restored
    assert "plan: ready" in restored


def test_interactive_mode_extension_shortcut_can_replace_and_restore_header(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    class DynamicHeader(Component):
        def __init__(self) -> None:
            self.disposed = False

        def render(self, width: int) -> list[str]:
            return ["custom header", "extension startup"]

        def dispose(self) -> None:
            self.disposed = True

    custom_headers: list[DynamicHeader] = []

    def set_header(ctx):
        def make_header(tui, theme):
            header = DynamicHeader()
            custom_headers.append(header)
            return header

        ctx["ui"].setHeader(make_header)

    def restore_header(ctx):
        ctx["ui"].setHeader(None)

    app.session.extension_runner.register_shortcut(
        "ctrl+g",
        {"description": "Set header", "handler": set_header},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+g",
        {"description": "Restore header", "handler": restore_header},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+g") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "custom header" in rendered
    assert "extension startup" in rendered
    assert "appv22 pi+hermes TUI" not in rendered
    assert custom_headers and custom_headers[-1].disposed is False

    assert mode._dispatch_extension_shortcut("ctrl+shift+g") is True

    restored = strip_ansi("\n".join(app.tui.render(140)))
    assert custom_headers[-1].disposed is True
    assert "custom header" not in restored
    assert "appv22 pi+hermes TUI" in restored


def test_interactive_mode_extension_shortcut_can_control_editor_text(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "editor submitted")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    captured: list[str] = []

    def edit_buffer(ctx):
        ctx["ui"].setEditorText("prefill")
        ctx["ui"].pasteToEditor(" + pasted")
        captured.append(ctx["ui"].getEditorText())

    app.session.extension_runner.register_shortcut(
        "ctrl+e",
        {"description": "Edit buffer", "handler": edit_buffer},
    )
    inputs = iter(["ctrl+e", "", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["prefill + pasted"]
    assert calls["model"] == 1
    assert "prefill + pasted" in rendered
    assert "editor submitted" in rendered
    assert "ctrl+e" not in rendered


def test_interactive_mode_extension_shortcut_can_open_multiline_editor(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[str | None] = []

    def open_editor(ctx):
        captured.append(ctx["ui"].editor("Edit handoff prompt", "prefill line 1\nprefill line 2"))

    app.session.extension_runner.register_shortcut(
        "ctrl+m",
        {"description": "Open editor", "handler": open_editor},
    )
    inputs = iter(["edited line 1\nedited line 2"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+m") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["edited line 1\nedited line 2"]
    assert prompts == ["Edit handoff prompt: "]
    assert "editor: Edit handoff prompt" in rendered
    assert "prefill line 1" in rendered
    assert "prefill line 2" in rendered
    assert "edited line 1" in rendered
    assert "edited line 2" in rendered
    assert "ctrl+m" not in rendered


def test_interactive_mode_extension_shortcut_can_add_autocomplete_provider(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_command(
        "review",
        {"description": "Review files", "handler": lambda args, ctx=None: None},
    )
    app.session.extension_runner.register_command(
        "deploy",
        {
            "description": "Deploy to environment",
            "getArgumentCompletions": lambda prefix: [
                {"value": env, "label": env} for env in ("dev", "staging", "prod") if env.startswith(prefix)
            ],
            "handler": lambda args, ctx=None: None,
        },
    )

    def install_provider(ctx):
        def wrap(current):
            class IssueProvider:
                triggerCharacters = ["#"]

                def getSuggestions(self, lines, cursor_line, cursor_col, options):
                    before_cursor = (lines[cursor_line] if cursor_line < len(lines) else "")[:cursor_col]
                    if not before_cursor.endswith("#2"):
                        return current.getSuggestions(lines, cursor_line, cursor_col, options)
                    return {
                        "prefix": "#2",
                        "items": [
                            {
                                "value": "#2983",
                                "label": "#2983",
                                "description": "Extension API for autocomplete",
                            }
                        ],
                    }

                def applyCompletion(self, lines, cursor_line, cursor_col, item, prefix):
                    return current.applyCompletion(lines, cursor_line, cursor_col, item, prefix)

                def shouldTriggerFileCompletion(self, lines, cursor_line, cursor_col):
                    return current.shouldTriggerFileCompletion(lines, cursor_line, cursor_col)

            return IssueProvider()

        ctx["ui"].addAutocompleteProvider(wrap)

    app.session.extension_runner.register_shortcut(
        "ctrl+a",
        {"description": "Install autocomplete", "handler": install_provider},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+a") is True

    issue_suggestions = mode.get_autocomplete_suggestions(["please fix #2"], 0, len("please fix #2"))
    assert issue_suggestions == {
        "prefix": "#2",
        "items": [
            {
                "value": "#2983",
                "label": "#2983",
                "description": "Extension API for autocomplete",
            }
        ],
    }

    slash_suggestions = mode.get_autocomplete_suggestions(["/rev"], 0, len("/rev"))
    assert slash_suggestions == {
        "prefix": "/rev",
        "items": [{"value": "review", "label": "review", "description": "Review files"}],
    }
    argument_suggestions = mode.get_autocomplete_suggestions(["/deploy st"], 0, len("/deploy st"))
    assert argument_suggestions == {
        "prefix": "st",
        "items": [{"value": "staging", "label": "staging"}],
    }
    assert mode.autocomplete_provider.triggerCharacters == ["#"]

    editor = Input("please fix #2")
    editor.setAutocompleteProvider(mode.autocomplete_provider)
    editor.handle_input("\t")
    assert editor.get_value() == "please fix #2983"


def test_interactive_mode_extension_shortcut_can_open_custom_component(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    class ConfirmComponent(Component):
        def __init__(self, done) -> None:
            self.done = done
            self.inputs: list[str] = []
            self.disposed = False

        def render(self, width: int) -> list[str]:
            return ["custom confirm", "press enter to accept"]

        def handle_input(self, data: str) -> None:
            self.inputs.append(data)
            if data == "\r":
                self.done({"accepted": True})

        def dispose(self) -> None:
            self.disposed = True

    captured: list[object] = []
    components: list[ConfirmComponent] = []
    factory_args: list[tuple[object, object, object]] = []

    def open_custom(ctx):
        def make_component(tui, theme, keybindings, done):
            factory_args.append((tui, theme, keybindings))
            component = ConfirmComponent(done)
            components.append(component)
            return component

        captured.append(ctx["ui"].custom(make_component))

    app.session.extension_runner.register_shortcut(
        "ctrl+k",
        {"description": "Open custom component", "handler": open_custom},
    )
    inputs = iter(["\r"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+k") is True

    rendered_now = strip_ansi("\n".join(app.tui.render(140)))
    rendered_history = strip_ansi(terminal.output)
    assert captured == [{"accepted": True}]
    assert len(components) == 1
    assert factory_args == [(app.tui, None, None)]
    assert components[0].inputs == ["\r"]
    assert components[0].disposed is True
    assert "custom confirm" in rendered_history
    assert "custom confirm" not in rendered_now
    assert "press enter to accept" not in rendered_now
    assert "ctrl+k" not in rendered_now


def test_tui_footer_status_diff_and_width_constraints() -> None:
    terminal = FakeTerminal(columns=24)
    tui = TUI(terminal)
    footer = FooterComponent(cwd="/tmp/very/long/project/path", model="faux-model", thinking_level="off")
    status = StatusLine("Idle")
    tui.add(footer)
    tui.add(status)

    first = tui.request_render()
    status.set_message("Working on a long operation")
    second = tui.request_render()

    assert first.full is True
    assert second.full is False
    assert second.first_changed == 1
    assert all(visible_width(line) <= 24 for line in second.lines)


def test_tui_diff_render_clips_to_terminal_rows_without_scrolling_past_viewport() -> None:
    terminal = FakeTerminal(columns=80, rows=5)
    tui = TUI(terminal)
    for index in range(8):
        tui.add(Text(f"history {index}"))
    footer = StatusLine("Idle")
    tui.add(footer)

    first = tui.request_render()
    footer.set_message("Running")
    second = tui.request_render()

    assert len(first.lines) == 5
    assert len(second.lines) == 5
    assert second.lines[-1] == "status: Running"
    assert "\x1b[6;1H" not in terminal.writes[-1]
    assert "\x1b[9;1H" not in terminal.writes[-1]


def test_interactive_mode_renders_real_prompt_loop(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "tui reply")))
    terminal = FakeTerminal(columns=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["hi", "/exit"])
    input_prompts = []
    mode = InteractiveMode(
        app,
        input_fn=lambda prompt: input_prompts.append(prompt) or next(inputs),
    )

    exit_code = mode.run()

    rendered = strip_ansi(terminal.output)
    assert exit_code == 0
    assert "appv22" in rendered
    assert "Current working directory:" in rendered
    assert "hi" in rendered
    assert "> hi" not in rendered
    assert "tui reply" in rendered
    assert '{"type":' not in rendered
    assert input_prompts == ["", ""]


def test_interactive_mode_keeps_agent_output_above_status_footer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "ordered reply")))
    terminal = FakeTerminal(columns=100)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["hi", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = app.tui.render(100)
    prompt_index = next(index for index, line in enumerate(rendered) if "hi" in strip_ansi(line))
    reply_index = next(index for index, line in enumerate(rendered) if line == "ordered reply")
    status_index = next(index for index, line in enumerate(rendered) if line.startswith("status:"))
    footer_index = next(index for index, line in enumerate(rendered) if line.startswith("model:"))

    assert prompt_index < reply_index < status_index < footer_index


def test_interactive_mode_bang_runs_bash_without_model_and_records_context(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["! printf hi", "!! printf secret", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["n"] == 0
    assert "$ printf hi" in rendered
    assert "$ printf secret [no context]" in rendered
    assert "hi" in rendered
    assert "secret" in rendered
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert [message.command for message in bash_messages] == ["printf hi", "printf secret"]
    assert bash_messages[0].excludeFromContext in (None, False)
    assert bash_messages[1].excludeFromContext is True
    converted = app.session._convert_to_llm(app.messages)
    converted_text = "\n".join(
        block.text for message in converted for block in getattr(message, "content", []) if getattr(block, "type", None) == "text"
    )
    assert "printf hi" in converted_text
    assert "printf secret" not in converted_text


def test_interactive_mode_bang_uses_user_bash_extension_result(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    seen_events: list[dict] = []

    def handle_user_bash(event):
        seen_events.append(event)
        return {
            "result": BashResult(
                output="from extension\n",
                exit_code=0,
                cancelled=False,
                truncated=False,
                full_output_path=None,
            )
        }

    app.session.extension_runner.on("user_bash", handle_user_bash)
    inputs = iter(["! printf from-shell", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert calls["n"] == 0
    assert seen_events == [
        {
            "type": "user_bash",
            "command": "printf from-shell",
            "excludeFromContext": False,
            "cwd": str(tmp_path),
        }
    ]
    assert "from extension" in rendered
    assert bash_messages[-1].command == "printf from-shell"
    assert bash_messages[-1].output == "from extension\n"


def test_interactive_mode_bang_uses_user_bash_extension_operations(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    exec_calls: list[tuple[str, str]] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        exec_calls.append((command, cwd))
        options.on_data(b"from custom operations\n")
        return {"exit_code": 0}

    def handle_user_bash(event):
        return {"operations": BashOperations(exec=exec_command)}

    app.session.extension_runner.on("user_bash", handle_user_bash)
    inputs = iter(["! printf from-shell", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert exec_calls == [("printf from-shell", str(tmp_path))]
    assert "from custom operations" in rendered
    assert bash_messages[-1].output == "from custom operations\n"


def test_interactive_mode_manual_compress_renders_feedback_and_updates_footer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nmanual compacted",
        enable_tui=True,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    inputs = iter(["/compress old context", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = "\n".join(app.tui.render(140))
    assert "compact: Compressed:" in rendered
    assert "Approx request size:" in rendered
    assert "ctx:" in rendered
    assert "compactions: 1" in rendered


def test_interactive_mode_compact_alias_is_local_and_does_not_call_model(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nmanual compacted",
        enable_tui=True,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    inputs = iter(["/compact old context", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = "\n".join(app.tui.render(140))
    assert calls["n"] == 0
    assert "compact: Compressed:" in rendered
    assert "model should not run" not in rendered
    assert "compactions: 1" in rendered


def test_interactive_mode_login_logout_oauth_are_local_tui_commands(tmp_path) -> None:
    calls: list[object] = []

    def script(model, context):
        calls.append(("model", context))
        return text_response_events(model, "model should not run")

    def login(callbacks):
        calls.append(("login", sorted(callbacks.keys())))
        return {"access": "login-token", "refresh": "refresh-token", "expires": 4_102_444_800_000}

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.registerProvider(
        "sso",
        {
            "baseUrl": "https://sso.example.test",
            "api": "faux",
            "oauth": {
                "name": "Corporate SSO",
                "login": login,
                "refreshToken": lambda credentials: credentials,
                "getApiKey": lambda credentials: credentials["access"],
            },
            "models": [
                {
                    "id": "sso-model",
                    "name": "SSO Model",
                    "reasoning": False,
                    "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "contextWindow": 32000,
                    "maxTokens": 4096,
                }
            ],
        },
    )
    inputs = iter(["/login", "1", "1", "/logout", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert [call[0] for call in calls] == ["login"]
    assert "onAuth" in calls[0][1]
    assert "onDeviceCode" in calls[0][1]
    assert "onPrompt" in calls[0][1]
    assert "Logged in to Corporate SSO" in rendered
    assert "Logged out of Corporate SSO" in rendered
    assert get_provider_auth_status("sso") == {"configured": False}
    assert get_api_key_for_provider("sso") is None
    assert "model should not run" not in rendered


def test_interactive_mode_login_api_key_is_local_tui_command(tmp_path) -> None:
    calls: list[object] = []

    def script(model, context):
        calls.append(("model", context))
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.registerProvider(
        "proxy",
        {
            "name": "Proxy AI",
            "baseUrl": "https://proxy.example.test",
            "api": "faux",
            "apiKey": "$PROXY_API_KEY",
            "models": [
                {
                    "id": "proxy-model",
                    "name": "Proxy Model",
                    "reasoning": False,
                    "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "contextWindow": 32000,
                    "maxTokens": 4096,
                }
            ],
        },
    )
    inputs = iter(["/login", "2", "1", "typed-secret", "/logout", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls == []
    assert "Saved API key for Proxy AI" in rendered
    assert "Removed stored API key for Proxy AI" in rendered
    assert get_provider_auth_status("proxy") == {"configured": False}
    assert get_api_key_for_provider("proxy") is None
    assert "model should not run" not in rendered


def test_interactive_mode_bad_read_numeric_string_returns_tool_error_not_traceback(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "file.py").write_text("print('ok')\n", encoding="utf-8")
    calls = {"count": 0}

    def script(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(model, "read", {"path": "src/file.py", "limit": "100.0"})
        return text_response_events(model, "handled invalid read")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    app.run_turn("read src/file.py with limit 100")

    rendered = "\n".join(app.tui.render(120))
    assert "read src/file.py" in rendered
    assert "read.limit: expected integer" in rendered
    assert "Traceback" not in rendered
    assert calls["count"] == 2


def test_strip_ansi_helper() -> None:
    assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"
