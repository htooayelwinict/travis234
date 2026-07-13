from __future__ import annotations

from tests._support_tui import *  # noqa: F403


def test_visible_width_strips_ansi() -> None:
    assert visible_width("\x1b[31mred\x1b[0m") == 3
    assert visible_width("plain") == 5

def test_visible_width_ports_travis234_tabs_wide_unicode_and_apc() -> None:
    assert visible_width("\t\x1b[31m界\x1b[0m") == 5
    assert visible_width("🙂界") == 4
    assert visible_width("a\x1b_travis234:c\x07b") == 2

def test_fuzzy_match_ports_travis234_scoring_and_swapped_model_tokens() -> None:
    assert fuzzy_match("", "anything").matches is True
    assert fuzzy_match("", "anything").score == 0
    assert fuzzy_match("longquery", "short").matches is False
    assert fuzzy_match("abc", "aXbXc").matches is True
    assert fuzzy_match("abc", "cba").matches is False
    assert fuzzy_match("ABC", "abc").matches is True

    consecutive = fuzzy_match("foo", "foobar")
    scattered = fuzzy_match("foo", "f_o_o_bar")
    assert consecutive.matches is True
    assert scattered.matches is True
    assert consecutive.score < scattered.score

    at_boundary = fuzzy_match("fb", "foo-bar")
    not_at_boundary = fuzzy_match("fb", "afbx")
    assert at_boundary.matches is True
    assert not_at_boundary.matches is True
    assert at_boundary.score < not_at_boundary.score

    assert fuzzy_match("codex52", "gpt-5.2-codex").matches is True

def test_fuzzy_filter_ports_travis234_tokenized_sorting_and_custom_text() -> None:
    assert fuzzy_filter(["apple", "banana", "cherry"], "", lambda value: value) == ["apple", "banana", "cherry"]
    assert fuzzy_filter(["apple", "banana", "cherry"], "an", lambda value: value) == ["banana"]
    assert fuzzy_filter(["a_p_p", "app", "application"], "app", lambda value: value)[0] == "app"
    assert fuzzy_filter(["clone", "cl"], "cl", lambda value: value) == ["cl", "clone"]

    items = [
        {"name": "foo", "id": 1},
        {"name": "bar", "id": 2},
        {"name": "foobar", "id": 3},
    ]
    filtered = fuzzy_filter(items, "foo", lambda item: item["name"])
    assert [item["name"] for item in filtered] == ["foo", "foobar"]

    model = {"id": "gpt-5.5", "provider": "openai-codex"}
    assert fuzzy_filter([model], "openai-codex/gpt-5.5", lambda item: f"{item['id']} {item['provider']}") == [model]

def test_footer_ports_travis234_home_path_formatting() -> None:
    assert format_cwd_for_footer("/home/user2", "/home/user") == "/home/user2"
    assert format_cwd_for_footer("/home/user", "/home/user") == "~"
    assert format_cwd_for_footer("/home/user/project", "/home/user") == "~/project"

    footer = FooterComponent(cwd="/home/user/project", model="faux-model", home="/home/user")
    assert footer.render(80)[0] == "~/project"

def test_interactive_mode_footer_shows_history_hint_only_when_scrolled_up(tmp_path) -> None:
    terminal = FakeTerminal(columns=80, rows=6)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    for index in range(12):
        mode.history.add(Text(f"history line {index}"))
    app.tui.request_render()

    app.tui.scroll_by(-3)
    mode._refresh_footer()

    scrolled_footer = "\n".join(mode.footer.render(80))
    assert "history" in scrolled_footer
    assert "End to latest" in scrolled_footer

    app.tui.scroll_to_bottom()
    mode._refresh_footer()

    bottom_footer = "\n".join(mode.footer.render(80))
    assert "End to latest" not in bottom_footer

def test_interactive_mode_footer_history_hint_updates_from_scroll_input(tmp_path) -> None:
    terminal = FakeTerminal(columns=80, rows=6)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    for index in range(12):
        mode.history.add(Text(f"history line {index}"))
    app.tui.request_render()

    assert terminal.input_handler is not None
    terminal.input_handler("\x1b[5~")

    scrolled_footer = "\n".join(mode.footer.render(80))
    assert "End to latest" in scrolled_footer

    terminal.input_handler("\x1b[F")

    bottom_footer = "\n".join(mode.footer.render(80))
    assert "End to latest" not in bottom_footer

def test_interactive_mode_collapses_subagent_tool_noise_but_keeps_lifecycle(tmp_path) -> None:
    terminal = FakeTerminal(columns=100, rows=12)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode._handle_session_event(
        {
            "type": "subagent_start",
            "child_role": "explorer",
            "child_subagent_id": "subagent-fixed",
            "child_goal": "scan docs",
        }
    )
    mode._handle_session_event(
        {
            "type": "subagent_tool_start",
            "role": "explorer",
            "toolName": "read",
            "status": "started",
            "argsPreview": "huge.md",
        }
    )
    mode._handle_session_event(
        {
            "type": "subagent_tool_end",
            "role": "explorer",
            "toolName": "read",
            "status": "ok",
            "argsPreview": "huge.md",
            "resultPreview": "NOISY RESULT " * 20,
        }
    )
    mode._handle_session_event(
        {
            "type": "subagent_tool_guardrail",
            "role": "explorer",
            "toolName": "ls",
            "status": "guardrail_halt",
            "guardrailCode": "idempotent_no_progress_block",
        }
    )
    mode._handle_session_event(
        {
            "type": "subagent_stop",
            "child_role": "explorer",
            "child_subagent_id": "subagent-fixed",
            "status": "failed",
            "child_summary": "guardrail summary",
        }
    )

    rendered = "\n".join(mode.history.render(100))
    assert "subagent explorer started subagent-fixed" in rendered
    assert "subagent explorer failed subagent-fixed" in rendered
    assert "guardrail idempotent_no_progress_block" in rendered
    assert "NOISY RESULT" not in rendered
    assert "huge.md" not in rendered

def test_simple_autocomplete_provider_ports_travis234_fuzzy_command_filtering() -> None:
    provider = SimpleAutocompleteProvider(
        [
            {"name": "clear-cache", "description": "Clear cache", "argumentHint": "<scope>"},
            {"value": "commit", "description": "Commit changes"},
            {"name": "model", "description": "Switch model"},
        ]
    )

    fuzzy_result = provider.get_suggestions(["/cc"], 0, len("/cc"))
    assert fuzzy_result is not None
    assert fuzzy_result["prefix"] == "/cc"
    assert fuzzy_result["items"][0] == {
        "value": "clear-cache",
        "label": "clear-cache",
        "description": "<scope> — Clear cache",
    }

    item_result = provider.get_suggestions(["/cm"], 0, len("/cm"))
    assert item_result is not None
    assert item_result["items"][0]["value"] == "commit"

