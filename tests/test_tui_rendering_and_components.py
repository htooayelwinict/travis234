from __future__ import annotations

from tests._support_tui import *  # noqa: F403


def test_interactive_mode_turn_failure_resets_status_and_accepts_followup_prompt(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    def run_turn(prompt: str, **kwargs) -> None:
        prompts.append(prompt)
        if prompt == "boom":
            raise RuntimeError("boom")
        mode.history.add(Text("after failure"))
        app.tui.request_render()

    app.run_turn = run_turn

    mode.status.set_message("Running")
    mode._start_turn_thread("boom", 0, 0)
    mode._wait_for_active_turn()

    rendered_after_failure = strip_ansi("\n".join(app.tui.render(120)))
    assert prompts == ["boom"]
    assert "Turn failed: boom" in rendered_after_failure
    assert mode.status._message == "Idle"
    assert "status: Running" not in rendered_after_failure
    assert "status: Compressing" not in rendered_after_failure
    assert "status: Aborting" not in rendered_after_failure

    mode.status.set_message("Running")
    mode._start_turn_thread("after failure", app.compaction.compressor.compression_count, 0)
    mode._wait_for_active_turn()

    rendered_after_followup = strip_ansi("\n".join(app.tui.render(120)))
    assert prompts == ["boom", "after failure"]
    assert "after failure" in rendered_after_followup
    assert mode.status._message == "Idle"
    assert "status: Running" not in rendered_after_followup

def test_interactive_mode_ctrl_c_requires_second_press_to_exit_idle_tui(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    outcome: dict[str, object] = {}

    def run_mode() -> None:
        try:
            outcome["code"] = mode.run()
        except BaseException as error:  # noqa: BLE001 - test thread must surface failures.
            outcome["error"] = error

    thread = threading.Thread(target=run_mode)
    thread.start()
    exited_after_first_ctrl_c = False
    exited_after_second_ctrl_c = False
    try:
        assert _wait_until(lambda: terminal.input_handler is not None and mode.active_editor is not None)
        terminal.input_handler("\x03")
        exited_after_first_ctrl_c = _wait_until(lambda: not thread.is_alive(), timeout=0.2)
        if thread.is_alive():
            terminal.input_handler("\x03")
            exited_after_second_ctrl_c = _wait_until(lambda: not thread.is_alive(), timeout=0.5)
    finally:
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert exited_after_first_ctrl_c is False
    assert exited_after_second_ctrl_c is True
    assert outcome["code"] == 0
    assert calls["n"] == 0

def test_interactive_mode_run_ctrl_c_aborts_active_turn_and_recovers_prompt(tmp_path) -> None:
    started = threading.Event()
    aborted = threading.Event()
    provider_calls = {"n": 0}

    def script(model, context):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(model, "aborter", {})
        return text_response_events(model, "followup ok")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def aborter_execute(tool_call_id, args, signal=None, on_update=None):
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if signal and signal.aborted:
                aborted.set()
                raise RuntimeError("Operation aborted")
            time.sleep(0.005)
        raise RuntimeError("abort signal was not delivered")

    app.session.agent.state.tools = [
        AgentTool(
            name="aborter",
            description="aborter",
            parameters={"type": "object", "properties": {}},
            label="Aborter",
            execute=aborter_execute,
        )
    ]
    mode = InteractiveMode(app)
    outcome: dict[str, object] = {}

    def run_mode() -> None:
        try:
            outcome["code"] = mode.run()
        except BaseException as error:  # noqa: BLE001 - test thread must surface failures.
            outcome["error"] = error

    thread = threading.Thread(target=run_mode)
    thread.start()
    try:
        assert _wait_until(lambda: terminal.input_handler is not None and mode.active_editor is not None)
        assert terminal.input_handler is not None

        terminal.input_handler("run aborter\r")
        assert started.wait(timeout=2)

        terminal.input_handler("\x03")

        assert _wait_until(
            lambda: aborted.is_set()
            and not mode._is_turn_active()
            and mode.status._message == "Idle"
            and mode.active_editor is not None,
            timeout=2,
        )
        assert provider_calls["n"] == 1

        terminal.input_handler("followup\r")

        assert _wait_until(
            lambda: provider_calls["n"] == 2
            and not mode._is_turn_active()
            and mode.status._message == "Idle"
            and mode.active_editor is not None,
            timeout=2,
        )
        assert "followup ok" in strip_ansi(terminal.output)

        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        if thread.is_alive():
            mode._shutdown_requested = True
            app.session.agent.abort()
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0

def test_interactive_mode_sigint_aborts_active_turn_without_shutdown(tmp_path) -> None:
    started = threading.Event()
    aborted = threading.Event()

    def script(model, context):
        return tool_call_response_events(model, "aborter", {})

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def aborter_execute(tool_call_id, args, signal=None, on_update=None):
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if signal and signal.aborted:
                aborted.set()
                raise RuntimeError("Operation aborted")
            time.sleep(0.005)
        raise RuntimeError("abort signal was not delivered")

    app.session.agent.state.tools = [
        AgentTool(
            name="aborter",
            description="aborter",
            parameters={"type": "object", "properties": {}},
            label="Aborter",
            execute=aborter_execute,
        )
    ]
    mode = InteractiveMode(app)
    mode.init()

    mode.status.set_message("Running")
    mode._start_turn_thread("run aborter", 0, 0)
    assert started.wait(timeout=2)

    mode._handle_sigint(None, None)

    assert mode._shutdown_requested is False
    assert mode.status._message == "Aborting"
    mode._wait_for_active_turn()
    assert aborted.is_set()
    assert mode.status._message == "Idle"
    assert not mode._is_turn_active()

def test_interactive_mode_late_ctrl_c_after_turn_finish_does_not_exit(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "quick done")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    mode.init()

    mode.status.set_message("Running")
    mode._start_turn_thread("quick", 0, 0)
    mode._wait_for_active_turn()
    assert mode.status._message == "Idle"

    mode._handle_sigint(None, None)

    assert mode._shutdown_requested is False
    assert mode.status._message == "Idle"

def test_tui_ports_travis234_input_listener_transform_consume_and_unsubscribe() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    events: list[tuple[str, str]] = []

    def transform(data: str):
        events.append(("transform", data))
        return {"data": f"{data}!"}

    def consume(data: str):
        events.append(("consume", data))
        return {"consume": True}

    unsubscribe_transform = tui.add_input_listener(transform)
    tui.add_input_listener(consume)
    tui.start()

    assert terminal.input_handler is not None
    terminal.input_handler("a")

    unsubscribe_transform()
    tui.remove_input_listener(consume)
    terminal.input_handler("b")

    assert events == [("transform", "a"), ("consume", "a!")]

def test_tui_ports_travis234_terminal_input_to_focused_component() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    editor = Input(prompt="> ")
    tui.add(editor)
    tui.add(Text("footer"))
    tui.add_input_listener(lambda data: {"data": data.upper()})

    tui.set_focus(editor)
    tui.start()
    before_writes = len(terminal.writes)
    assert editor.focused is True

    assert terminal.input_handler is not None
    terminal.input_handler("a")

    assert editor.get_value() == "A"
    assert len(terminal.writes) > before_writes
    assert "A" in terminal.writes[-1]

    tui.set_focus(None)
    assert editor.focused is False

def test_tui_ports_travis234_invisible_focused_overlay_redirects_to_visible_capturing_overlay() -> None:
    class Recorder(Component):
        def __init__(self, label: str) -> None:
            self.label = label
            self.focused = False
            self.events: list[str] = []

        def render(self, width: int) -> list[str]:
            return [self.label]

        def handle_input(self, data: str) -> None:
            self.events.append(data)

    terminal = FakeTerminal(columns=80, rows=24)
    tui = TUI(terminal)
    fallback = Recorder("FALLBACK")
    non_capturing = Recorder("NC")
    primary = Recorder("PRIMARY")
    is_visible = True

    tui.add(Text(""))
    tui.start()
    tui.show_overlay(fallback)
    tui.show_overlay(non_capturing, {"nonCapturing": True})
    tui.show_overlay(primary, {"visible": lambda _width, _height: is_visible})
    assert primary.focused is True

    is_visible = False
    assert terminal.input_handler is not None
    terminal.input_handler("x")

    assert fallback.events == ["x"]
    assert non_capturing.events == []
    assert primary.events == []
    assert fallback.focused is True

def test_tui_ports_travis234_key_release_filtering_for_focused_component() -> None:
    class RecordingInput(Component):
        def __init__(self, *, wants_key_release: bool = False) -> None:
            self.events: list[str] = []
            self.focused = False
            self.wants_key_release = wants_key_release

        def render(self, width: int) -> list[str]:
            return ["events:" + ",".join(self.events)]

        def handle_input(self, data: str) -> None:
            self.events.append(data)

    release_sequence = "\x1b[97;1:3u"
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    default = RecordingInput()
    wants_release = RecordingInput(wants_key_release=True)
    tui.add(default)
    tui.add(wants_release)
    tui.start()

    assert terminal.input_handler is not None
    tui.set_focus(default)
    terminal.input_handler(release_sequence)
    terminal.input_handler("a")

    tui.set_focus(wants_release)
    terminal.input_handler(release_sequence)

    assert default.events == ["a"]
    assert wants_release.events == [release_sequence]

def test_tui_no_change_yields_empty_diff() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    tui.add(Text("static"))
    tui.request_render()
    info = tui.request_render()
    assert info.first_changed == -1

def test_tui_strips_travis234_cursor_marker_and_tracks_cursor_position() -> None:
    terminal = FakeTerminal(columns=40, rows=5)
    tui = TUI(terminal)
    editor = Input(value="hello", prompt="> ")
    editor.cursor = 2
    editor.focused = True
    tui.add(editor)

    info = tui.request_render()

    assert "\x1b_travis234:c\x07" not in terminal.output
    assert "\x1b_travis234:c\x07" not in "\n".join(info.lines)
    assert [strip_ansi(line).rstrip() for line in info.lines] == ["> hello"]
    assert info.cursor_position == (0, 4)

def test_tui_positions_hardware_cursor_for_focused_input() -> None:
    terminal = FakeTerminal(columns=40, rows=5)
    tui = TUI(terminal, show_hardware_cursor=True)
    editor = Input(value="hello", prompt="> ")
    editor.cursor = 2
    tui.add(editor)
    tui.set_focus(editor)

    info = tui.request_render()

    assert info.cursor_position == (0, 4)
    assert "\x1b[5G" in terminal.output
    assert "\x1b[?25h" in terminal.output

def test_tui_ports_travis234_terminal_output_normalization_without_mutating_lines() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("กำ ກຳ"))

    info = tui.request_render()

    assert info.lines == ["กำ ກຳ"]
    assert "กํา ກໍາ" in terminal.output
    assert "กำ ກຳ" not in terminal.output

def test_tui_ports_travis234_line_resets_after_terminal_output_lines() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("\x1b[3mItalic"))
    tui.add(Text("Plain"))

    info = tui.request_render()

    assert info.lines == ["\x1b[3mItalic", "Plain"]
    assert "\x1b[3mItalic\x1b[0m\x1b]8;;\x07\r\nPlain\x1b[0m\x1b]8;;\x07" in terminal.output

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