def test_combined_autocomplete_provider_ports_travis234_commands_files_and_attachments(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "notes file.md").write_text("notes", encoding="utf-8")

    provider = CombinedAutocompleteProvider(
        [{"name": "compact", "description": "Compress context", "argumentHint": "[focus]"}],
        str(tmp_path),
    )

    command_suggestions = provider.get_suggestions(["/co"], 0, 3, {"signal": None, "force": False})
    assert command_suggestions["prefix"] == "/co"
    assert command_suggestions["items"][0] == {
        "value": "compact",
        "label": "compact",
        "description": "[focus] — Compress context",
    }
    applied_command = provider.apply_completion(["/co"], 0, 3, command_suggestions["items"][0], "/co")
    assert applied_command == {"lines": ["/compact "], "cursorLine": 0, "cursorCol": len("/compact ")}

    path_suggestions = provider.get_suggestions(["open src/"], 0, len("open src/"), {"signal": None, "force": False})
    assert path_suggestions["prefix"] == "src/"
    assert path_suggestions["items"][:2] == [
        {"value": "src/pkg/", "label": "pkg/"},
        {"value": "src/app.py", "label": "app.py"},
    ]

    at_suggestions = provider.get_suggestions(['@"notes'], 0, len('@"notes'), {"signal": None, "force": True})
    assert at_suggestions["prefix"] == '@"notes'
    assert at_suggestions["items"][0] == {"value": '@"notes file.md"', "label": "notes file.md"}
    applied_at = provider.apply_completion(['@"notes'], 0, len('@"notes'), at_suggestions["items"][0], '@"notes')
    assert applied_at == {"lines": ['@"notes file.md" '], "cursorLine": 0, "cursorCol": len('@"notes file.md" ')}

    assert provider.should_trigger_file_completion(["/compact"], 0, len("/compact")) is False
    assert provider.should_trigger_file_completion(["/compact s"], 0, len("/compact s")) is True

def test_stdin_buffer_ports_travis234_split_sequences_and_kitty_regressions() -> None:
    buffer = StdinBuffer({"timeout": 10})
    emitted: list[str] = []
    buffer.on("data", emitted.append)

    buffer.process("abc\x1b[A")
    assert emitted == ["a", "b", "c", "\x1b[A"]

    buffer.process("\x1b[<3")
    buffer.process("5;1")
    buffer.process("5;")
    assert buffer.get_buffer() == "\x1b[<35;15;"
    buffer.process("10m")
    assert emitted[-1] == "\x1b[<35;15;10m"

    emitted.clear()
    buffer.process("\x1b\x1b[27;129:3u")
    assert emitted == ["\x1b", "\x1b[27;129:3u"]

    emitted.clear()
    buffer.process("\x1b[64u")
    buffer.process("@")
    assert emitted == ["\x1b[64u"]

def test_stdin_buffer_ports_travis234_bracketed_paste_events() -> None:
    buffer = StdinBuffer({"timeout": 10})
    emitted: list[str] = []
    pasted: list[str] = []
    buffer.on("data", emitted.append)
    buffer.on("paste", pasted.append)

    buffer.process("a")
    buffer.process("\x1b[200~hello ")
    buffer.process("world\x1b[201~")
    buffer.process("b")

    assert emitted == ["a", "b"]
    assert pasted == ["hello world"]

def test_parse_osc11_background_color_ports_travis234_formats() -> None:
    assert parse_osc11_background_color("\x1b]11;rgb:0000/8000/ffff\x07") == {"r": 0, "g": 128, "b": 255}
    assert parse_osc11_background_color("\x1b]11;#ffffff\x1b\\") == {"r": 255, "g": 255, "b": 255}
    assert parse_osc11_background_color("\x1b]11;#000000\x07") == {"r": 0, "g": 0, "b": 0}
    assert parse_osc11_background_color("x\x1b]11;#ffffff\x07") is None
    assert parse_osc11_background_color("\x1b]10;#ffffff\x07") is None
    assert parse_osc11_background_color("\x1b]11;#ffffff\x07x") is None

def test_tui_query_terminal_background_color_consumes_osc11_response() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    seen_inputs: list[str] = []
    tui.add_input_listener(lambda data: seen_inputs.append(data))
    tui.start()
    try:
        query = tui.query_terminal_background_color({"timeout_ms": 1000})
        assert "\x1b]11;?\x07" in terminal.writes
        assert terminal.input_handler is not None
        terminal.input_handler("\x1b]11;#ffffff\x07")
        assert query.result(timeout=1) == {"r": 255, "g": 255, "b": 255}
        assert seen_inputs == []
    finally:
        tui.stop()

def test_tui_query_terminal_background_color_ports_travis234_reply_edges() -> None:
    class Recorder(Component):
        def __init__(self) -> None:
            self.events: list[str] = []

        def render(self, width: int) -> list[str]:
            return ["recorder"]

        def handle_input(self, data: str) -> None:
            self.events.append(data)

    terminal = FakeTerminal()
    tui = TUI(terminal)
    recorder = Recorder()
    seen_inputs: list[str] = []
    tui.add(recorder)
    tui.set_focus(recorder)
    tui.add_input_listener(lambda data: seen_inputs.append(data))
    tui.start()

    try:
        invalid_query = tui.query_terminal_background_color({"timeoutMs": 1000})
        assert terminal.input_handler is not None
        terminal.input_handler("\x1b]11;not-a-color\x07")
        assert invalid_query.result(timeout=1) is None
        assert seen_inputs == []
        assert recorder.events == []

        pass_through_query = tui.query_terminal_background_color({"timeout_ms": 1000})
        terminal.input_handler("x")
        assert pass_through_query.done() is False
        assert seen_inputs == ["x"]
        assert recorder.events == ["x"]
        terminal.input_handler("\x1b]11;#ffffff\x07")
        assert pass_through_query.result(timeout=1) == {"r": 255, "g": 255, "b": 255}

        timeout_query = tui.query_terminal_background_color({"timeout_ms": 1})
        assert timeout_query.result(timeout=1) is None
        time.sleep(0.01)
        terminal.input_handler("\x1b]11;#000000\x07")
        assert seen_inputs == ["x"]
        assert recorder.events == ["x"]
    finally:
        tui.stop()

def test_keybindings_manager_ports_travis234_defaults_conflicts_and_globals() -> None:
    keybindings = KeybindingsManager(
        TUI_KEYBINDINGS,
        {
            "tui.input.submit": ["enter", "ctrl+enter"],
            "tui.select.confirm": "ctrl+x",
            "tui.input.copy": "ctrl+x",
        },
    )

    assert keybindings.get_keys("tui.input.submit") == ["enter", "ctrl+enter"]
    assert keybindings.get_keys("tui.editor.cursorLeft") == ["left", "ctrl+b"]
    assert keybindings.get_conflicts() == [
        {"key": "ctrl+x", "keybindings": ["tui.select.confirm", "tui.input.copy"]}
    ]
    assert keybindings.matches("\x1b[D", "tui.editor.cursorLeft") is True

    set_keybindings(keybindings)
    assert get_keybindings() is keybindings

def test_keys_ports_travis234_parse_match_and_release_surface() -> None:
    from travis.tui.keys import is_key_release, matches_key, parse_key

    assert parse_key("\x03") == "ctrl+c"
    assert parse_key("\r") == "enter"
    assert parse_key("\n") == "enter"
    assert parse_key("\t") == "tab"
    assert parse_key("\x7f") == "backspace"
    assert parse_key("\x1bb") == "alt+b"
    assert parse_key("\x1b[A") == "up"
    assert parse_key("\x1b[1;5D") == "ctrl+left"
    assert parse_key("\x1b[97;5u") == "ctrl+a"
    assert parse_key("\x1b[97;1:3u") == "a"

    assert matches_key("\x03", "ctrl+c") is True
    assert matches_key("\x1b[1;5D", "ctrl+left") is True
    assert matches_key("\x1b[97;5u", "ctrl+a") is True
    assert matches_key("\x1b[97;1:3u", "a") is True

    assert is_key_release("\x1b[97;1:3u") is True
    assert is_key_release("\x1b[200~90:62:3F:A5\x1b[201~") is False

def test_tui_package_exports_travis234_key_helpers() -> None:
    from travis.tui import (
        is_key_release,
        matches_key,
        parse_key,
    )

    assert parse_key("\x03") == "ctrl+c"
    assert parse_key("\x1bb") == "alt+b"
    assert matches_key("\x1b[A", "up") is True
    assert matches_key("\x1b[97;5u", "ctrl+a") is True
    assert is_key_release("\x1b[97;1:3u") is True
    assert is_key_release("\x1b[200~90:62:3F:A5\x1b[201~") is False

def test_keys_ports_travis234_key_object_kitty_state_and_repeat_surface() -> None:
    from travis.tui.keys import (
        Key,
        is_key_repeat,
        is_kitty_protocol_active,
        matches_key,
        set_kitty_protocol_active,
    )

    set_kitty_protocol_active(False)
    assert is_kitty_protocol_active() is False
    set_kitty_protocol_active(True)
    assert is_kitty_protocol_active() is True

    assert Key.escape == "escape"
    assert Key.backtick == "`"
    assert Key.ctrl("c") == "ctrl+c"
    assert Key.ctrl_shift("p") == "ctrl+shift+p"
    assert Key.alt_super("?") == "alt+super+?"
    assert matches_key("\x1b[112;6u", Key.ctrl_shift("p")) is True

    assert is_key_repeat("\x1b[97;1:2u") is True
    assert is_key_repeat("\x1b[200~90:62:2F:A5\x1b[201~") is False

def test_keys_and_utils_export_travis234_canonical_helpers() -> None:
    assert decode_kitty_printable("\x1b[97u") == "a"
    assert decode_kitty_printable("\x1b[97:65:97;2u") == "A"
    assert decode_kitty_printable("\x1b[97;5u") is None

    assert visible_width("\x1b[31mred\x1b[0m") == 3
    truncated = truncate_to_width("abcdef", 4)
    assert truncated == "abcd\x1b[0m"
    assert visible_width(truncated) == 4
    assert wrap_text("\x1b[31mhello world\x1b[0m", 6) == ["\x1b[31mhello", "world\x1b[0m"]

def test_tui_package_exports_travis234_extended_key_helpers() -> None:
    from travis.tui import Key, is_key_repeat, is_kitty_protocol_active, set_kitty_protocol_active

    set_kitty_protocol_active(False)
    assert is_kitty_protocol_active() is False
    assert Key.ctrl_alt("x") == "ctrl+alt+x"
    assert is_key_repeat("\x1b[120;1:2u") is True

def test_truncate_to_width_passes_ansi() -> None:
    assert truncate_to_width("hello world", 5) == "hello\x1b[0m"
    styled = "\x1b[31mhello world\x1b[0m"
    assert visible_width(truncate_to_width(styled, 5)) == 5

def test_truncate_to_width_streams_very_large_unicode_input() -> None:
    truncated = truncate_to_width("🙂界" * 100_000, 40, "…")

    assert visible_width(truncated) <= 40
    assert truncated.endswith("…\x1b[0m")

def test_truncate_to_width_ports_travis234_no_ellipsis_reset() -> None:
    truncated = truncate_to_width(f"\x1b[31m{'hello' * 100}", 10, "")

    assert visible_width(truncated) <= 10
    assert truncated.endswith("\x1b[0m")

def test_truncate_to_width_ports_travis234_wide_character_boundaries() -> None:
    assert truncate_to_width("🙂界abc", 4) == "🙂界\x1b[0m"
    assert truncate_to_width("a\t界", 4) == "a\t\x1b[0m"

def test_truncate_to_width_ports_travis234_optional_ellipsis_and_padding() -> None:
    truncated = truncate_to_width("abcdef", 4, "…")
    assert truncated == "abc\x1b[0m…\x1b[0m"
    assert visible_width(truncated) == 4

    padded = truncate_to_width("🙂界🙂界x", 8, "…", True)
    assert padded == "🙂界🙂\x1b[0m…\x1b[0m "
    assert visible_width(padded) == 8

def test_truncated_text_ports_travis234_padding_truncation_and_first_line_only() -> None:
    padded = TruncatedText("Hello world", 1, 0).render(30)
    assert len(padded) == 1
    assert visible_width(padded[0]) == 30
    assert "Hello world" in padded[0]

    vertical = TruncatedText("Hello", 0, 2).render(12)
    assert len(vertical) == 5
    assert all(visible_width(line) == 12 for line in vertical)

    truncated = TruncatedText("This is a very long first line that needs truncation\nSecond line", 1, 0).render(25)
    assert len(truncated) == 1
    assert visible_width(truncated[0]) == 25
    assert "..." in truncated[0]
    assert "Second line" not in truncated[0]

def test_loader_and_cancellable_loader_port_travis234_rendering_and_escape_cancel() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    loader = Loader(tui, lambda value: f"<{value}>", lambda value: value.upper(), "Working", {"frames": ["*"]})

    try:
        rendered = loader.render(40)
        assert rendered[0] == ""
        assert "* WORKING" in rendered[1]

        aborted: list[bool] = []
        cancellable = CancellableLoader(tui, lambda value: value, lambda value: value, "Working", {"frames": [""]})
        cancellable.on_abort = lambda: aborted.append(True)
        assert cancellable.aborted is False

        cancellable.handle_input("\x1b")

        assert cancellable.signal.aborted is True
        assert cancellable.aborted is True
        assert aborted == [True]
    finally:
        loader.stop()