def test_interactive_renderer_hides_thinking_content_by_default() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    renderer = InteractiveRenderer(tui)

    msg = _assistant("")
    renderer.handle_event(MessageStartEvent(message=msg))
    streamed = AssistantMessage(
        content=[
            ThinkingContent(thinking="private chain of thought"),
            TextContent(text="Visible answer"),
        ],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    renderer.handle_event(MessageUpdateEvent(message=streamed, assistant_message_event=None))
    renderer.handle_event(MessageEndEvent(message=streamed))

    rendered = strip_ansi("\n".join(tui.render(80)))
    assert "Visible answer" in rendered
    assert "private chain of thought" not in rendered
    assert "Thinking:" not in rendered

def test_interactive_renderer_hides_streaming_tool_call_drafts_until_execution_start() -> None:
    from travis.ai.types import ToolCall

    terminal = FakeTerminal()
    tui = TUI(terminal)
    renderer = InteractiveRenderer(tui)

    msg = _assistant("")
    renderer.handle_event(MessageStartEvent(message=msg))
    draft = AssistantMessage(
        content=[ToolCall(id="c1", name="write", arguments={})],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    renderer.handle_event(MessageUpdateEvent(message=draft, assistant_message_event=None))

    rendered_draft = "\n".join(tui.render(80))
    assert "write" in rendered_draft
    assert "-> write" not in rendered_draft
    assert "write({})" not in rendered_draft

    draft_with_args = AssistantMessage(
        content=[ToolCall(id="c1", name="write", arguments={"path": "a.txt", "content": "body"})],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    renderer.handle_event(MessageUpdateEvent(message=draft_with_args, assistant_message_event=None))
    rendered_updated_draft = "\n".join(tui.render(80))
    assert "a.txt" in rendered_updated_draft
    assert "-> write" not in rendered_updated_draft

    renderer.handle_event(
        ToolExecutionStartEvent(tool_call_id="c1", tool_name="write", args={"path": "a.txt", "content": "body"})
    )
    rendered_started = "\n".join(tui.render(80))
    assert "write" in rendered_started
    assert "a.txt" in rendered_started

def test_interactive_renderer_duplicate_tool_calls_complete_through_agent_loop() -> None:
    from travis.ai.types import ToolCall, ToolcallEndEvent, ToolcallStartEvent

    terminal = FakeTerminal()
    tui = TUI(terminal)
    renderer = InteractiveRenderer(tui)
    model = faux_model()
    provider_calls = {"n": 0}

    def convert(messages):
        return [message for message in messages if getattr(message, "role", None) in ("user", "assistant", "toolResult")]

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] != 1:
            return text_response_events(m, "done")
        calls = [
            ToolCall(id="call_1", name="echo", arguments={"text": "same"}),
            ToolCall(id="call_2", name="echo", arguments={"text": "same"}),
            ToolCall(id="call_3", name="echo", arguments={"text": "different"}),
        ]
        partial = AssistantMessage(
            content=list(calls),
            api=m.api,
            provider=m.provider,
            model=m.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
        final = AssistantMessage(
            content=list(calls),
            api=m.api,
            provider=m.provider,
            model=m.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
        events = [StartEvent(partial=partial)]
        for index, tool_call in enumerate(calls):
            events.append(ToolcallStartEvent(content_index=index, partial=partial))
            events.append(ToolcallEndEvent(content_index=index, tool_call=tool_call, partial=partial))
        events.append(DoneEvent(reason="toolUse", message=final))
        return events

    register_api_provider(create_faux_provider(script))

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"echo:{args['text']}")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=echo_execute,
    )

    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        AgentContext(system_prompt="sys", messages=[], tools=[echo]),
        AgentLoopConfig(model=model, convert_to_llm=convert),
        renderer.handle_event,
    )

    components = renderer._tool_components
    assert set(components) == {"call_1", "call_2", "call_3"}
    assert {
        call_id for call_id, component in components.items() if component.result is not None
    } == {"call_1", "call_2", "call_3"}

def test_interactive_renderer_skips_non_visual_turn_events() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    renderer = InteractiveRenderer(tui)
    render_calls = {"n": 0}
    original_request_render = tui.request_render

    def counting_request_render(*args, **kwargs):
        render_calls["n"] += 1
        return original_request_render(*args, **kwargs)

    tui.request_render = counting_request_render

    message = _assistant("Visible reply")
    renderer.handle_event(MessageStartEvent(message=message))
    renderer.handle_event(MessageEndEvent(message=message))
    calls_after_visible_reply = render_calls["n"]

    renderer.handle_event(TurnEndEvent(message=message, tool_results=[]))
    renderer.handle_event(AgentEndEvent(messages=[message]))

    assert render_calls["n"] == calls_after_visible_reply

def test_interactive_renderer_ignores_dict_subagent_events() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    renderer = InteractiveRenderer(tui)
    render_calls = {"n": 0}
    original_request_render = tui.request_render

    def counting_request_render(*args, **kwargs):
        render_calls["n"] += 1
        return original_request_render(*args, **kwargs)

    tui.request_render = counting_request_render

    renderer.handle_event(
        {
            "type": "subagent_tool_start",
            "role": "reviewer",
            "toolName": "read",
            "status": "started",
        }
    )

    assert render_calls["n"] == 0

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
    assert "→ Beta" in rendered
    assert "(2/3)" in rendered
    select.set_filter("ga")
    assert select.render(40)[0].startswith("→ Gamma")
    cancelled: list[bool] = []
    select.on_cancel = lambda: cancelled.append(True)
    select.set_filter("none")
    select.handle_input("\x1b")
    assert cancelled == [True]

    footer = FooterComponent(cwd="/tmp/project", model="faux-model", thinking_level="high", pending=2)
    assert footer.render(25) == ["/tmp/project", "0.0%/0 (auto)  faux-model"]
    footer = FooterComponent(
        cwd="/tmp/project",
        model="faux-model",
        provider="faux",
        context_tokens=1200,
        context_threshold=16000,
        context_window=16000,
        compression_count=2,
        available_provider_count=2,
        git_branch="main",
        extension_statuses={"plan": "ready\nnow"},
    )
    assert footer.render(35) == [
        "/tmp/project (main)",
        "7.5%/16k (auto)   (faux) faux-model",
        "ready now",
    ]
    footer = FooterComponent(
        cwd="/tmp/project",
        model="faux-model",
        git_branch="main",
        session_name="work session",
    )
    assert footer.render(80)[0] == "/tmp/project (main) • work session"
    footer = FooterComponent(
        cwd="/tmp/project",
        model="faux-model",
        context_window=200000,
        context_percent=12.3,
        total_input=12345,
        total_output=6789,
        total_cache_read=50,
        total_cache_write=50,
        latest_cache_hit_rate=25.0,
        total_cost=1.234,
    )
    assert footer.render(80)[1] == "↑12k ↓6.8k R50 W50 CH25.0% $1.234 12.3%/200k (auto)                   faux-model"
    footer = FooterComponent(
        cwd="/tmp/project",
        model="faux-model",
        context_window=200000,
        context_percent_unknown=True,
    )
    assert footer.render(40)[1] == "?/200k (auto)                 faux-model"
    status = StatusLine("Retrying\nsoon", kind="info")
    assert status.render(40) == ["info: Retrying soon"]

def test_select_list_ports_travis234_ctrl_c_cancel_keybinding() -> None:
    select = SelectList(
        [
            SelectItem(value="alpha", label="Alpha"),
            SelectItem(value="beta", label="Beta"),
        ],
        max_visible=2,
    )
    cancelled: list[bool] = []
    select.on_cancel = lambda: cancelled.append(True)

    select.handle_input("\x03")

    assert cancelled == [True]

def test_select_list_ports_travis234_selection_change_and_public_api() -> None:
    select = SelectList(
        [
            SelectItem(value="alpha", label="Alpha"),
            SelectItem(value="beta", label="Beta"),
            SelectItem(value="gamma", label="Gamma"),
        ],
        max_visible=2,
    )
    changed: list[str] = []
    selected: list[str] = []
    cancelled: list[bool] = []
    select.on_selection_change = lambda item: changed.append(item.value)
    select.on_select = lambda item: selected.append(item.value)
    select.on_cancel = lambda: cancelled.append(True)

    select.handle_input("\x1b[B")
    assert changed == ["beta"]
    assert select.get_selected_item() == SelectItem(value="beta", label="Beta")

    select.set_selected_index(99)
    assert select.get_selected_item() == SelectItem(value="gamma", label="Gamma")

    select.set_filter("be")
    assert select.get_selected_item() == SelectItem(value="beta", label="Beta")

    select.handle_input("\r")
    select.handle_input("\x03")
    assert selected == ["beta"]
    assert cancelled == [True]

def test_select_list_ports_travis234_display_value_and_description_normalization() -> None:
    select = SelectList(
        [
            SelectItem(value="alpha", label="", description="line one\nline two"),
        ],
        max_visible=1,
    )

    assert select.render(80) == ["→ alpha" + (" " * 27) + "line one line two"]

def test_select_list_ports_travis234_layout_alignment_and_truncation_hook() -> None:
    seen_contexts: list[dict[str, object]] = []

    def truncate_primary(context: dict[str, object]) -> str:
        seen_contexts.append(context)
        text = str(context["text"])
        max_width = int(context["maxWidth"])
        if len(text) <= max_width:
            return text
        return text[: max(0, max_width - 1)] + "*"

    select = SelectList(
        [
            SelectItem(value="very-long-command-name", label="very-long-command-name", description="first"),
            SelectItem(value="short", label="short", description="second"),
        ],
        max_visible=5,
        layout={
            "minPrimaryColumnWidth": 12,
            "maxPrimaryColumnWidth": 12,
            "truncatePrimary": truncate_primary,
        },
    )

    rendered = select.render(80)

    assert rendered[0].startswith("→ very-long*")
    assert _visible_index_of(rendered[0], "first") == _visible_index_of(rendered[1], "second") == 14
    assert seen_contexts[0]["text"] == "very-long-command-name"
    assert seen_contexts[0]["maxWidth"] == 10
    assert seen_contexts[0]["columnWidth"] == 12
    assert seen_contexts[0]["item"] == SelectItem(
        value="very-long-command-name",
        label="very-long-command-name",
        description="first",
    )
    assert seen_contexts[0]["isSelected"] is True

def test_settings_list_ports_travis234_search_cycle_cancel_and_submenu() -> None:
    set_keybindings(KeybindingsManager(TUI_KEYBINDINGS))
    theme = {
        "label": lambda text, selected: f"<{text}>" if selected else text,
        "value": lambda text, selected: f"[{text}]" if selected else text,
        "description": lambda text: f"desc:{text}",
        "cursor": "->",
        "hint": lambda text: f"hint:{text}",
    }
    changes: list[tuple[str, str]] = []
    cancelled: list[bool] = []
    settings = SettingsList(
        [
            {
                "id": "theme",
                "label": "Theme",
                "description": "Color theme",
                "currentValue": "dark",
                "values": ["dark", "light"],
            },
            {"id": "api", "label": "API key", "currentValue": "unset", "values": ["unset", "set"]},
        ],
        5,
        theme,
        lambda item_id, value: changes.append((item_id, value)),
        lambda: cancelled.append(True),
        {"enableSearch": True},
    )

    rendered = "\n".join(settings.render(48))
    assert "Theme" in rendered
    assert "Color theme" in rendered

    settings.handle_input("\r")
    assert changes == [("theme", "light")]
    assert "light" in "\n".join(settings.render(48))

    settings.update_value("theme", "dark")
    assert "dark" in "\n".join(settings.render(48))

    settings.handle_input("a")
    filtered = "\n".join(settings.render(48))
    assert "API key" in filtered
    assert "Theme" not in filtered

    settings.handle_input("\x1b")
    assert cancelled == [True]

    class Submenu(Component):
        def __init__(self, done) -> None:
            self.done = done

        def render(self, width: int) -> list[str]:
            return ["submenu"]

        def handle_input(self, data: str) -> None:
            if data == "s":
                self.done("selected")

    submenu_changes: list[tuple[str, str]] = []
    submenu_settings = SettingsList(
        [
            {
                "id": "mode",
                "label": "Mode",
                "currentValue": "auto",
                "submenu": lambda current, done: Submenu(done),
            }
        ],
        3,
        theme,
        lambda item_id, value: submenu_changes.append((item_id, value)),
        lambda: None,
    )
    submenu_settings.handle_input("\r")
    assert submenu_settings.render(40) == ["submenu"]
    submenu_settings.handle_input("s")
    assert submenu_changes == [("mode", "selected")]
    assert "selected" in "\n".join(submenu_settings.render(40))

def test_input_ports_travis234_line_movement_and_kill_yank_keybindings() -> None:
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

def test_input_ports_travis234_on_escape_cancel_keybinding() -> None:
    input_component = Input(value="draft")
    cancelled: list[str] = []
    input_component.on_escape = lambda: cancelled.append("escape")

    input_component.handle_input("\x1b")

    assert cancelled == ["escape"]
    assert input_component.get_value() == "draft"

    input_component.on_escape = None
    input_component.on_escape = lambda: cancelled.append("ctrl+c")
    input_component.handle_input("\x03")

    assert cancelled == ["escape", "ctrl+c"]
    assert input_component.get_value() == "draft"

def test_input_ports_travis234_line_kill_and_yank_pop_keybindings() -> None:
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

def test_input_render_scrolls_to_cursor_and_uses_travis234_fake_cursor() -> None:
    input_component = Input(value="abcdefghijklmnopqrstuvwxyz", prompt="> ")
    input_component.focused = True
    input_component.cursor = len(input_component.value)

    rendered = input_component.render(12)[0]
    plain = strip_ansi(rendered)

    assert visible_width(rendered) <= 12
    assert "\x1b_travis234:c\x07" in rendered
    assert "\x1b[7m \x1b[27m" in rendered
    assert "z" in plain
    assert "abc" not in plain

def test_input_ports_travis234_grapheme_cursor_and_delete_behavior() -> None:
    input_component = Input()
    input_component.set_value("a👨‍💻b")

    input_component.handle_input("\x1b[D")
    assert input_component.cursor == len("a👨‍💻")
    input_component.handle_input("\x1b[D")
    assert input_component.cursor == len("a")

    input_component.handle_input("\x1b[C")
    assert input_component.cursor == len("a👨‍💻")

    input_component.handle_input("\x7f")
    assert input_component.get_value() == "ab"
    assert input_component.cursor == len("a")

    input_component.set_value("a👨‍💻b")
    input_component.cursor = len("a")
    input_component.handle_input("\x1b[3~")
    assert input_component.get_value() == "ab"
    assert input_component.cursor == len("a")

def test_input_ports_travis234_up_down_prompt_history_navigation() -> None:
    input_component = Input(value="draft")
    input_component.add_to_history("first")
    input_component.add_to_history("second")
    input_component.add_to_history("second")

    input_component.handle_input("\x1b[A")
    assert input_component.get_value() == "second"
    assert input_component.cursor == 0

    input_component.handle_input("\x1b[A")
    assert input_component.get_value() == "first"
    assert input_component.cursor == 0

    input_component.handle_input("\x1b[B")
    assert input_component.get_value() == "second"
    assert input_component.cursor == len("second")

    input_component.handle_input("\x1b[B")
    assert input_component.get_value() == "draft"
    assert input_component.cursor == len("draft")

def test_input_ignores_mouse_reports_that_reach_prompt_editor() -> None:
    input_component = Input(value="draft")
    input_component.cursor = len("draft")

    input_component.handle_input("\x1b[<64;1;1M\x1b[<64;1;1m")
    input_component.handle_input("\x1b[64;1;1M")
    input_component.handle_input("\x1b[M`!!")

    assert input_component.get_value() == "draft"
    assert input_component.cursor == len("draft")

def test_input_ignores_leaked_mouse_report_fragments_that_reach_prompt_editor() -> None:
    input_component = Input(value="draft")
    input_component.cursor = len("draft")

    input_component.handle_input("[<64;1;1M")
    input_component.handle_input("<65;1;1M")
    input_component.handle_input("^[[<64;1;1m")
    input_component.handle_input("[M`!!")

    assert input_component.get_value() == "draft"
    assert input_component.cursor == len("draft")

def test_input_buffers_incremental_leaked_mouse_report_fragments() -> None:
    input_component = Input(value="draft")
    input_component.cursor = len("draft")

    for char in "[<65;1;1M":
        input_component.handle_input(char)
    for char in "clean":
        input_component.handle_input(char)

    assert input_component.get_value() == "draftclean"
    assert input_component.cursor == len("draftclean")

def test_input_mask_hides_value_during_render_but_preserves_submitted_value() -> None:
    input_component = Input(prompt="Enter API key: ", mask=True)
    input_component.focused = True

    input_component.handle_input("typed-secret")

    rendered = strip_ansi("".join(input_component.render(80)))
    assert input_component.get_value() == "typed-secret"
    assert "typed-secret" not in rendered
    assert "*" * len("typed-secret") in rendered

def test_input_ports_travis234_alt_d_delete_word_forward_keybinding() -> None:
    input_component = Input()
    input_component.set_value("hello world")
    input_component.handle_input("\x01")

    input_component.handle_input("\x1bd")
    assert input_component.get_value() == " world"

    input_component.handle_input("\x1bd")
    assert input_component.get_value() == ""

    input_component.handle_input("\x19")
    assert input_component.get_value() == "hello world"

def test_input_ports_travis234_alt_delete_delete_word_forward_keybinding() -> None:
    input_component = Input()
    input_component.set_value("hello world")
    input_component.handle_input("\x01")

    input_component.handle_input("\x1b[3;3~")
    assert input_component.get_value() == " world"
    assert input_component.cursor == 0

    input_component.handle_input("\x1b[3;3:1~")
    assert input_component.get_value() == ""
    assert input_component.cursor == 0

    input_component.handle_input("\x19")
    assert input_component.get_value() == "hello world"

def test_input_ports_travis234_bracketed_paste_sanitization() -> None:
    input_component = Input()
    input_component.set_value("prefixsuffix")
    input_component.cursor = len("prefix")

    input_component.handle_input("\x1b[200~one\r\ntwo\tthree\n\x1b[201~")

    assert input_component.get_value() == "prefixonetwo    threesuffix"
    assert input_component.cursor == len("prefixonetwo    three")

def test_input_ports_travis234_delete_key_forward_deletion() -> None:
    input_component = Input()
    input_component.set_value("hello")
    input_component.cursor = 1

    input_component.handle_input("\x1b[3~")
    assert input_component.get_value() == "hllo"
    assert input_component.cursor == 1

    input_component.cursor = len(input_component.get_value())
    input_component.handle_input("\x1b[3~")
    assert input_component.get_value() == "hllo"

def test_input_ports_travis234_ctrl_b_ctrl_f_cursor_navigation() -> None:
    input_component = Input()
    input_component.set_value("hello")

    input_component.handle_input("\x02")
    assert input_component.cursor == len("hell")

    input_component.handle_input("\x02")
    input_component.handle_input("\x02")
    input_component.handle_input("\x02")
    input_component.handle_input("\x02")
    assert input_component.cursor == 0

    input_component.handle_input("\x06")
    assert input_component.cursor == 1

    for _ in range(10):
        input_component.handle_input("\x06")
    assert input_component.cursor == len("hello")

def test_input_ports_travis234_alternate_home_end_key_sequences() -> None:
    for sequence in ("\x1bOH", "\x1b[1~", "\x1b[7~"):
        input_component = Input()
        input_component.set_value("hello")
        input_component.cursor = len("he")

        input_component.handle_input(sequence)

        assert input_component.get_value() == "hello"
        assert input_component.cursor == 0

    for sequence in ("\x1bOF", "\x1b[4~", "\x1b[8~"):
        input_component = Input()
        input_component.set_value("hello")
        input_component.cursor = len("he")

        input_component.handle_input(sequence)

        assert input_component.get_value() == "hello"
        assert input_component.cursor == len("hello")

def test_input_ports_travis234_ctrl_d_delete_char_forward_keybinding() -> None:
    input_component = Input()
    input_component.set_value("hello")
    input_component.cursor = 1

    input_component.handle_input("\x04")
    assert input_component.get_value() == "hllo"
    assert input_component.cursor == 1

    input_component.cursor = len(input_component.get_value())
    input_component.handle_input("\x04")
    assert input_component.get_value() == "hllo"

def test_input_ports_travis234_ctrl_minus_undo_for_typing_and_delete() -> None:
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

def test_input_ports_travis234_alt_b_alt_f_word_navigation() -> None:
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

def test_input_ports_travis234_modified_arrow_word_navigation() -> None:
    input_component = Input()
    input_component.set_value("alpha beta gamma")

    input_component.handle_input("\x1b[1;3D")
    assert input_component.get_value() == "alpha beta gamma"
    assert input_component.cursor == len("alpha beta ")

    input_component.handle_input("\x1b[1;5D")
    assert input_component.cursor == len("alpha ")

    input_component.handle_input("\x1b[1;3C")
    assert input_component.get_value() == "alpha beta gamma"
    assert input_component.cursor == len("alpha beta")

    input_component.handle_input("\x1b[1;5C")
    assert input_component.cursor == len("alpha beta gamma")

def test_input_ports_travis234_alt_backspace_delete_word_backward() -> None:
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
    from travis.tui import AssistantMessageComponent

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
    assert "to expand" in collapsed.replace("\n", " ")

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

def test_tool_execution_accepts_component_render_call_like_travis234() -> None:
    long_path = "/workspace/demo_okf_bundle/spec/final-important-suffix.md"

    definition = ToolDefinition(
        name="write",
        label="Write",
        description="Write file",
        parameters={},
        execute=lambda *args, **kwargs: AgentToolResult(content=[]),
        render_call=lambda args, ctx: Text(f"write {args['path']}"),
    )
    component = ToolExecutionComponent(
        "write",
        "call-1",
        {"path": long_path},
        tool_definition=definition,
        cwd="/workspace",
    )

    rendered = component.render(24)
    joined = "\n".join(rendered)

    assert "suffix.md" in joined
    assert all(visible_width(line) <= 24 for line in rendered)

def test_tool_execution_long_call_header_stays_single_stable_line() -> None:
    long_path = "/workspace/demo_okf_bundle/spec/very/deep/final-important-suffix.md"

    definition = ToolDefinition(
        name="write",
        label="Write",
        description="Write file",
        parameters={},
        execute=lambda *args, **kwargs: AgentToolResult(content=[]),
        render_call=lambda args, ctx: Text(f"write {args['path']}"),
    )
    component = ToolExecutionComponent(
        "write",
        "call-1",
        {"path": long_path},
        tool_definition=definition,
        cwd="/workspace",
    )

    rendered = component.render(32)

    assert len(rendered) == 1
    assert "suffix.md" in rendered[0]
    assert "very/deep" not in rendered[0]
    assert visible_width(rendered[0]) <= 32

def test_tool_execution_accepts_component_render_result_like_travis234() -> None:
    definition = ToolDefinition(
        name="read",
        label="Read",
        description="Read file",
        parameters={},
        execute=lambda *args, **kwargs: AgentToolResult(content=[]),
        render_call=lambda args, ctx: f"read {args['path']}",
        render_result=lambda result, metadata, context: Text(
            "[ok] first wrapped result line with final-important-suffix.md"
        ),
    )
    component = ToolExecutionComponent(
        "read",
        "call-1",
        {"path": "notes.md"},
        tool_definition=definition,
        cwd="/workspace",
    )
    component.update_result(AgentToolResult(content=[]), is_error=False)

    rendered = component.render(28)
    joined = "\n".join(rendered)

    assert "final-important-suffix.md" in joined
    assert all(visible_width(line) <= 28 for line in rendered)

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

def test_tool_execution_collapses_huge_single_line_generic_result_before_rendering() -> None:
    component = ToolExecutionComponent("huge", {})
    result = AgentToolResult(content=[TextContent(text="x" * 80_000)], details=None)

    component.update_result(result, is_error=False)
    rendered = "\n".join(component.render(80))

    assert "more chars, to expand" in rendered
    assert len(rendered) < 8_000

def test_tool_execution_fallback_never_renders_process_stdin_payload() -> None:
    component = ToolExecutionComponent(
        "process",
        {
            "action": "write",
            "session_id": "proc_0123456789abcdef",
            "input": "TOP-SECRET-PAYLOAD",
        },
    )

    rendered = "\n".join(component.render(80))

    assert "process write proc_01234567" in rendered
    assert "TOP-SECRET-PAYLOAD" not in rendered

def test_tool_execution_uses_process_definition_renderer_for_wait_metadata() -> None:
    definition = SimpleNamespace(
        render_call=lambda args, ctx: (
            f"process {args['action']} {args['session_id'][:13]} "
            f"cursor={args['cursor']} wait={args['wait_time_ms']}ms"
        ),
        render_result=None,
    )
    component = ToolExecutionComponent(
        "process",
        {
            "action": "wait",
            "session_id": "proc_0123456789abcdef",
            "cursor": 8,
            "wait_time_ms": 60_000,
        },
        tool_definition=definition,
    )

    rendered = "\n".join(component.render(100))

    assert "process wait proc_01234567 cursor=8 wait=60000ms" in rendered

def test_tool_execution_renders_stable_running_process_marker() -> None:
    component = ToolExecutionComponent("bash", {"command": "sleep 30"})
    component.update_result(
        AgentToolResult(
            content=[TextContent(text="START\n")],
            details={
                "status": "running",
                "sessionId": "proc_0123456789abcdef0123456789abcdef",
            },
        ),
        is_error=False,
    )

    rendered = "\n".join(component.render(80))

    assert "running: proc_01234567" in rendered

def test_user_and_skill_invocation_components_render_like_travis234() -> None:
    from travis.tui import SkillInvocationMessageComponent, UserMessageComponent, parse_skill_block

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
    from travis.tui import message_to_component

    message = UserMessage(
        content=(
            '<skill name="tui" location="/skills/tui/SKILL.md">\n'
            "Render with boxes.\n"
            "</skill>\n\n"
            "Now update travis."
        ),
        timestamp=now_ms(),
    )

    component = message_to_component(message)
    assert component is not None
    rendered = strip_ansi("\n".join(component.render(100)))

    assert "[skill] tui" in rendered
    assert "Render with boxes." not in rendered
    assert "Now update travis." in rendered
    assert "> Now update" not in rendered

def test_bash_execution_component_renders_status_and_output() -> None:
    from travis.tui import BashExecutionComponent, message_to_component

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
    from travis.tui import (
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

def test_interactive_mode_renders_live_custom_message_with_extension_renderer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_message_renderer(
        "context",
        lambda message, options=None, theme=None: Text(f"live custom rendered: {message.content}"),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    app.session.send_custom_message({"customType": "context", "content": "Fresh extension context", "display": True})

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert "live custom rendered: Fresh extension context" in rendered
    assert "[context]" not in rendered

def test_interactive_mode_runs_agents_command_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/agents", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "No subagents have been spawned" in rendered

def test_interactive_mode_runs_help_command_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/help", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "TUI commands" in rendered
    assert "/model" in rendered
    assert "model should not run" not in rendered