def test_terminal_image_ports_travis234_capabilities_encoding_dimensions_and_helpers(monkeypatch) -> None:
    reset_capabilities_cache()
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    assert detect_capabilities() == {"images": "kitty", "trueColor": True, "hyperlinks": True}
    assert get_capabilities() == {"images": "kitty", "trueColor": True, "hyperlinks": True}

    set_capabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    set_cell_dimensions({"widthPx": 10, "heightPx": 20})
    assert get_cell_dimensions() == {"widthPx": 10, "heightPx": 20}

    assert encode_kitty("AAAA", {"columns": 2, "rows": 2, "imageId": 42, "moveCursor": False}) == (
        "\x1b_Ga=T,f=100,q=2,C=1,c=2,r=2,i=42;AAAA\x1b\\"
    )
    chunked = encode_kitty("A" * 4100)
    assert ",m=1;" in chunked
    assert "\x1b_Gm=0;" in chunked
    assert delete_kitty_image(42) == "\x1b_Ga=d,d=I,i=42,q=2\x1b\\"
    assert delete_all_kitty_images() == "\x1b_Ga=d,d=A,q=2\x1b\\"

    assert encode_iterm2("AAAA", {"width": 2, "height": "auto", "name": "pixel", "preserveAspectRatio": False}) == (
        "\x1b]1337;File=inline=1;width=2;height=auto;name=cGl4ZWw=;preserveAspectRatio=0:AAAA\x07"
    )

    png_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    gif_2x3 = base64.b64encode(
        b"GIF89a" + (2).to_bytes(2, "little") + (3).to_bytes(2, "little") + b"\x00\x00\x00"
    ).decode("ascii")
    assert get_png_dimensions(png_1x1) == {"widthPx": 1, "heightPx": 1}
    assert get_gif_dimensions(gif_2x3) == {"widthPx": 2, "heightPx": 3}
    assert get_image_dimensions(png_1x1, "image/png") == {"widthPx": 1, "heightPx": 1}

    rendered = render_image("AAAA", {"widthPx": 20, "heightPx": 20}, {
        "maxWidthCells": 2,
        "imageId": 7,
        "moveCursor": False,
    })
    assert rendered == {
        "sequence": "\x1b_Ga=T,f=100,q=2,C=1,c=2,r=1,i=7;AAAA\x1b\\",
        "rows": 1,
        "imageId": 7,
    }
    assert calculate_image_rows({"widthPx": 20, "heightPx": 40}, 2, {"widthPx": 10, "heightPx": 20}) == 2
    assert is_image_line(rendered["sequence"]) is True
    assert hyperlink("Open", "https://example.com") == "\x1b]8;;https://example.com\x1b\\Open\x1b]8;;\x1b\\"
    assert image_fallback("image/png", {"widthPx": 1, "heightPx": 1}, "pixel.png") == (
        "[Image: pixel.png [image/png] 1x1]"
    )
    assert 1 <= allocate_image_id() <= 0xFFFFFFFF

def test_image_component_ports_travis234_fallback_and_kitty_rendering() -> None:
    reset_capabilities_cache()
    set_cell_dimensions({"widthPx": 10, "heightPx": 20})

    set_capabilities({"images": None, "trueColor": False, "hyperlinks": False})
    png_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    fallback = Image(
        png_1x1,
        "image/png",
        {"fallbackColor": lambda value: f"<{value}>"},
        {"filename": "pixel.png", "maxWidthCells": 10},
    )
    assert fallback.render(40) == ["<[Image: pixel.png [image/png] 1x1]>"]

    set_capabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    image = Image(
        "AAAA",
        "image/png",
        {"fallbackColor": lambda value: value},
        {"imageId": 42, "maxWidthCells": 2},
        {"widthPx": 20, "heightPx": 20},
    )

    assert image.get_image_id() == 42
    assert image.render(80) == ["\x1b_Ga=T,f=100,q=2,C=1,c=2,r=1,i=42;AAAA\x1b\\"]

def test_tui_ports_travis234_terminal_image_cell_size_query_and_response() -> None:
    class InvalidatingText(Text):
        def __init__(self, text: str) -> None:
            super().__init__(text)
            self.invalidations = 0

        def invalidate(self) -> None:
            self.invalidations += 1
            super().invalidate()

    reset_capabilities_cache()
    set_capabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    set_cell_dimensions({"widthPx": 9, "heightPx": 18})
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    text = InvalidatingText("ready")
    tui.add(text)
    tui.start()

    try:
        assert "\x1b[16t" in terminal.writes
        assert terminal.input_handler is not None

        terminal.input_handler("\x1b[6;24;12t")

        assert get_cell_dimensions() == {"widthPx": 12, "heightPx": 24}
        assert text.invalidations == 1
    finally:
        tui.stop()

def test_tui_ports_travis234_kitty_image_cleanup_and_image_line_output() -> None:
    set_capabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    terminal = FakeTerminal(columns=80)
    image_line = "\x1b_Ga=T,f=100,q=2,C=1,c=2,r=1,i=42;AAAA\x1b\\"
    text = Text(image_line)
    tui = TUI(terminal)
    tui.add(text)

    tui.request_render()
    assert image_line in terminal.writes[-1]
    assert image_line + "\x1b[0m" not in terminal.writes[-1]

    text.set_text("plain")
    tui.request_render()

    assert delete_kitty_image(42) in terminal.writes[-1]
    assert "plain" in terminal.writes[-1]

def test_tui_ports_travis234_overlay_focus_handle_and_composition() -> None:
    class FocusBox(Component):
        def __init__(self, text: str) -> None:
            self.text = text
            self.focused = False

        def render(self, width: int) -> list[str]:
            return [self.text]

    terminal = FakeTerminal(columns=20, rows=6)
    tui = TUI(terminal)
    tui.add(Text("base"))
    overlay = FocusBox("OV")

    assert is_focusable(overlay) is True
    assert is_focusable(Text("plain")) is False
    assert is_focusable(None) is False

    handle = tui.show_overlay(overlay, {"row": 1, "col": 4, "width": 6})

    assert tui.has_overlay() is True
    assert handle.is_focused() is True
    assert overlay.focused is True
    assert tui.last_render is not None
    assert len(tui.last_render.lines) == 6
    assert tui.last_render.lines[0] == "base"
    assert strip_ansi(tui.last_render.lines[1]).startswith("    OV")

    handle.set_hidden(True)
    assert handle.is_hidden() is True
    assert tui.has_overlay() is False
    assert overlay.focused is False
    assert tui.last_render is not None
    assert "OV" not in "\n".join(tui.last_render.lines)

    handle.set_hidden(False)
    assert handle.is_hidden() is False
    assert handle.is_focused() is True

    handle.unfocus({"target": None})
    assert handle.is_focused() is False
    assert tui.has_overlay() is True

    handle.focus()
    assert handle.is_focused() is True
    tui.hide_overlay()
    assert tui.has_overlay() is False
    assert overlay.focused is False

def test_slice_with_width_ports_travis234_tab_and_wide_boundaries() -> None:
    text = "out 192M\t.travis234/skill-tests/results-ha"
    sliced = slice_with_width(text, 0, 10, strict=True)
    assert sliced == {"text": "out 192M", "width": 8}
    assert visible_width(sliced["text"]) == sliced["width"]

    assert slice_by_column("🙂界abc", 0, 4, strict=True) == "🙂界"
    assert slice_by_column("a🙂b", 1, 2, strict=True) == "🙂"

def test_extract_segments_ports_travis234_tab_width_regression() -> None:
    text = "out 192M\t.travis234/skill-tests/results-ha"
    segments = extract_segments(text, 10, 13, 10, strict_after=True)

    assert segments["before"] == "out 192M\t"
    assert segments["beforeWidth"] == 11
    assert visible_width(segments["before"]) == segments["beforeWidth"]
    assert extract_segments(text, 10, 13, 10, True) == segments

def test_tui_composite_line_ports_travis234_segment_reset_and_style_resume() -> None:
    reset = "\x1b[0m\x1b]8;;\x07"

    line = TUI._composite_line_at("\x1b[31m0123456789ABCD", "XX", 4, 2, 12)

    assert visible_width(line) == 12
    assert line == f"\x1b[31m0123{reset}XX{reset}\x1b[31m6789AB"

def test_wrap_text_wraps_to_width() -> None:
    assert wrap_text("the quick brown fox", 9) == ["the quick", "brown fox"]
    assert wrap_text("", 10) == [""]
    assert wrap_text("abcdefghij", 4) == ["abcd", "efgh", "ij"]

def test_text_component_caches_and_wraps() -> None:
    text = Text("a b c d e")
    assert text.render(3) == ["a b", "c d", "e"]

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

def test_tui_scrolls_transcript_viewport_without_blank_cutoff() -> None:
    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(10):
        tui.add(Text(f"line {index}"))

    bottom = tui.request_render()
    assert bottom.lines == ["line 6", "line 7", "line 8", "line 9"]

    assert tui.scroll_by(-2) == -2
    scrolled_up = tui.request_render()

    assert scrolled_up.lines == ["line 4", "line 5", "line 6", "line 7"]
    assert all(line for line in scrolled_up.lines)

    assert tui.scroll_by(99) == 2
    back_at_bottom = tui.request_render()

    assert back_at_bottom.lines == ["line 6", "line 7", "line 8", "line 9"]

def test_tui_manual_scroll_preserves_offset_until_rejoined_to_bottom() -> None:
    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(10):
        tui.add(Text(f"line {index}"))

    tui.request_render()
    tui.scroll_by(-2)
    assert tui.request_render().lines == ["line 4", "line 5", "line 6", "line 7"]

    tui.add(Text("line 10"))
    scrolled_after_growth = tui.request_render()

    assert scrolled_after_growth.lines == ["line 5", "line 6", "line 7", "line 8"]
    assert "line 10" not in scrolled_after_growth.lines

    tui.scroll_to_bottom()
    assert tui.request_render().lines == ["line 7", "line 8", "line 9", "line 10"]

    tui.add(Text("line 11"))
    assert tui.request_render().lines == ["line 8", "line 9", "line 10", "line 11"]

def test_interactive_mode_submit_rejoins_bottom_after_manual_scroll(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=80, rows=6)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/agents", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))
    mode.init()
    for index in range(12):
        mode.history.add(Text(f"history {index}"))

    app.tui.request_render()
    app.tui.scroll_by(-3)
    assert app.tui.is_scrolled()

    assert mode.run() == 0

    assert not app.tui.is_scrolled()

def test_tui_page_keys_scroll_transcript_before_focused_input() -> None:
    class InputRecorder(Container):
        def __init__(self) -> None:
            super().__init__()
            self.inputs: list[str] = []

        def render(self, width: int) -> list[str]:
            return ["prompt"]

        def handle_input(self, data: str) -> None:
            self.inputs.append(data)

    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(8):
        tui.add(Text(f"line {index}"))
    input_recorder = InputRecorder()
    tui.add(input_recorder)
    tui.set_focus(input_recorder)
    tui.start()

    assert terminal.input_handler is not None
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

    terminal.input_handler("\x1b[5~")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 2", "line 3", "line 4", "line 5"]

    terminal.input_handler("\x1b[F")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

    terminal.input_handler("\x1b[5~")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 2", "line 3", "line 4", "line 5"]

    terminal.input_handler("\x1b[6~")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

def test_tui_mouse_wheel_scrolls_transcript_before_focused_input() -> None:
    class InputRecorder(Container):
        def __init__(self) -> None:
            super().__init__()
            self.inputs: list[str] = []

        def render(self, width: int) -> list[str]:
            return ["prompt"]

        def handle_input(self, data: str) -> None:
            self.inputs.append(data)

    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(8):
        tui.add(Text(f"line {index}"))
    input_recorder = InputRecorder()
    tui.add(input_recorder)
    tui.set_focus(input_recorder)
    tui.start()

    assert terminal.input_handler is not None
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

    terminal.input_handler("\x1b[<64;1;1M")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 2", "line 3", "line 4", "line 5"]

    terminal.input_handler("\x1b[<65;1;1M")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

def test_tui_rxvt_mouse_wheel_scrolls_transcript_before_focused_input() -> None:
    class InputRecorder(Container):
        def __init__(self) -> None:
            super().__init__()
            self.inputs: list[str] = []

        def render(self, width: int) -> list[str]:
            return ["prompt"]

        def handle_input(self, data: str) -> None:
            self.inputs.append(data)

    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(8):
        tui.add(Text(f"line {index}"))
    input_recorder = InputRecorder()
    tui.add(input_recorder)
    tui.set_focus(input_recorder)
    tui.start()

    assert terminal.input_handler is not None
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

    terminal.input_handler("\x1b[64;1;1M")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 2", "line 3", "line 4", "line 5"]

    terminal.input_handler("\x1b[65;1;1M")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

def test_tui_legacy_x10_mouse_wheel_scrolls_transcript_before_focused_input() -> None:
    class InputRecorder(Container):
        def __init__(self) -> None:
            super().__init__()
            self.inputs: list[str] = []

        def render(self, width: int) -> list[str]:
            return ["prompt"]

        def handle_input(self, data: str) -> None:
            self.inputs.append(data)

    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(8):
        tui.add(Text(f"line {index}"))
    input_recorder = InputRecorder()
    tui.add(input_recorder)
    tui.set_focus(input_recorder)
    tui.start()

    assert terminal.input_handler is not None
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

    terminal.input_handler("\x1b[M`!!")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 2", "line 3", "line 4", "line 5"]

    terminal.input_handler("\x1b[Ma!!")

    assert input_recorder.inputs == []
    assert tui.last_render is not None
    assert tui.last_render.lines == ["line 5", "line 6", "line 7", "prompt"]

def test_tui_ports_travis234_synchronized_output_wrapping_for_full_and_diff_renders() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    text = Text("first")
    tui.add(text)

    tui.request_render()
    assert terminal.writes[-1].startswith("\x1b[?2026h")
    assert terminal.writes[-1].endswith("\x1b[?2026l")

    text.set_text("second")
    tui.request_render()
    assert terminal.writes[-1].startswith("\x1b[?2026h")
    assert terminal.writes[-1].endswith("\x1b[?2026l")

def test_tui_ports_travis234_first_render_without_screen_clear() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("first"))

    tui.request_render()

    assert terminal.writes[-1].startswith("\x1b[?2026hfirst")
    assert "\x1b[2J\x1b[H" not in terminal.writes[-1]

def test_tui_ports_travis234_forced_full_render_clears_scrollback() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("first"))
    tui.request_render()

    tui.request_render(force=True)

    assert "\x1b[2J\x1b[H\x1b[3J" in terminal.writes[-1]

def test_tui_ports_travis234_clear_on_shrink_api_and_env(monkeypatch) -> None:
    monkeypatch.delenv("TRAVIS234_CLEAR_ON_SHRINK", raising=False)
    tui = TUI(FakeTerminal(columns=40))

    assert tui.get_clear_on_shrink() is False

    tui.set_clear_on_shrink(True)
    assert tui.get_clear_on_shrink() is True

    tui.set_clear_on_shrink(False)
    assert tui.get_clear_on_shrink() is False

    monkeypatch.setenv("TRAVIS234_CLEAR_ON_SHRINK", "1")
    assert TUI(FakeTerminal(columns=40)).get_clear_on_shrink() is True

def test_tui_ports_travis234_clear_on_shrink_uses_clearing_full_redraw() -> None:
    terminal = FakeTerminal(columns=40, rows=10)
    tui = TUI(terminal)
    tui.set_clear_on_shrink(True)
    tui.add(Text("first"))
    second = Text("second")
    tui.add(second)
    tui.request_render()

    tui.remove(second)
    info = tui.request_render()

    assert info.full is True
    assert "\x1b[2J\x1b[H\x1b[3J" in terminal.writes[-1]

def test_tui_ports_travis234_full_redraw_counter() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    text = Text("first")
    tui.add(text)

    assert tui.full_redraws == 0

    tui.request_render()
    assert tui.full_redraws == 1

    text.set_text("second")
    tui.request_render()
    assert tui.full_redraws == 1

    tui.request_render(force=True)
    assert tui.full_redraws == 2

def test_tui_ports_travis234_hardware_cursor_api_and_env(monkeypatch) -> None:
    monkeypatch.delenv("TRAVIS234_HARDWARE_CURSOR", raising=False)
    assert TUI(FakeTerminal(columns=40)).get_show_hardware_cursor() is False

    explicit = TUI(FakeTerminal(columns=40), show_hardware_cursor=True)
    assert explicit.get_show_hardware_cursor() is True

    monkeypatch.setenv("TRAVIS234_HARDWARE_CURSOR", "1")
    assert TUI(FakeTerminal(columns=40)).get_show_hardware_cursor() is True
    assert TUI(FakeTerminal(columns=40), show_hardware_cursor=False).get_show_hardware_cursor() is False

def test_tui_ports_travis234_disabling_hardware_cursor_hides_terminal_cursor() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal, show_hardware_cursor=True)
    tui.add(Text("first"))
    tui.request_render()

    tui.set_show_hardware_cursor(False)

    assert tui.get_show_hardware_cursor() is False
    assert "\x1b[?25l" in terminal.writes[-1]

def test_terminal_ports_travis234_movement_and_clear_operations() -> None:
    terminal = FakeTerminal(columns=40)

    terminal.move_by(2)
    terminal.move_by(-1)
    terminal.move_by(0)
    terminal.clear_line()
    terminal.clear_line()
    terminal.clear_from_cursor()
    terminal.clear_from_cursor()
    terminal.clear_screen()
    terminal.clear_screen()

    assert terminal.writes == [
        "\x1b[2B",
        "\x1b[1A",
        "\x1b[K",
        "\x1b[K",
        "\x1b[J",
        "\x1b[J",
        "\x1b[2J\x1b[H",
        "\x1b[2J\x1b[H",
    ]

def test_terminal_ports_travis234_progress_sequences() -> None:
    terminal = FakeTerminal(columns=40)

    terminal.set_progress(True)
    terminal.set_progress(False)

    assert terminal.writes == ["\x1b]9;4;3\x07", "\x1b]9;4;0;\x07"]

def test_process_terminal_ports_travis234_progress_keepalive_and_clear() -> None:
    class RecordingProcessTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=0.01)

        def write(self, data: str) -> None:
            self.writes.append(data)

    terminal = RecordingProcessTerminal()

    terminal.set_progress(True)
    time.sleep(0.035)
    terminal.set_progress(False)
    writes_after_clear = list(terminal.writes)
    time.sleep(0.03)

    assert terminal.writes.count("\x1b]9;4;3\x07") >= 2
    assert terminal.writes[-1] == "\x1b]9;4;0;\x07"
    assert terminal.writes == writes_after_clear

def test_process_terminal_ports_travis234_start_stop_progress_cleanup() -> None:
    class RecordingProcessTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=10)

        def write(self, data: str) -> None:
            self.writes.append(data)

    terminal = RecordingProcessTerminal()

    terminal.start(lambda data: None, lambda: None)
    terminal.set_progress(True)
    terminal.stop()

    assert terminal.writes == [
        "\x1b[?2004h",
        "\x1b]9;4;3\x07",
        "\x1b]9;4;0;\x07",
        "\x1b[?2004l",
    ]

def test_process_terminal_disables_mouse_tracking_by_default_to_keep_touchpad_scroll_safe(monkeypatch) -> None:
    class RecordingProcessTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=10)

        def write(self, data: str) -> None:
            self.writes.append(data)

    monkeypatch.delenv("TRAVIS234_TUI_MOUSE", raising=False)
    monkeypatch.delenv("TRAVIS234_SANDBOX", raising=False)
    terminal = RecordingProcessTerminal()

    terminal.start(lambda data: None, lambda: None)
    terminal.stop()

    assert "\x1b[?1000h\x1b[?1006h" not in terminal.writes
    assert "\x1b[?1006l\x1b[?1000l" not in terminal.writes

def test_process_terminal_enables_mouse_tracking_by_default_in_sandbox(monkeypatch) -> None:
    class RecordingProcessTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=10)

        def write(self, data: str) -> None:
            self.writes.append(data)

    monkeypatch.delenv("TRAVIS234_TUI_MOUSE", raising=False)
    monkeypatch.setenv("TRAVIS234_SANDBOX", "1")
    terminal = RecordingProcessTerminal()

    terminal.start(lambda data: None, lambda: None)
    terminal.stop()

    assert "\x1b[?1000h\x1b[?1006h" in terminal.writes
    assert "\x1b[?1006l\x1b[?1000l" in terminal.writes

def test_process_terminal_can_disable_sandbox_mouse_tracking(monkeypatch) -> None:
    class RecordingProcessTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=10)

        def write(self, data: str) -> None:
            self.writes.append(data)

    monkeypatch.setenv("TRAVIS234_SANDBOX", "1")
    monkeypatch.setenv("TRAVIS234_TUI_MOUSE", "0")
    terminal = RecordingProcessTerminal()

    terminal.start(lambda data: None, lambda: None)
    terminal.stop()

    assert "\x1b[?1000h\x1b[?1006h" not in terminal.writes
    assert "\x1b[?1006l\x1b[?1000l" not in terminal.writes

def test_process_terminal_can_opt_into_mouse_tracking(monkeypatch) -> None:
    class RecordingProcessTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=10)

        def write(self, data: str) -> None:
            self.writes.append(data)

    monkeypatch.setenv("TRAVIS234_TUI_MOUSE", "1")
    terminal = RecordingProcessTerminal()

    terminal.start(lambda data: None, lambda: None)
    terminal.stop()

    assert "\x1b[?1000h\x1b[?1006h" in terminal.writes
    assert "\x1b[?1006l\x1b[?1000l" in terminal.writes

def test_process_terminal_ports_travis234_utf8_text_decoding_before_stdin_buffer() -> None:
    terminal = ProcessTerminal()
    seen: list[str] = []
    terminal.input_handler = seen.append
    terminal._stdin_buffer = StdinBuffer({"timeout": 10})
    terminal._stdin_buffer.on("data", terminal._forward_input_sequence)

    emoji_bytes = "👨‍💻".encode("utf-8")
    terminal._process_stdin_bytes(emoji_bytes[:2])
    assert seen == []

    terminal._process_stdin_bytes(emoji_bytes[2:])
    assert seen == ["👨", "\u200d", "💻"]

def test_tui_ports_travis234_start_stop_terminal_lifecycle() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("ready"))

    tui.start()

    assert terminal.writes[0] == "\x1b[?2004h"
    assert terminal.writes[1] == "\x1b[?25l"
    assert terminal.input_handler is not None
    assert terminal.resize_handler is not None
    assert "ready" in terminal.output

    tui.stop()

    assert terminal.writes[-2:] == ["\x1b[?25h", "\x1b[?2004l"]

def test_interactive_mode_ports_travis234_tui_lifecycle_on_run_exit(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    exit_code = mode.run()

    assert exit_code == 0
    assert terminal.writes[0] == "\x1b[?2004h"
    assert terminal.writes[1] == "\x1b[?25l"
    assert terminal.writes[-2:] == ["\x1b[?25h", "\x1b[?2004l"]

def test_tui_stop_ports_travis234_drain_input_before_terminal_restore() -> None:
    class DrainingTerminal(FakeTerminal):
        def __init__(self) -> None:
            super().__init__(columns=40)
            self.lifecycle: list[str] = []

        def drain_input(self, max_ms: int = 1000, idle_ms: int = 50) -> None:
            self.lifecycle.append(f"drain:{max_ms}:{idle_ms}")

        def show_cursor(self) -> None:
            self.lifecycle.append("show_cursor")
            super().show_cursor()

        def stop(self) -> None:
            self.lifecycle.append("stop")
            super().stop()

    terminal = DrainingTerminal()
    tui = TUI(terminal)
    tui.start()

    tui.stop()

    assert terminal.lifecycle == ["drain:1000:50", "show_cursor", "stop"]

def test_process_terminal_drain_input_discards_pending_bytes() -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\x1b[27;3u")
        terminal = ProcessTerminal()
        terminal._stdin_fd = read_fd
        seen: list[str] = []
        terminal.input_handler = seen.append

        terminal.drain_input(max_ms=50, idle_ms=1)

        readable, _writable, _errors = select.select([read_fd], [], [], 0)
        assert readable == []
        assert seen == []
    finally:
        os.close(read_fd)
        os.close(write_fd)

def test_interactive_mode_default_uses_raw_tui_input_for_prompt_submit(monkeypatch, tmp_path) -> None:
    def forbidden_input(prompt: str) -> str:
        raise AssertionError("raw TUI mode must not call Python input()")

    monkeypatch.setattr(builtins, "input", forbidden_input)
    seen_prompts: list[str] = []

    def script(model, context):
        user_messages = [message for message in context.messages if getattr(message, "role", None) == "user"]
        if user_messages:
            content = user_messages[-1].content
            if isinstance(content, str):
                seen_prompts.append(content)
            else:
                seen_prompts.append("".join(block.text for block in content if isinstance(block, TextContent)))
        return text_response_events(model, "raw reply")

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
    try:
        assert _wait_until(lambda: terminal.input_handler is not None and mode.active_editor is not None)
        assert mode.active_editor is not None
        assert mode.active_editor.focused is True

        terminal.input_handler("h")
        terminal.input_handler("i")
        assert _wait_until(lambda: mode.active_editor is not None and mode.active_editor.get_value() == "hi")
        terminal.input_handler("\r")

        assert _wait_until(lambda: seen_prompts == ["hi"], timeout=2)
        assert _wait_until(lambda: not mode._is_turn_active(), timeout=2)
        assert _wait_until(lambda: mode.active_editor is not None, timeout=2)
        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0
    assert "raw reply" in strip_ansi(terminal.output)

def test_interactive_mode_persists_travis234_prompt_history_between_editors(tmp_path) -> None:
    seen_prompts: list[str] = []

    def script(model, context):
        text = ""
        if context.messages and getattr(context.messages[-1], "role", None) == "user":
            content = context.messages[-1].content
            if isinstance(content, str):
                text = content
            else:
                text = "".join(block.text for block in content if isinstance(block, TextContent))
        seen_prompts.append(text)
        return text_response_events(model, "ok")

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
    try:
        assert _wait_until(lambda: terminal.input_handler is not None and mode.active_editor is not None)
        terminal.input_handler("first\r")
        assert _wait_until(lambda: seen_prompts == ["first"], timeout=2)
        assert _wait_until(lambda: not mode._is_turn_active() and mode.active_editor is not None, timeout=2)

        terminal.input_handler("second\r")
        assert _wait_until(lambda: seen_prompts == ["first", "second"], timeout=2)
        assert _wait_until(lambda: not mode._is_turn_active() and mode.active_editor is not None, timeout=2)

        terminal.input_handler("\x1b[A")
        assert _wait_until(lambda: mode.active_editor is not None and mode.active_editor.get_value() == "second")
        terminal.input_handler("\x1b[A")
        assert _wait_until(lambda: mode.active_editor is not None and mode.active_editor.get_value() == "first")
        terminal.input_handler("\x1b[B")
        assert _wait_until(lambda: mode.active_editor is not None and mode.active_editor.get_value() == "second")
        terminal.input_handler("\x1b[B")
        assert _wait_until(lambda: mode.active_editor is not None and mode.active_editor.get_value() == "")

        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0

def test_interactive_mode_editor_escape_aborts_active_turn(tmp_path, monkeypatch) -> None:
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    aborts: list[bool] = []

    monkeypatch.setattr(mode, "_is_turn_active", lambda: True)
    monkeypatch.setattr(app.session.agent, "abort", lambda: aborts.append(True))

    mode._handle_editor_escape()

    assert aborts == [True]
    assert mode.status._message == "Aborting"

def test_interactive_mode_editor_escape_aborts_active_turn_bash(tmp_path, monkeypatch) -> None:
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    aborts: list[str] = []

    monkeypatch.setattr(mode, "_is_turn_active", lambda: True)
    app.session._bash_signal = object()
    monkeypatch.setattr(app.session.agent, "abort", lambda: aborts.append("agent"))
    monkeypatch.setattr(app.session, "abort_bash", lambda: aborts.append("bash"))

    try:
        mode._handle_editor_escape()
    finally:
        app.session._bash_signal = None

    assert aborts == ["agent"]
    assert mode.status._message == "Aborting"

def test_ctrl_c_interrupts_focused_user_command_without_aborting_agent(tmp_path, monkeypatch) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    interrupts = []
    agent_aborts = []
    assert mode._user_commands is not None
    monkeypatch.setattr(
        mode._user_commands,
        "interrupt_focused",
        lambda: interrupts.append(True) or True,
    )
    monkeypatch.setattr(mode, "_is_turn_active", lambda: True)
    monkeypatch.setattr(app.session.agent, "abort", lambda: agent_aborts.append(True))

    mode._handle_editor_escape()

    assert interrupts == [True]
    assert agent_aborts == []
    assert mode.status._message == "Interrupting user command"
    mode._user_commands.close()
    mode.footer_data_provider.dispose()
    app.close()

def test_ctrl_c_aborts_agent_only_once_without_focused_user_command(tmp_path, monkeypatch) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    aborts = []
    assert mode._user_commands is not None
    monkeypatch.setattr(mode._user_commands, "interrupt_focused", lambda: False)
    monkeypatch.setattr(mode, "_is_turn_active", lambda: True)
    monkeypatch.setattr(app.session.agent, "abort", lambda: aborts.append(True))

    mode._handle_editor_escape()
    mode._handle_editor_escape()

    assert aborts == [True]
    assert mode._agent_abort_requested is True
    mode._user_commands.close()
    mode.footer_data_provider.dispose()
    app.close()

def test_wait_for_active_turn_has_a_shutdown_deadline(tmp_path) -> None:
    from concurrent.futures import Future

    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    never_finishes: Future[object] = Future()
    mode._turn_future = never_finishes

    started_at = time.monotonic()
    stopped = mode._wait_for_active_turn(timeout_seconds=0.05)

    assert stopped is False
    assert time.monotonic() - started_at < 0.5
    mode._turn_future = None
    if mode._user_commands is not None:
        mode._user_commands.close()
    mode.footer_data_provider.dispose()
    app.close()

def test_interactive_mode_escape_aborted_tool_turn_returns_to_idle(tmp_path) -> None:
    started = threading.Event()
    finished = threading.Event()
    provider_calls = {"n": 0}

    def script(model, context):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(model, "aborter", {})
        return text_response_events(model, "should not run after abort")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def aborter_execute(tool_call_id, args, signal=None, on_update=None):
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if signal and signal.aborted:
                finished.set()
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
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode.status.set_message("Running")
    mode._start_turn_thread("run aborter", 0, 0)
    assert started.wait(timeout=2)

    mode._handle_editor_escape()
    mode._wait_for_active_turn()

    assert finished.is_set()
    assert provider_calls["n"] == 1
    assert mode.status._message == "Idle"

def test_interactive_mode_escape_aborts_streaming_text_turn_before_done(tmp_path) -> None:
    started = threading.Event()
    send_after_abort = threading.Event()
    allow_finish = threading.Event()
    provider_finished = threading.Event()

    def stream_fn(model, context, options=None):
        stream = create_assistant_message_event_stream()

        def produce() -> None:
            partial = AssistantMessage(
                content=[TextContent(type="text", text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
            stream.push(StartEvent(partial=partial))
            stream.push(TextStartEvent(content_index=0, partial=partial))
            partial.content[0].text += "before-abort"
            stream.push(TextDeltaEvent(content_index=0, delta="before-abort", partial=partial))
            started.set()

            if send_after_abort.wait(timeout=2):
                partial.content[0].text += "after-abort"
                stream.push(TextDeltaEvent(content_index=0, delta="after-abort", partial=partial))

            allow_finish.wait(timeout=2)
            final = AssistantMessage(
                content=[TextContent(type="text", text=partial.content[0].text)],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
            stream.push(TextEndEvent(content_index=0, content=final.content[0].text, partial=partial))
            stream.push(DoneEvent(reason="stop", message=final))
            provider_finished.set()

        threading.Thread(target=produce, daemon=True).start()
        return stream

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode.status.set_message("Running")
    mode._start_turn_thread("stream text", 0, 0)
    assert started.wait(timeout=2)

    try:
        mode._handle_editor_escape()
        send_after_abort.set()
        stopped = _wait_until(lambda: not mode._is_turn_active() and mode.status._message == "Idle", timeout=1)
        rendered = strip_ansi("\n".join(app.tui.render(120)))
        provider_finished_before_release = provider_finished.is_set()
    finally:
        allow_finish.set()
        mode._wait_for_active_turn()

    assert stopped is True
    assert provider_finished_before_release is False
    assert "after-abort" not in rendered
    assert mode.status._message == "Idle"

def test_interactive_mode_escape_aborts_turn_waiting_for_first_stream_event(tmp_path) -> None:
    stream_started = threading.Event()
    captured_signal = {}

    def stream_fn(model, context, options=None):
        captured_signal["signal"] = getattr(options, "signal", None)
        stream_started.set()
        return create_assistant_message_event_stream()

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    mode.init()

    try:
        mode.status.set_message("Running")
        mode._start_turn_thread("hang before first token", 0, 0)
        assert stream_started.wait(timeout=1)

        mode._handle_editor_escape()

        stopped = _wait_until(lambda: not mode._is_turn_active(), timeout=1)
    finally:
        app.tui.stop()

    assert getattr(captured_signal["signal"], "aborted") is True
    assert stopped is True

def test_interactive_mode_ctrl_c_aborts_active_turn_and_accepts_followup_prompt(tmp_path) -> None:
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
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode.status.set_message("Running")
    mode._start_turn_thread("run aborter", 0, 0)
    assert started.wait(timeout=2)

    result = mode._handle_tui_terminal_input("\x03")
    assert result == {"consume": True}
    assert mode.status._message == "Aborting"

    mode._wait_for_active_turn()

    assert aborted.is_set()
    assert provider_calls["n"] == 1
    assert mode.status._message == "Idle"
    assert not mode._is_turn_active()

    mode.status.set_message("Running")
    mode._start_turn_thread("followup", app.compaction.compressor.compression_count, 0)
    mode._wait_for_active_turn()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert provider_calls["n"] == 2
    assert "followup ok" in rendered
    assert mode.status._message == "Idle"
    assert "status: Running" not in rendered
    assert "status: Aborting" not in rendered

def test_interactive_mode_escape_is_noop_when_idle_and_preserves_session_state(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        UserMessage(role="user", content=[TextContent(type="text", text="keep me")]),
        AssistantMessage(
            content=[TextContent(type="text", text="still here")],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        ),
    ]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()
    before_messages = list(app.messages)

    mode._handle_editor_escape()

    assert app.messages == before_messages
    assert mode.status._message == "Idle"
    assert mode._shutdown_requested is False
    assert not mode._is_turn_active()
