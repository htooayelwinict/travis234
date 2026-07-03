from __future__ import annotations

import base64
import builtins
import json
import os
import select
import threading
import time
import urllib.error

import appv231.tui.interactive_mode as interactive_mode
from appv231.ai.providers import model_catalog
from appv231.ai.providers.model_catalog import openrouter_live_catalog_item_to_model
from appv231.tui import (
    Image,
    Component,
    Container,
    FooterComponent,
    FakeTerminal,
    formatCwdForFooter,
    allocateImageId,
    calculateImageRows,
    fuzzyFilter,
    fuzzyMatch,
    CancellableLoader,
    CombinedAutocompleteProvider,
    deleteAllKittyImages,
    deleteKittyImage,
    detectCapabilities,
    decodeKittyPrintable,
    encodeITerm2,
    encodeKitty,
    getCapabilities,
    getCellDimensions,
    getGifDimensions,
    getImageDimensions,
    Input,
    InteractiveMode,
    InteractiveRenderer,
    KeybindingsManager,
    Loader,
    Markdown,
    parseOsc11BackgroundColor,
    ProcessTerminal,
    SelectItem,
    SelectList,
    SettingsList,
    SimpleAutocompleteProvider,
    StatusLine,
    StdinBuffer,
    getPngDimensions,
    hyperlink,
    imageFallback,
    isImageLine,
    isFocusable,
    renderImage,
    resetCapabilitiesCache,
    setCapabilities,
    setCellDimensions,
    TUI,
    Text,
    ToolExecutionComponent,
    TruncatedText,
    TUI_KEYBINDINGS,
    getKeybindings,
    setKeybindings,
    extractSegments,
    extract_segments,
    strip_ansi,
    slice_by_column,
    slice_with_width,
    truncateToWidth,
    truncate_to_width,
    visibleWidth,
    visible_width,
    wrapTextWithAnsi,
    wrap_text,
)
from appv231.agent.types import (
    AgentEndEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from appv231.agent.types import AgentTool, AgentToolResult
from appv231.ai.providers.capabilities import ProviderParamWarning
from appv231.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv231.ai.providers.params import GenerationParams
from appv231.ai.models import get_api_key_for_provider, get_provider_auth_status, register_model, reset_models
from appv231.ai.stream import register_api_provider, reset_api_providers
from appv231.ai.types import (
    AssistantMessage,
    Cost,
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    Usage,
    UserMessage,
    empty_usage,
    now_ms,
)
from appv231.ai.model_resolver import ScopedModel
from appv231.app import CodingApp
from appv231.compaction.timing import ManualCompressionStatus
from appv231.coding_agent import BashResult
from appv231.coding_agent.session_store import BashExecutionMessage, BranchSummaryMessage, CustomMessage
from appv231.coding_agent.subagents import CallableSubagentBackend
from appv231.coding_agent.tools.bash import BashOperations
from appv231.coding_agent.tools.read import create_read_tool_definition
from appv231.coding_agent.tools.types import ToolDefinition
from appv231.ai.event_stream import create_assistant_message_event_stream
from appv231.ai.stream import ApiProvider


def setup_function() -> None:
    reset_api_providers()
    reset_models()
    model_catalog.reset_cache()


def _visible_index_of(line: str, text: str) -> int:
    index = line.index(text)
    return visible_width(line[:index])


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_visible_width_strips_ansi() -> None:
    assert visible_width("\x1b[31mred\x1b[0m") == 3
    assert visible_width("plain") == 5


def test_visible_width_ports_pi_tabs_wide_unicode_and_apc() -> None:
    assert visible_width("\t\x1b[31m界\x1b[0m") == 5
    assert visible_width("🙂界") == 4
    assert visible_width("a\x1b_pi:c\x07b") == 2


def test_fuzzy_match_ports_pi_scoring_and_swapped_model_tokens() -> None:
    assert fuzzyMatch("", "anything").matches is True
    assert fuzzyMatch("", "anything").score == 0
    assert fuzzyMatch("longquery", "short").matches is False
    assert fuzzyMatch("abc", "aXbXc").matches is True
    assert fuzzyMatch("abc", "cba").matches is False
    assert fuzzyMatch("ABC", "abc").matches is True

    consecutive = fuzzyMatch("foo", "foobar")
    scattered = fuzzyMatch("foo", "f_o_o_bar")
    assert consecutive.matches is True
    assert scattered.matches is True
    assert consecutive.score < scattered.score

    at_boundary = fuzzyMatch("fb", "foo-bar")
    not_at_boundary = fuzzyMatch("fb", "afbx")
    assert at_boundary.matches is True
    assert not_at_boundary.matches is True
    assert at_boundary.score < not_at_boundary.score

    assert fuzzyMatch("codex52", "gpt-5.2-codex").matches is True


def test_fuzzy_filter_ports_pi_tokenized_sorting_and_custom_text() -> None:
    assert fuzzyFilter(["apple", "banana", "cherry"], "", lambda value: value) == ["apple", "banana", "cherry"]
    assert fuzzyFilter(["apple", "banana", "cherry"], "an", lambda value: value) == ["banana"]
    assert fuzzyFilter(["a_p_p", "app", "application"], "app", lambda value: value)[0] == "app"
    assert fuzzyFilter(["clone", "cl"], "cl", lambda value: value) == ["cl", "clone"]

    items = [
        {"name": "foo", "id": 1},
        {"name": "bar", "id": 2},
        {"name": "foobar", "id": 3},
    ]
    filtered = fuzzyFilter(items, "foo", lambda item: item["name"])
    assert [item["name"] for item in filtered] == ["foo", "foobar"]

    model = {"id": "gpt-5.5", "provider": "openai-codex"}
    assert fuzzyFilter([model], "openai-codex/gpt-5.5", lambda item: f"{item['id']} {item['provider']}") == [model]


def test_footer_ports_pi_home_path_formatting() -> None:
    assert formatCwdForFooter("/home/user2", "/home/user") == "/home/user2"
    assert formatCwdForFooter("/home/user", "/home/user") == "~"
    assert formatCwdForFooter("/home/user/project", "/home/user") == "~/project"

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


def test_simple_autocomplete_provider_ports_pi_fuzzy_command_filtering() -> None:
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


def test_combined_autocomplete_provider_ports_pi_commands_files_and_attachments(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "notes file.md").write_text("notes", encoding="utf-8")

    provider = CombinedAutocompleteProvider(
        [{"name": "compact", "description": "Compress context", "argumentHint": "[focus]"}],
        str(tmp_path),
    )

    command_suggestions = provider.getSuggestions(["/co"], 0, 3, {"signal": None, "force": False})
    assert command_suggestions["prefix"] == "/co"
    assert command_suggestions["items"][0] == {
        "value": "compact",
        "label": "compact",
        "description": "[focus] — Compress context",
    }
    applied_command = provider.applyCompletion(["/co"], 0, 3, command_suggestions["items"][0], "/co")
    assert applied_command == {"lines": ["/compact "], "cursorLine": 0, "cursorCol": len("/compact ")}

    path_suggestions = provider.getSuggestions(["open src/"], 0, len("open src/"), {"signal": None, "force": False})
    assert path_suggestions["prefix"] == "src/"
    assert path_suggestions["items"][:2] == [
        {"value": "src/pkg/", "label": "pkg/"},
        {"value": "src/app.py", "label": "app.py"},
    ]

    at_suggestions = provider.getSuggestions(['@"notes'], 0, len('@"notes'), {"signal": None, "force": True})
    assert at_suggestions["prefix"] == '@"notes'
    assert at_suggestions["items"][0] == {"value": '@"notes file.md"', "label": "notes file.md"}
    applied_at = provider.applyCompletion(['@"notes'], 0, len('@"notes'), at_suggestions["items"][0], '@"notes')
    assert applied_at == {"lines": ['@"notes file.md" '], "cursorLine": 0, "cursorCol": len('@"notes file.md" ')}

    assert provider.shouldTriggerFileCompletion(["/compact"], 0, len("/compact")) is False
    assert provider.shouldTriggerFileCompletion(["/compact s"], 0, len("/compact s")) is True


def test_stdin_buffer_ports_pi_split_sequences_and_kitty_regressions() -> None:
    buffer = StdinBuffer({"timeout": 10})
    emitted: list[str] = []
    buffer.on("data", emitted.append)

    buffer.process("abc\x1b[A")
    assert emitted == ["a", "b", "c", "\x1b[A"]

    buffer.process("\x1b[<3")
    buffer.process("5;1")
    buffer.process("5;")
    assert buffer.getBuffer() == "\x1b[<35;15;"
    buffer.process("10m")
    assert emitted[-1] == "\x1b[<35;15;10m"

    emitted.clear()
    buffer.process("\x1b\x1b[27;129:3u")
    assert emitted == ["\x1b", "\x1b[27;129:3u"]

    emitted.clear()
    buffer.process("\x1b[64u")
    buffer.process("@")
    assert emitted == ["\x1b[64u"]


def test_stdin_buffer_ports_pi_bracketed_paste_events() -> None:
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


def test_parse_osc11_background_color_ports_pi_formats() -> None:
    assert parseOsc11BackgroundColor("\x1b]11;rgb:0000/8000/ffff\x07") == {"r": 0, "g": 128, "b": 255}
    assert parseOsc11BackgroundColor("\x1b]11;#ffffff\x1b\\") == {"r": 255, "g": 255, "b": 255}
    assert parseOsc11BackgroundColor("\x1b]11;#000000\x07") == {"r": 0, "g": 0, "b": 0}
    assert parseOsc11BackgroundColor("x\x1b]11;#ffffff\x07") is None
    assert parseOsc11BackgroundColor("\x1b]10;#ffffff\x07") is None
    assert parseOsc11BackgroundColor("\x1b]11;#ffffff\x07x") is None


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


def test_tui_query_terminal_background_color_ports_pi_reply_edges() -> None:
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
        invalid_query = tui.queryTerminalBackgroundColor({"timeoutMs": 1000})
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


def test_keybindings_manager_ports_pi_defaults_conflicts_and_globals() -> None:
    keybindings = KeybindingsManager(
        TUI_KEYBINDINGS,
        {
            "tui.input.submit": ["enter", "ctrl+enter"],
            "tui.select.confirm": "ctrl+x",
            "tui.input.copy": "ctrl+x",
        },
    )

    assert keybindings.getKeys("tui.input.submit") == ["enter", "ctrl+enter"]
    assert keybindings.getKeys("tui.editor.cursorLeft") == ["left", "ctrl+b"]
    assert keybindings.getConflicts() == [
        {"key": "ctrl+x", "keybindings": ["tui.select.confirm", "tui.input.copy"]}
    ]
    assert keybindings.matches("\x1b[D", "tui.editor.cursorLeft") is True

    setKeybindings(keybindings)
    assert getKeybindings() is keybindings


def test_keys_ports_pi_parse_match_and_release_surface() -> None:
    from appv231.tui.keys import is_key_release, matches_key, parse_key

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


def test_tui_package_exports_pi_key_helpers() -> None:
    from appv231.tui import isKeyRelease, is_key_release, matchesKey, matches_key, parseKey, parse_key

    assert parse_key("\x03") == "ctrl+c"
    assert parseKey("\x1bb") == "alt+b"
    assert matches_key("\x1b[A", "up") is True
    assert matchesKey("\x1b[97;5u", "ctrl+a") is True
    assert is_key_release("\x1b[97;1:3u") is True
    assert isKeyRelease("\x1b[200~90:62:3F:A5\x1b[201~") is False


def test_keys_ports_pi_key_object_kitty_state_and_repeat_surface() -> None:
    from appv231.tui.keys import (
        Key,
        isKeyRepeat,
        isKittyProtocolActive,
        is_key_repeat,
        is_kitty_protocol_active,
        matches_key,
        setKittyProtocolActive,
        set_kitty_protocol_active,
    )

    set_kitty_protocol_active(False)
    assert is_kitty_protocol_active() is False
    setKittyProtocolActive(True)
    assert isKittyProtocolActive() is True

    assert Key.escape == "escape"
    assert Key.backtick == "`"
    assert Key.ctrl("c") == "ctrl+c"
    assert Key.ctrlShift("p") == "ctrl+shift+p"
    assert Key.altSuper("?") == "alt+super+?"
    assert matches_key("\x1b[112;6u", Key.ctrlShift("p")) is True

    assert is_key_repeat("\x1b[97;1:2u") is True
    assert isKeyRepeat("\x1b[200~90:62:2F:A5\x1b[201~") is False


def test_keys_and_utils_export_pi_decode_printable_and_camel_aliases() -> None:
    assert decodeKittyPrintable("\x1b[97u") == "a"
    assert decodeKittyPrintable("\x1b[97:65:97;2u") == "A"
    assert decodeKittyPrintable("\x1b[97;5u") is None

    assert visibleWidth("\x1b[31mred\x1b[0m") == 3
    truncated = truncateToWidth("abcdef", 4)
    assert truncated == "a\x1b[0m...\x1b[0m"
    assert visibleWidth(truncated) == 4
    assert wrapTextWithAnsi("\x1b[31mhello world\x1b[0m", 6) == ["\x1b[31mhello", "world\x1b[0m"]


def test_tui_package_exports_pi_extended_key_helpers() -> None:
    from appv231.tui import Key, isKeyRepeat, isKittyProtocolActive, setKittyProtocolActive

    setKittyProtocolActive(False)
    assert isKittyProtocolActive() is False
    assert Key.ctrlAlt("x") == "ctrl+alt+x"
    assert isKeyRepeat("\x1b[120;1:2u") is True


def test_truncate_to_width_passes_ansi() -> None:
    assert truncate_to_width("hello world", 5) == "hello\x1b[0m"
    styled = "\x1b[31mhello world\x1b[0m"
    assert visible_width(truncate_to_width(styled, 5)) == 5


def test_truncate_to_width_streams_very_large_unicode_input() -> None:
    truncated = truncate_to_width("🙂界" * 100_000, 40, "…")

    assert visible_width(truncated) <= 40
    assert truncated.endswith("…\x1b[0m")


def test_truncate_to_width_ports_pi_no_ellipsis_reset() -> None:
    truncated = truncate_to_width(f"\x1b[31m{'hello' * 100}", 10, "")

    assert visible_width(truncated) <= 10
    assert truncated.endswith("\x1b[0m")


def test_truncate_to_width_ports_pi_wide_character_boundaries() -> None:
    assert truncate_to_width("🙂界abc", 4) == "🙂界\x1b[0m"
    assert truncate_to_width("a\t界", 4) == "a\t\x1b[0m"


def test_truncate_to_width_ports_pi_optional_ellipsis_and_padding() -> None:
    truncated = truncate_to_width("abcdef", 4, "…")
    assert truncated == "abc\x1b[0m…\x1b[0m"
    assert visible_width(truncated) == 4

    padded = truncate_to_width("🙂界🙂界x", 8, "…", True)
    assert padded == "🙂界🙂\x1b[0m…\x1b[0m "
    assert visible_width(padded) == 8


def test_truncated_text_ports_pi_padding_truncation_and_first_line_only() -> None:
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


def test_loader_and_cancellable_loader_port_pi_rendering_and_escape_cancel() -> None:
    terminal = FakeTerminal()
    tui = TUI(terminal)
    loader = Loader(tui, lambda value: f"<{value}>", lambda value: value.upper(), "Working", {"frames": ["*"]})

    try:
        rendered = loader.render(40)
        assert rendered[0] == ""
        assert "* WORKING" in rendered[1]

        aborted: list[bool] = []
        cancellable = CancellableLoader(tui, lambda value: value, lambda value: value, "Working", {"frames": [""]})
        cancellable.onAbort = lambda: aborted.append(True)
        assert cancellable.aborted is False

        cancellable.handle_input("\x1b")

        assert cancellable.signal.aborted is True
        assert cancellable.aborted is True
        assert aborted == [True]
    finally:
        loader.stop()


def test_terminal_image_ports_pi_capabilities_encoding_dimensions_and_helpers(monkeypatch) -> None:
    resetCapabilitiesCache()
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    assert detectCapabilities() == {"images": "kitty", "trueColor": True, "hyperlinks": True}
    assert getCapabilities() == {"images": "kitty", "trueColor": True, "hyperlinks": True}

    setCapabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    setCellDimensions({"widthPx": 10, "heightPx": 20})
    assert getCellDimensions() == {"widthPx": 10, "heightPx": 20}

    assert encodeKitty("AAAA", {"columns": 2, "rows": 2, "imageId": 42, "moveCursor": False}) == (
        "\x1b_Ga=T,f=100,q=2,C=1,c=2,r=2,i=42;AAAA\x1b\\"
    )
    chunked = encodeKitty("A" * 4100)
    assert ",m=1;" in chunked
    assert "\x1b_Gm=0;" in chunked
    assert deleteKittyImage(42) == "\x1b_Ga=d,d=I,i=42,q=2\x1b\\"
    assert deleteAllKittyImages() == "\x1b_Ga=d,d=A,q=2\x1b\\"

    assert encodeITerm2("AAAA", {"width": 2, "height": "auto", "name": "pixel", "preserveAspectRatio": False}) == (
        "\x1b]1337;File=inline=1;width=2;height=auto;name=cGl4ZWw=;preserveAspectRatio=0:AAAA\x07"
    )

    png_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    gif_2x3 = base64.b64encode(
        b"GIF89a" + (2).to_bytes(2, "little") + (3).to_bytes(2, "little") + b"\x00\x00\x00"
    ).decode("ascii")
    assert getPngDimensions(png_1x1) == {"widthPx": 1, "heightPx": 1}
    assert getGifDimensions(gif_2x3) == {"widthPx": 2, "heightPx": 3}
    assert getImageDimensions(png_1x1, "image/png") == {"widthPx": 1, "heightPx": 1}

    rendered = renderImage("AAAA", {"widthPx": 20, "heightPx": 20}, {
        "maxWidthCells": 2,
        "imageId": 7,
        "moveCursor": False,
    })
    assert rendered == {
        "sequence": "\x1b_Ga=T,f=100,q=2,C=1,c=2,r=1,i=7;AAAA\x1b\\",
        "rows": 1,
        "imageId": 7,
    }
    assert calculateImageRows({"widthPx": 20, "heightPx": 40}, 2, {"widthPx": 10, "heightPx": 20}) == 2
    assert isImageLine(rendered["sequence"]) is True
    assert hyperlink("Open", "https://example.com") == "\x1b]8;;https://example.com\x1b\\Open\x1b]8;;\x1b\\"
    assert imageFallback("image/png", {"widthPx": 1, "heightPx": 1}, "pixel.png") == (
        "[Image: pixel.png [image/png] 1x1]"
    )
    assert 1 <= allocateImageId() <= 0xFFFFFFFF


def test_image_component_ports_pi_fallback_and_kitty_rendering() -> None:
    resetCapabilitiesCache()
    setCellDimensions({"widthPx": 10, "heightPx": 20})

    setCapabilities({"images": None, "trueColor": False, "hyperlinks": False})
    png_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    fallback = Image(
        png_1x1,
        "image/png",
        {"fallbackColor": lambda value: f"<{value}>"},
        {"filename": "pixel.png", "maxWidthCells": 10},
    )
    assert fallback.render(40) == ["<[Image: pixel.png [image/png] 1x1]>"]

    setCapabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    image = Image(
        "AAAA",
        "image/png",
        {"fallbackColor": lambda value: value},
        {"imageId": 42, "maxWidthCells": 2},
        {"widthPx": 20, "heightPx": 20},
    )

    assert image.getImageId() == 42
    assert image.render(80) == ["\x1b_Ga=T,f=100,q=2,C=1,c=2,r=1,i=42;AAAA\x1b\\"]
    assert image.render(80) == ["\x1b_Ga=T,f=100,q=2,C=1,c=2,r=1,i=42;AAAA\x1b\\"]


def test_tui_ports_pi_terminal_image_cell_size_query_and_response() -> None:
    class InvalidatingText(Text):
        def __init__(self, text: str) -> None:
            super().__init__(text)
            self.invalidations = 0

        def invalidate(self) -> None:
            self.invalidations += 1
            super().invalidate()

    resetCapabilitiesCache()
    setCapabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
    setCellDimensions({"widthPx": 9, "heightPx": 18})
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    text = InvalidatingText("ready")
    tui.add(text)
    tui.start()

    try:
        assert "\x1b[16t" in terminal.writes
        assert terminal.input_handler is not None

        terminal.input_handler("\x1b[6;24;12t")

        assert getCellDimensions() == {"widthPx": 12, "heightPx": 24}
        assert text.invalidations == 1
    finally:
        tui.stop()


def test_tui_ports_pi_kitty_image_cleanup_and_image_line_output() -> None:
    setCapabilities({"images": "kitty", "trueColor": True, "hyperlinks": True})
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

    assert deleteKittyImage(42) in terminal.writes[-1]
    assert "plain" in terminal.writes[-1]


def test_tui_ports_pi_overlay_focus_handle_and_composition() -> None:
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

    assert isFocusable(overlay) is True
    assert isFocusable(Text("plain")) is False
    assert isFocusable(None) is False

    handle = tui.showOverlay(overlay, {"row": 1, "col": 4, "width": 6})

    assert tui.hasOverlay() is True
    assert handle.isFocused() is True
    assert overlay.focused is True
    assert tui.last_render is not None
    assert len(tui.last_render.lines) == 6
    assert tui.last_render.lines[0] == "base"
    assert strip_ansi(tui.last_render.lines[1]).startswith("    OV")

    handle.setHidden(True)
    assert handle.isHidden() is True
    assert tui.hasOverlay() is False
    assert overlay.focused is False
    assert tui.last_render is not None
    assert "OV" not in "\n".join(tui.last_render.lines)

    handle.setHidden(False)
    assert handle.isHidden() is False
    assert handle.isFocused() is True

    handle.unfocus({"target": None})
    assert handle.isFocused() is False
    assert tui.hasOverlay() is True

    handle.focus()
    assert handle.isFocused() is True
    tui.hideOverlay()
    assert tui.hasOverlay() is False
    assert overlay.focused is False


def test_slice_with_width_ports_pi_tab_and_wide_boundaries() -> None:
    text = "out 192M\t.pi/skill-tests/results-ha"
    sliced = slice_with_width(text, 0, 10, strict=True)
    assert sliced == {"text": "out 192M", "width": 8}
    assert visible_width(sliced["text"]) == sliced["width"]

    assert slice_by_column("🙂界abc", 0, 4, strict=True) == "🙂界"
    assert slice_by_column("a🙂b", 1, 2, strict=True) == "🙂"


def test_extract_segments_ports_pi_tab_width_regression() -> None:
    text = "out 192M\t.pi/skill-tests/results-ha"
    segments = extract_segments(text, 10, 13, 10, strict_after=True)

    assert segments["before"] == "out 192M\t"
    assert segments["beforeWidth"] == 11
    assert visible_width(segments["before"]) == segments["beforeWidth"]
    assert extractSegments(text, 10, 13, 10, True) == segments


def test_tui_composite_line_ports_pi_segment_reset_and_style_resume() -> None:
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


def test_tui_ports_pi_synchronized_output_wrapping_for_full_and_diff_renders() -> None:
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


def test_tui_ports_pi_first_render_without_screen_clear() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("first"))

    tui.request_render()

    assert terminal.writes[-1].startswith("\x1b[?2026hfirst")
    assert "\x1b[2J\x1b[H" not in terminal.writes[-1]


def test_tui_ports_pi_forced_full_render_clears_scrollback() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("first"))
    tui.request_render()

    tui.request_render(force=True)

    assert "\x1b[2J\x1b[H\x1b[3J" in terminal.writes[-1]


def test_tui_ports_pi_clear_on_shrink_api_and_env(monkeypatch) -> None:
    monkeypatch.delenv("APPV231_CLEAR_ON_SHRINK", raising=False)
    tui = TUI(FakeTerminal(columns=40))

    assert tui.get_clear_on_shrink() is False
    assert tui.getClearOnShrink() is False

    tui.set_clear_on_shrink(True)
    assert tui.get_clear_on_shrink() is True

    tui.setClearOnShrink(False)
    assert tui.getClearOnShrink() is False

    monkeypatch.setenv("APPV231_CLEAR_ON_SHRINK", "1")
    assert TUI(FakeTerminal(columns=40)).get_clear_on_shrink() is True


def test_tui_ports_pi_clear_on_shrink_uses_clearing_full_redraw() -> None:
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


def test_tui_ports_pi_full_redraw_counter() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    text = Text("first")
    tui.add(text)

    assert tui.full_redraws == 0
    assert tui.fullRedraws == 0

    tui.request_render()
    assert tui.full_redraws == 1
    assert tui.fullRedraws == 1

    text.set_text("second")
    tui.request_render()
    assert tui.full_redraws == 1

    tui.request_render(force=True)
    assert tui.full_redraws == 2


def test_tui_ports_pi_hardware_cursor_api_and_env(monkeypatch) -> None:
    monkeypatch.delenv("APPV231_HARDWARE_CURSOR", raising=False)
    assert TUI(FakeTerminal(columns=40)).get_show_hardware_cursor() is False

    explicit = TUI(FakeTerminal(columns=40), show_hardware_cursor=True)
    assert explicit.get_show_hardware_cursor() is True
    assert explicit.getShowHardwareCursor() is True

    monkeypatch.setenv("APPV231_HARDWARE_CURSOR", "1")
    assert TUI(FakeTerminal(columns=40)).get_show_hardware_cursor() is True
    assert TUI(FakeTerminal(columns=40), show_hardware_cursor=False).getShowHardwareCursor() is False


def test_tui_ports_pi_disabling_hardware_cursor_hides_terminal_cursor() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal, show_hardware_cursor=True)
    tui.add(Text("first"))
    tui.request_render()

    tui.set_show_hardware_cursor(False)

    assert tui.get_show_hardware_cursor() is False
    assert "\x1b[?25l" in terminal.writes[-1]


def test_terminal_ports_pi_movement_and_clear_operations() -> None:
    terminal = FakeTerminal(columns=40)

    terminal.move_by(2)
    terminal.moveBy(-1)
    terminal.move_by(0)
    terminal.clear_line()
    terminal.clearLine()
    terminal.clear_from_cursor()
    terminal.clearFromCursor()
    terminal.clear_screen()
    terminal.clearScreen()

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


def test_terminal_ports_pi_progress_sequences() -> None:
    terminal = FakeTerminal(columns=40)

    terminal.set_progress(True)
    terminal.setProgress(False)

    assert terminal.writes == ["\x1b]9;4;3\x07", "\x1b]9;4;0;\x07"]


def test_process_terminal_ports_pi_progress_keepalive_and_clear() -> None:
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


def test_process_terminal_ports_pi_start_stop_progress_cleanup() -> None:
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

    monkeypatch.delenv("APPV231_TUI_MOUSE", raising=False)
    monkeypatch.delenv("APPV231_SANDBOX", raising=False)
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

    monkeypatch.delenv("APPV231_TUI_MOUSE", raising=False)
    monkeypatch.setenv("APPV231_SANDBOX", "1")
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

    monkeypatch.setenv("APPV231_SANDBOX", "1")
    monkeypatch.setenv("APPV231_TUI_MOUSE", "0")
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

    monkeypatch.setenv("APPV231_TUI_MOUSE", "1")
    terminal = RecordingProcessTerminal()

    terminal.start(lambda data: None, lambda: None)
    terminal.stop()

    assert "\x1b[?1000h\x1b[?1006h" in terminal.writes
    assert "\x1b[?1006l\x1b[?1000l" in terminal.writes


def test_process_terminal_ports_pi_utf8_text_decoding_before_stdin_buffer() -> None:
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


def test_tui_ports_pi_start_stop_terminal_lifecycle() -> None:
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


def test_interactive_mode_ports_pi_tui_lifecycle_on_run_exit(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    exit_code = mode.run()

    assert exit_code == 0
    assert terminal.writes[0] == "\x1b[?2004h"
    assert terminal.writes[1] == "\x1b[?25l"
    assert terminal.writes[-2:] == ["\x1b[?25h", "\x1b[?2004l"]


def test_tui_stop_ports_pi_drain_input_before_terminal_restore() -> None:
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


def test_interactive_mode_persists_pi_prompt_history_between_editors(tmp_path) -> None:
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

    assert aborts == ["agent", "bash"]
    assert mode.status._message == "Aborting"


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
    finally:
        allow_finish.set()
        mode._wait_for_active_turn()

    assert stopped is True
    assert provider_finished.is_set() is False
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


def test_tui_ports_pi_input_listener_transform_consume_and_unsubscribe() -> None:
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
    tui.addInputListener(consume)
    tui.start()

    assert terminal.input_handler is not None
    terminal.input_handler("a")

    unsubscribe_transform()
    tui.removeInputListener(consume)
    terminal.input_handler("b")

    assert events == [("transform", "a"), ("consume", "a!")]


def test_tui_ports_pi_terminal_input_to_focused_component() -> None:
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

    tui.setFocus(None)
    assert editor.focused is False


def test_tui_ports_pi_invisible_focused_overlay_redirects_to_visible_capturing_overlay() -> None:
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
    tui.showOverlay(fallback)
    tui.showOverlay(non_capturing, {"nonCapturing": True})
    tui.showOverlay(primary, {"visible": lambda _width, _height: is_visible})
    assert primary.focused is True

    is_visible = False
    assert terminal.input_handler is not None
    terminal.input_handler("x")

    assert fallback.events == ["x"]
    assert non_capturing.events == []
    assert primary.events == []
    assert fallback.focused is True


def test_tui_ports_pi_key_release_filtering_for_focused_component() -> None:
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


def test_tui_ports_pi_terminal_output_normalization_without_mutating_lines() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("กำ ກຳ"))

    info = tui.request_render()

    assert info.lines == ["กำ ກຳ"]
    assert "กํา ກໍາ" in terminal.output
    assert "กำ ກຳ" not in terminal.output


def test_tui_ports_pi_line_resets_after_terminal_output_lines() -> None:
    terminal = FakeTerminal(columns=40)
    tui = TUI(terminal)
    tui.add(Text("\x1b[3mItalic"))
    tui.add(Text("Plain"))

    info = tui.request_render()

    assert info.lines == ["\x1b[3mItalic", "Plain"]
    assert "\x1b[3mItalic\x1b[0m\x1b]8;;\x07\r\nPlain\x1b[0m\x1b]8;;\x07" in terminal.output


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
    from appv231.ai.types import ToolCall

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


def test_select_list_ports_pi_ctrl_c_cancel_keybinding() -> None:
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


def test_select_list_ports_pi_selection_change_and_public_api() -> None:
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
    select.onSelectionChange = lambda item: changed.append(item.value)
    select.onSelect = lambda item: selected.append(item.value)
    select.onCancel = lambda: cancelled.append(True)

    select.handle_input("\x1b[B")
    assert changed == ["beta"]
    assert select.getSelectedItem() == SelectItem(value="beta", label="Beta")

    select.setSelectedIndex(99)
    assert select.getSelectedItem() == SelectItem(value="gamma", label="Gamma")

    select.setFilter("be")
    assert select.getSelectedItem() == SelectItem(value="beta", label="Beta")

    select.handle_input("\r")
    select.handle_input("\x03")
    assert selected == ["beta"]
    assert cancelled == [True]


def test_select_list_ports_pi_display_value_and_description_normalization() -> None:
    select = SelectList(
        [
            SelectItem(value="alpha", label="", description="line one\nline two"),
        ],
        max_visible=1,
    )

    assert select.render(80) == ["→ alpha" + (" " * 27) + "line one line two"]


def test_select_list_ports_pi_layout_alignment_and_truncation_hook() -> None:
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


def test_settings_list_ports_pi_search_cycle_cancel_and_submenu() -> None:
    setKeybindings(KeybindingsManager(TUI_KEYBINDINGS))
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

    settings.handleInput("\r")
    assert changes == [("theme", "light")]
    assert "light" in "\n".join(settings.render(48))

    settings.updateValue("theme", "dark")
    assert "dark" in "\n".join(settings.render(48))

    settings.handleInput("a")
    filtered = "\n".join(settings.render(48))
    assert "API key" in filtered
    assert "Theme" not in filtered

    settings.handleInput("\x1b")
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
    submenu_settings.handleInput("\r")
    assert submenu_settings.render(40) == ["submenu"]
    submenu_settings.handleInput("s")
    assert submenu_changes == [("mode", "selected")]
    assert "selected" in "\n".join(submenu_settings.render(40))


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


def test_input_ports_pi_on_escape_cancel_keybinding() -> None:
    input_component = Input(value="draft")
    cancelled: list[str] = []
    input_component.onEscape = lambda: cancelled.append("escape")

    input_component.handle_input("\x1b")

    assert cancelled == ["escape"]
    assert input_component.get_value() == "draft"

    input_component.onEscape = None
    input_component.on_escape = lambda: cancelled.append("ctrl+c")
    input_component.handle_input("\x03")

    assert cancelled == ["escape", "ctrl+c"]
    assert input_component.get_value() == "draft"


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


def test_input_ports_pi_grapheme_cursor_and_delete_behavior() -> None:
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


def test_input_ports_pi_up_down_prompt_history_navigation() -> None:
    input_component = Input(value="draft")
    input_component.addToHistory("first")
    input_component.addToHistory("second")
    input_component.addToHistory("second")

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


def test_input_ports_pi_alt_delete_delete_word_forward_keybinding() -> None:
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


def test_input_ports_pi_ctrl_b_ctrl_f_cursor_navigation() -> None:
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


def test_input_ports_pi_alternate_home_end_key_sequences() -> None:
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


def test_input_ports_pi_modified_arrow_word_navigation() -> None:
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
    from appv231.tui import AssistantMessageComponent

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


def test_tool_execution_accepts_component_render_call_like_pi() -> None:
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


def test_tool_execution_accepts_component_render_result_like_pi() -> None:
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


def test_user_and_skill_invocation_components_render_like_pi() -> None:
    from appv231.tui import SkillInvocationMessageComponent, UserMessageComponent, parse_skill_block

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
    from appv231.tui import message_to_component

    message = UserMessage(
        content=(
            '<skill name="tui" location="/skills/tui/SKILL.md">\n'
            "Render with boxes.\n"
            "</skill>\n\n"
            "Now update appv231."
        ),
        timestamp=now_ms(),
    )

    component = message_to_component(message)
    assert component is not None
    rendered = strip_ansi("\n".join(component.render(100)))

    assert "[skill] tui" in rendered
    assert "Render with boxes." not in rendered
    assert "Now update appv231." in rendered
    assert "> Now update" not in rendered


def test_bash_execution_component_renders_status_and_output() -> None:
    from appv231.tui import BashExecutionComponent, message_to_component

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
    from appv231.tui import (
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


def test_interactive_mode_reports_unknown_slash_command_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/does-not-exist", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "Unknown command: /does-not-exist" in rendered
    assert "Type /help for available commands." in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_runs_delegate_command_through_turn_thread(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    def backend(task):
        return "delegate command ok"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("test", backend))
    inputs = iter(["/delegate --backend test reviewer inspect package.json", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls["model"] == 0
    assert "delegate command ok" in rendered
    assert "Unknown command: /delegate" not in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_does_not_run_delegate_command_on_input_thread(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()

    assert mode._dispatch_extension_command("/delegate reviewer inspect package.json") is False


def test_interactive_mode_runs_agents_command_during_active_turn_without_queueing(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    stop = threading.Event()
    active_turn = threading.Thread(target=stop.wait)

    mode.init()
    active_turn.start()
    try:
        with mode._turn_lock:
            mode._turn_thread = active_turn

        assert mode._handle_active_turn_prompt("/agents") is True

        rendered = strip_ansi("\n".join(app.tui.render(120)))
        assert "No subagents have been spawned" in rendered
        assert "Queued message for after current turn" not in rendered
        assert mode._queued_after_turn == []
    finally:
        stop.set()
        active_turn.join(timeout=1)


def test_interactive_mode_runs_agents_command_while_subagent_tool_waits(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    provider_calls = {"n": 0}
    provider_context_texts: list[str] = []

    def script(model, context):
        provider_calls["n"] += 1
        context_text_parts: list[str] = []
        for message in context.messages:
            content = getattr(message, "content", [])
            if isinstance(content, str):
                context_text_parts.append(content)
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    context_text_parts.append(str(text))
        provider_context_texts.append("\n".join(context_text_parts))
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {"role": "reviewer", "goal": "slow review", "wait": True, "timeoutSeconds": 30},
            )
        return text_response_events(model, "parent done")

    def slow_backend(task):
        started.set()
        release.wait(2)
        return "child done"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    prompt_count = {"n": 0}

    def input_fn(_prompt: str) -> str:
        prompt_count["n"] += 1
        if prompt_count["n"] == 1:
            return "spawn a slow reviewer subagent"
        if prompt_count["n"] == 2:
            assert started.wait(timeout=2)
            return "/agents"
        release.set()
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert provider_calls["n"] == 2
    assert "Subagents:" in rendered
    assert "reviewer" in rendered
    assert "running - slow review" in rendered
    assert "Queued message for after current turn" not in rendered
    assert "parent done" in rendered
    assert all("Subagents:" not in text for text in provider_context_texts)


def test_interactive_mode_live_terminal_runs_agents_command_while_subagent_tool_waits(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    provider_calls = {"n": 0}

    def script(model, context):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {"role": "reviewer", "goal": "slow review", "wait": True, "timeoutSeconds": 30},
            )
        return text_response_events(model, "parent done")

    def slow_backend(task):
        started.set()
        release.wait(2)
        return "child done"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
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

        terminal.input_handler("spawn a slow reviewer subagent\r")
        assert started.wait(timeout=2)
        assert mode._is_turn_active()

        terminal.input_handler("/agents\r")

        assert _wait_until(
            lambda: "Subagents:" in strip_ansi(terminal.output)
            and "running - slow review" in strip_ansi(terminal.output),
            timeout=0.5,
        )

        release.set()
        assert _wait_until(lambda: not mode._is_turn_active() and mode.active_editor is not None, timeout=2)
        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        release.set()
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0
    assert provider_calls["n"] == 2


def test_interactive_mode_live_terminal_ctrl_c_cancels_waiting_subagent_tool(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    provider_calls = {"n": 0}

    def script(model, context):
        provider_calls["n"] += 1
        return tool_call_response_events(
            model,
            "spawn_subagent",
            {"role": "reviewer", "goal": "slow review", "wait": True, "timeoutSeconds": 30},
        )

    def slow_backend(task):
        started.set()
        release.wait(2)
        return "late summary"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
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

        terminal.input_handler("spawn a slow reviewer subagent\r")
        assert started.wait(timeout=2)
        assert mode._is_turn_active()

        terminal.input_handler("\x03")

        assert _wait_until(
            lambda: not mode._is_turn_active()
            and mode.status._message == "Idle"
            and mode.active_editor is not None,
            timeout=2,
        )
        results = app.session.subagents.list_results()
        assert len(results) == 1
        assert results[0].status == "cancelled"
        assert results[0].errors == ["Cancelled by parent abort."]

        release.set()
        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        release.set()
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0
    assert provider_calls["n"] == 1


def test_interactive_mode_hides_successful_subagent_tool_trace_event(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode._handle_session_event(
        {
            "type": "subagent_tool_end",
            "taskId": "subagent-fixed",
            "role": "reviewer",
            "toolName": "read",
            "status": "ok",
            "argsPreview": "path=child.md",
            "resultPreview": "child trace body",
        }
    )

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "subagent reviewer read ok path=child.md" not in rendered
    assert "child trace body" not in rendered


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
    assert {"tokens", "contextWindow", "percent"}.issubset(context_usage)
    assert isinstance(context_usage.get("confidence"), str)
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
    assert "\nready" in rendered
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
    assert "faux-model" in rendered
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
        captured.append(ctx["ui"].input("Project name", "appv231"))

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
    assert prompts == ["Project name (appv231): "]
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


def test_interactive_mode_hides_existing_thinking_content_by_default(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        AssistantMessage(
            content=[
                ThinkingContent(thinking="private replayed chain of thought"),
                TextContent(text="Visible replayed answer"),
            ],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
    ]

    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Visible replayed answer" in rendered
    assert "private replayed chain of thought" not in rendered
    assert "Thinking:" not in rendered


def test_interactive_mode_extension_shortcut_can_set_terminal_title(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_title(ctx):
        ctx["ui"].setTitle("appv231 - workspace")

    app.session.extension_runner.register_shortcut(
        "ctrl+shift+t",
        {"description": "Set title", "handler": set_title},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+shift+t") is True

    assert "\x1b]0;appv231 - workspace\x07" in terminal.output


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
    assert "faux-model" in restored
    assert "\nready" in restored


def test_interactive_footer_data_provider_ports_pi_nested_git_branch_and_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "nested"
    git_dir = repo / ".git"
    nested.mkdir(parents=True)
    git_dir.mkdir()
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n")

    app = CodingApp(cwd=str(nested), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    provider = mode.footer_data_provider

    try:
        assert provider.getGitBranch() == "main"
        assert provider.get_available_provider_count() == 0

        seen: list[str | None] = []
        unsubscribe = provider.onBranchChange(lambda: seen.append(provider.getGitBranch()))
        head.write_text("ref: refs/heads/feature\n")
        provider.refresh_git_branch()
        unsubscribe()
        head.write_text("ref: refs/heads/ignored\n")
        provider.refresh_git_branch()

        assert provider.get_git_branch() == "ignored"
        assert seen == ["feature"]
    finally:
        provider.dispose()


def test_interactive_footer_data_provider_ports_pi_worktree_and_detached_resolution(tmp_path) -> None:
    common_git_dir = tmp_path / "repo" / ".git"
    git_dir = common_git_dir / "worktrees" / "src"
    worktree = tmp_path / "worktree"
    git_dir.mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/worktree-branch\n")
    (git_dir / "commondir").write_text("../..\n")

    app = CodingApp(cwd=str(worktree), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    try:
        assert mode.footer_data_provider.getGitBranch() == "worktree-branch"
    finally:
        mode.footer_data_provider.dispose()

    detached = tmp_path / "detached"
    detached_git_dir = detached / ".git"
    detached_git_dir.mkdir(parents=True)
    (detached_git_dir / "HEAD").write_text("abcdef123456\n")
    detached_app = CodingApp(cwd=str(detached), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    detached_mode = InteractiveMode(detached_app, input_fn=lambda prompt: "/exit")
    try:
        assert detached_mode.footer_data_provider.getGitBranch() == "detached"
    finally:
        detached_mode.footer_data_provider.dispose()


def test_interactive_mode_builtin_footer_renders_pi_git_branch(tmp_path) -> None:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    repo.mkdir()
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    app = CodingApp(cwd=str(repo), model=faux_model(), terminal=FakeTerminal(columns=360), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(360)))
        assert f"{repo} (main)" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_footer_data_provider_auto_refreshes_head_changes_and_rerenders(tmp_path) -> None:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    repo.mkdir()
    git_dir.mkdir()
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n")
    app = CodingApp(cwd=str(repo), model=faux_model(), terminal=FakeTerminal(columns=360), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    seen: list[str | None] = []

    mode.init()
    unsubscribe = mode.footer_data_provider.onBranchChange(lambda: seen.append(mode.footer_data_provider.getGitBranch()))
    try:
        assert mode.footer_data_provider.getGitBranch() == "main"

        head.write_text("ref: refs/heads/feature\n")

        assert _wait_until(
            lambda: seen == ["feature"]
            and f"{repo} (feature)" in strip_ansi("\n".join(app.tui.render(360)))
        )
    finally:
        unsubscribe()
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_mode_footer_ports_pi_available_provider_count_for_scoped_models(tmp_path) -> None:
    primary = faux_model()
    secondary = faux_model(api="other")
    secondary.provider = "other"
    secondary.id = "other-model"
    secondary.name = "Other"
    app = CodingApp(
        cwd=str(tmp_path),
        model=primary,
        scoped_models=[ScopedModel(model=primary), ScopedModel(model=secondary)],
        terminal=FakeTerminal(columns=140),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(140)))

        assert mode.footer_data_provider.getAvailableProviderCount() == 2
        assert "(faux) faux-model" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_mode_footer_ports_pi_usage_stats_from_session_messages(tmp_path) -> None:
    model = faux_model()
    model.context_window = 200_000
    usage = Usage(input=12345, output=6789, cache_read=50, cache_write=50)
    usage.cost = Cost(total=1.234)
    assistant = AssistantMessage(
        content=[TextContent(text="done")],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=usage,
        stop_reason="stop",
        timestamp=now_ms(),
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(columns=160), enable_tui=True)
    app.session.agent.state.messages = [assistant]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert "↑12k ↓6.8k R50 W50 CH0.4% $1.234" in rendered
        assert "faux-model" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_mode_footer_ports_pi_unknown_context_usage(tmp_path) -> None:
    model = faux_model()
    model.context_window = 200_000
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(columns=160), enable_tui=True)
    app.session.get_context_usage = lambda: {"tokens": None, "contextWindow": 200_000, "percent": None}
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert "?/200k (auto)" in rendered
        assert "0.0%/200k" not in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_mode_footer_marks_estimated_context_usage_with_tilde(tmp_path) -> None:
    model = faux_model()
    model.context_window = 200_000
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(columns=160), enable_tui=True)
    app.session.get_context_usage = lambda: {
        "tokens": 20_000,
        "contextWindow": 200_000,
        "percent": 10.0,
        "estimated": True,
    }
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert "~10.0%/200k (auto)" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_mode_footer_ports_pi_session_name_updates(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=160), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        app.session.set_session_name("work session")
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert f"{tmp_path} • work session" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


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
    assert "appv231 pi+hermes TUI" not in rendered
    assert custom_headers and custom_headers[-1].disposed is False

    assert mode._dispatch_extension_shortcut("ctrl+shift+g") is True

    restored = strip_ansi("\n".join(app.tui.render(140)))
    assert custom_headers[-1].disposed is True
    assert "custom header" not in restored
    assert "appv231 pi+hermes TUI" in restored


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
    assert second.first_changed == 2
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
    assert "appv231" in rendered
    assert "Current working directory:" in rendered
    assert "hi" in rendered
    assert "> hi" not in rendered
    assert "tui reply" in rendered
    assert '{"type":' not in rendered
    assert input_prompts == ["", ""]


def test_interactive_mode_queues_prompt_while_turn_is_streaming(tmp_path) -> None:
    first_stream_started = threading.Event()
    first_stream_released = threading.Event()
    first_stream_finished = threading.Event()
    second_input_requested = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        events = text_response_events(model, f"turn {stream_calls['n']}")
        if stream_calls["n"] > 1:
            provider = create_faux_provider(lambda m, c: events)
            return provider.stream_simple(model, context, options)

        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        first_stream_started.set()

        def finish() -> None:
            first_stream_released.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)
            first_stream_finished.set()

        threading.Thread(target=finish, daemon=True).start()
        return stream

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=100)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    input_calls = {"n": 0}

    def input_fn(prompt: str) -> str:
        index = input_calls["n"]
        input_calls["n"] += 1
        if index == 0:
            return "first"
        if index == 1:
            second_input_requested.set()
            return "second"
        if index == 2:
            first_stream_finished.wait(timeout=2)
            return "/exit"
        raise EOFError

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert first_stream_started.wait(timeout=2)
    try:
        assert second_input_requested.wait(timeout=0.25)
        assert app.session.get_steering_messages() == ["second"]
        assert app.session.pending_message_count == 1
    finally:
        first_stream_released.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_interactive_mode_runs_bang_bash_while_turn_is_streaming(tmp_path) -> None:
    first_stream_started = threading.Event()
    first_stream_released = threading.Event()
    first_stream_finished = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        events = text_response_events(model, "turn")
        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        first_stream_started.set()

        def finish() -> None:
            first_stream_released.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)
            first_stream_finished.set()

        threading.Thread(target=finish, daemon=True).start()
        return stream

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=100)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["first", "! printf streamed", "/exit"])

    def input_fn(prompt: str) -> str:
        value = next(inputs)
        if value == "! printf streamed":
            assert first_stream_started.wait(timeout=2)
        if value == "/exit":
            first_stream_finished.wait(timeout=2)
        return value

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert first_stream_started.wait(timeout=2)
    try:
        assert _wait_until(lambda: app.session.has_pending_bash_messages is True, timeout=2)
        assert app.session.get_steering_messages() == []
        assert mode.status._message == "Running"
    finally:
        first_stream_released.set()
        thread.join(timeout=2)
    assert not thread.is_alive()

    rendered = strip_ansi("\n".join(app.tui.render(100)))
    assert "$ printf streamed" in rendered
    assert "streamed" in rendered


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
    footer_index = next(index for index, line in enumerate(rendered) if "faux-model" in strip_ansi(line))

    assert prompt_index < reply_index < status_index < footer_index


def test_interactive_mode_labels_post_response_compaction_after_reply(tmp_path) -> None:
    compression_started = threading.Event()
    release_compression = threading.Event()

    def script(model, context):
        events = text_response_events(model, "reply before compaction")
        events[-1].message.usage.total_tokens = 200_000
        return events

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        enable_tui=True,
        context_length=100_000,
    )
    original_compact_post_response = app._compact_post_response

    def blocking_compact_post_response() -> None:
        compression_started.set()
        release_compression.wait(timeout=2)
        original_compact_post_response()

    app._compact_post_response = blocking_compact_post_response
    input_calls = {"n": 0}
    allow_exit_input = threading.Event()

    def input_fn(prompt: str) -> str:
        input_calls["n"] += 1
        if input_calls["n"] == 1:
            return "hi"
        allow_exit_input.wait(timeout=2)
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert compression_started.wait(timeout=2)
    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert "reply before compaction" in rendered
    assert "status: Compressing" in rendered
    assert "status: Running" not in rendered

    release_compression.set()
    allow_exit_input.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_interactive_mode_auto_compaction_notice_uses_actual_compaction_boundary_tokens(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=120), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    app.compaction.compressor.compression_count = 1
    app.compaction.last_compression_before_tokens = 50_000
    app.compaction.last_compression_after_tokens = 12_000

    mode.init()
    mode._render_auto_compaction_notice(before_compressions=0, before_tokens=8)

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert "Context compacted: ~50,000 -> ~12,000 tokens" in rendered
    assert "Context compacted: ~8 ->" not in rendered


def test_interactive_mode_footer_marks_context_rough_while_awaiting_real_usage(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=120), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    app.compaction.awaiting_real_usage_after_compression = True

    mode._refresh_footer()

    assert mode.footer.context_percent is None
    assert mode.footer.context_percent_unknown is True
    rendered = strip_ansi("\n".join(mode.footer.render(120)))
    assert "~0.0%/" in rendered
    assert "?/" not in rendered


def test_interactive_mode_renders_auto_retry_status_instead_of_stale_running(tmp_path) -> None:
    retry_started = threading.Event()
    allow_exit = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        if stream_calls["n"] == 1:
            stream = create_assistant_message_event_stream()
            error_message = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message="Provider finish_reason: network_error",
                timestamp=now_ms(),
            )
            stream.push(ErrorEvent(reason="error", error=error_message))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "retry recovered")).stream_simple(
            model, context, options
        )

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.set_auto_retry_enabled(True)
    app.session._max_retries = 1
    app.session._retry_delay_ms = 5000
    app.session.subscribe(lambda event: retry_started.set() if event.type == "auto_retry_start" else None)
    input_calls = {"n": 0}

    def input_fn(prompt: str) -> str:
        input_calls["n"] += 1
        if input_calls["n"] == 1:
            return "hi"
        allow_exit.wait(timeout=2)
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert retry_started.wait(timeout=2)
    try:
        assert mode.status._message.startswith("Retrying (1/1) in 5s")
        assert mode.status._message != "Running"
    finally:
        app.session.abort_retry()
        allow_exit.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


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
        return {
            "operations": BashOperations(exec=exec_command),
            "commandPrefix": "source ~/.profile",
        }

    app.session.extension_runner.on("user_bash", handle_user_bash)
    inputs = iter(["! printf from-shell", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert exec_calls == [("source ~/.profile\nprintf from-shell", str(tmp_path))]
    assert "from custom operations" in rendered
    assert bash_messages[-1].command == "printf from-shell"
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
    assert "%/" in rendered
    assert "faux-model" in rendered


def test_interactive_mode_manual_compress_failure_resets_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def fail_manual_compress(*args, **kwargs):
        raise RuntimeError("summary provider stuck")

    app.session.compact = fail_manual_compress
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode._run_manual_compress("/compress")

    rendered = "\n".join(app.tui.render(140))
    assert mode.status._message == "Idle"
    assert "compact: Compression failed: summary provider stuck" in rendered
    assert "status: Compressing" not in rendered


def test_interactive_mode_manual_compress_routes_deep_mode_through_session(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    calls: list[tuple[str | None, bool]] = []

    def fake_compact(focus=None, summarizer=None, deep=False):
        calls.append((focus, deep))
        return ManualCompressionStatus(
            messages=app.messages,
            compressed=False,
            noop=True,
            headline="No changes from compression: 0 messages",
            token_line="Approx request size: ~0 tokens (unchanged)",
            focus=focus,
            deep=deep,
        )

    app.session.compact = fake_compact
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode._run_manual_compress("/compress deep code scan")
    mode._run_manual_compress("/compress aggressive database schema")

    rendered = "\n".join(app.tui.render(140))
    assert calls == [("code scan", True), ("aggressive database schema", False)]
    assert mode.status._message == "Idle"
    assert "compact: No changes from compression: 0 messages" in rendered
    assert "status: Compressing" not in rendered


def test_status_line_uses_signal_glass_theme_for_known_kinds(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    line = StatusLine("Compression complete", kind="compact")

    rendered = "\n".join(line.render(80))

    assert "\x1b[38;2;86;240;182m" in rendered
    assert strip_ansi(rendered) == "compact: Compression complete"


def test_status_line_respects_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    line = StatusLine("Compression complete", kind="compact")

    rendered = "\n".join(line.render(80))

    assert "\x1b[" not in rendered
    assert rendered == "compact: Compression complete"


def test_footer_uses_signal_glass_theme_without_changing_text(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    footer = FooterComponent(
        cwd=str(tmp_path),
        model="faux-model",
        provider="faux",
        context_window=128_000,
        context_percent=3.5,
    )

    rendered = "\n".join(footer.render(120))

    assert "\x1b[38;2;120;255;208m" in rendered
    plain = strip_ansi(rendered)
    assert "faux-model" in plain
    assert "3.5%/128k" in plain


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
    assert "%/" in rendered


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
    assert "typed-secret" not in rendered
    assert get_provider_auth_status("proxy") == {"configured": False}
    assert get_api_key_for_provider("proxy") is None
    assert "model should not run" not in rendered


def test_interactive_mode_login_api_key_offers_active_provider_without_registered_model(monkeypatch, tmp_path) -> None:
    agent_dir = tmp_path / "agent-home" / "agent"
    monkeypatch.setenv("APPV231_CODING_AGENT_DIR", str(agent_dir))
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    model = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    inputs = iter(["/login", "2", "1", "typed-secret", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Saved API key for OpenRouter" in rendered
    assert "typed-secret" not in rendered
    assert get_provider_auth_status("openrouter") == {"configured": True, "source": "stored"}
    assert get_api_key_for_provider("openrouter") == "typed-secret"
    stored = json.loads((agent_dir / "auth.json").read_text(encoding="utf-8"))
    assert stored == {"openrouter": {"type": "api_key", "key": "typed-secret"}}
    assert (agent_dir / "auth.json").stat().st_mode & 0o777 == 0o600
    assert "model should not run" not in rendered


def test_interactive_mode_footer_counts_active_provider_without_registered_model(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=140)
    model = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    mode.init()

    try:
        assert mode.footer_data_provider.getAvailableProviderCount() == 1
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()


def test_interactive_mode_model_command_switches_openrouter_without_model_turn(tmp_path) -> None:
    calls: list[object] = []

    def script(model, context):
        calls.append((model, context))
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    model = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    inputs = iter(["/model openrouter/moonshotai/kimi-k2.6", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls == []
    assert app.session.model.provider == "openrouter"
    assert app.session.model.id == "moonshotai/kimi-k2.6"
    assert "Switched model to openrouter/moonshotai/kimi-k2.6" in rendered


def test_interactive_mode_model_command_selects_registered_alternate_without_model_turn(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", lambda **kwargs: [])
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    alternate = Model(
        id="moonshotai/kimi-k2.6",
        name="moonshotai/kimi-k2.6",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    register_model(alternate)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert app.session.model is alternate
    assert "Select model:" in rendered
    assert "Switched model to openrouter/moonshotai/kimi-k2.6" in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_model_command_fetches_openrouter_catalog_for_picker(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    fetched = {"count": 0}

    def fake_live_models(*, base_model, force_refresh=False):
        fetched["count"] += 1
        items = [
            {
                "id": "moonshotai/kimi-k2.6",
                "name": "Kimi K2.6",
                "context_length": 262144,
                "top_provider": {"max_completion_tokens": 16384},
                "architecture": {"input_modalities": ["text"]},
                "supported_parameters": ["reasoning"],
            },
            {
                "id": "qwen/qwen3.6-flash",
                "name": "Qwen Flash",
                "context_length": 128000,
                "top_provider": {"max_completion_tokens": 8192},
                "architecture": {"input_modalities": ["text", "image"]},
            },
        ]
        return [model for item in items if (model := openrouter_live_catalog_item_to_model(item, base_model)) is not None]

    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", fake_live_models)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert fetched["count"] == 1
    assert app.session.model.provider == "openrouter"
    assert app.session.model.id == "moonshotai/kimi-k2.6"
    assert app.session.model.context_window == 262144
    assert app.session.model.max_tokens == 16384
    assert app.session.model.reasoning is True
    assert "Select model:" in rendered
    assert "openrouter/moonshotai/kimi-k2.6" in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_model_command_caps_openrouter_full_context_output_limit(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )

    def fake_live_models(*, base_model, force_refresh=False):
        items = [
            {
                "id": "qwen/qwen3.6-35b-a3b",
                "name": "Qwen 3.6 35B",
                "context_length": 262144,
                "top_provider": {"max_completion_tokens": 262144},
                "supported_parameters": ["max_tokens", "tools"],
            }
        ]
        return [model for item in items if (model := openrouter_live_catalog_item_to_model(item, base_model)) is not None]

    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", fake_live_models)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    assert app.session.model.id == "qwen/qwen3.6-35b-a3b"
    assert app.session.model.context_window == 262144
    assert app.session.model.max_tokens == 16384
    assert app.session.model.max_tokens < app.session.model.context_window


def test_interactive_mode_openrouter_catalog_uses_shared_model_metadata_converter(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )

    def fake_live_models(*, base_model, force_refresh=False):
        items = [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
                "supported_parameters": ["tools", "tool_choice", "reasoning"],
            }
        ]
        return [model for item in items if (model := openrouter_live_catalog_item_to_model(item, base_model)) is not None]

    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", fake_live_models)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    assert app.session.model.id == "openai/gpt-5.4-mini"
    assert app.session.model.context_window == 400000
    assert app.session.model.max_tokens == 128000
    assert app.session.model.reasoning is True


def test_interactive_mode_model_command_uses_shared_openrouter_live_cache(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    cached_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )
    calls = {"count": 0}

    def fake_live_models(*, base_model, force_refresh=False):
        calls["count"] += 1
        assert base_model is active
        assert force_refresh is False
        return [cached_model]

    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", fake_live_models)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    assert calls["count"] == 1
    assert app.session.model is cached_model


def test_interactive_mode_model_command_filters_openrouter_catalog_without_huge_picker(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=160)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )

    def fake_live_models(*, base_model, force_refresh=False):
        items = [
            {"id": f"acme/noise-{index}", "name": f"Noise {index}", "context_length": 4096}
            for index in range(80)
        ] + [
            {"id": "moonshotai/kimi-k2.6", "name": "Kimi K2.6", "context_length": 262144},
            {"id": "moonshotai/kimi-dev-72b", "name": "Kimi Dev", "context_length": 131072},
        ]
        return [model for item in items if (model := openrouter_live_catalog_item_to_model(item, base_model)) is not None]

    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", fake_live_models)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model kimi", "2", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(160)))
    assert app.session.model.id == "moonshotai/kimi-dev-72b"
    assert "openrouter/moonshotai/kimi-k2.6" in rendered
    assert "openrouter/moonshotai/kimi-dev-72b" in rendered
    assert "acme/noise-0" not in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_model_command_warns_and_falls_back_when_openrouter_fetch_fails(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )

    def fail_fetch(*, base_model, force_refresh=False):
        raise RuntimeError("network down")

    monkeypatch.setattr(interactive_mode, "get_live_openrouter_models", fail_fetch)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert app.session.model is active
    assert "Could not fetch OpenRouter models; showing local models only." in rendered
    assert "openrouter/qwen/qwen3.6-flash (current)" in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_model_command_warns_when_openrouter_catalog_fails_open(
    tmp_path, monkeypatch
) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    model_catalog.reset_cache()
    monkeypatch.setenv("APPV231_HOME", str(tmp_path / "app-home"))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )

    def fail_urlopen(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert app.session.model is active
    assert "Could not fetch OpenRouter models; showing local models only." in rendered
    assert "openrouter/qwen/qwen3.6-flash (current)" in rendered
    assert "model should not run" not in rendered


def test_interactive_mode_extension_select_uses_tui_input_when_interactive(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    mode.init()

    def fail_raw_input(prompt: str) -> str:
        raise AssertionError(f"raw input called: {prompt}")

    mode.input_fn = fail_raw_input
    result: dict[str, str | None] = {}
    error: dict[str, BaseException] = {}

    def prompt_for_selection() -> None:
        try:
            result["value"] = mode.prompt_extension_select("Pick auth method:", ("Use subscription", "Use API key"))
        except BaseException as exc:  # noqa: BLE001 - test thread captures assertion failures.
            error["value"] = exc

    thread = threading.Thread(target=prompt_for_selection)
    thread.start()
    deadline = time.monotonic() + 2
    while thread.is_alive() and mode.active_editor is None and time.monotonic() < deadline:
        time.sleep(0.01)
    app.tui._handle_terminal_input("2\r")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert error == {}
    assert result == {"value": "Use API key"}


def test_interactive_mode_extension_select_reports_invalid_numeric_choice(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "99")
    mode.init()

    result = mode.prompt_extension_select("Select model:", ("alpha", "beta"), kind="model")

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert result is None
    assert "Invalid selection: 99. Enter a number from 1 to 2." in rendered


def test_interactive_mode_extension_select_reports_blank_cancel(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "")
    mode.init()

    result = mode.prompt_extension_select("Select model:", ("alpha", "beta"), kind="model")

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert result is None
    assert "Selection cancelled." in rendered


def test_interactive_mode_coerces_read_numeric_string_like_pi_validation(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "file.py").write_text("print('ok')\n", encoding="utf-8")
    calls = {"count": 0}
    seen_tool_result = {"text": ""}

    def script(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(model, "read", {"path": "src/file.py", "limit": "100.0"})
        seen_tool_result["text"] = "\n".join(
            block.text
            for message in context.messages
            if getattr(message, "role", None) == "toolResult"
            for block in getattr(message, "content", [])
            if hasattr(block, "text")
        )
        return text_response_events(model, "handled read")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    app.run_turn("read src/file.py with limit 100")

    rendered = "\n".join(app.tui.render(120))
    assert "read src/file.py" in rendered
    assert "read.limit: expected number" not in rendered
    assert "Traceback" not in rendered
    assert "print('ok')" in seen_tool_result["text"]
    assert calls["count"] == 2


def test_pi_standalone_editor_helpers_are_exported_and_match_core_behavior() -> None:
    from appv231.tui import KillRing, UndoStack, findWordBackward, findWordForward, isNativeModifierPressed

    ring = KillRing()
    ring.push("foo", {"prepend": False})
    ring.push("bar", {"prepend": False, "accumulate": True})
    ring.push("pre-", {"prepend": True, "accumulate": True})
    assert ring.peek() == "pre-foobar"
    ring.push("older", {"prepend": False})
    assert ring.length == 2
    ring.rotate()
    assert ring.peek() == "pre-foobar"

    stack = UndoStack()
    state = {"items": [1]}
    stack.push(state)
    state["items"].append(2)
    assert stack.length == 1
    assert stack.pop() == {"items": [1]}
    assert stack.pop() is None
    stack.push({"value": "x"})
    stack.clear()
    assert stack.length == 0

    assert findWordBackward("foo bar", 7) == 4
    assert findWordForward("foo bar", 0) == 3
    assert findWordBackward("foo.bar", 7) == 4
    assert findWordForward("foo.bar", 0) == 3
    assert findWordForward("  word", 0) == 6
    assert findWordBackward("word  ", 6) == 0

    assert isNativeModifierPressed("shift") is False
    assert isNativeModifierPressed("command") is False


def test_strip_ansi_helper() -> None:
    assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"


def test_markdown_render_reuses_cached_lines_until_text_changes() -> None:
    markdown = Markdown("**bold** `code`\n- *item*")

    first = markdown.render(80)
    second = markdown.render(80)

    assert second is first
    assert first == ["bold code", "- item"]

    markdown.set_text("**changed**")
    changed = markdown.render(80)

    assert changed is not first
    assert changed == ["changed"]


def test_input_render_avoids_full_width_scan_for_long_ascii_tail(monkeypatch) -> None:
    import appv231.tui.component as component_module

    real_visible_width = component_module.visible_width
    checked_lengths: list[int] = []

    def guarded_visible_width(text: str) -> int:
        checked_lengths.append(len(text))
        assert len(text) < 500
        return real_visible_width(text)

    monkeypatch.setattr(component_module, "visible_width", guarded_visible_width)
    editor = Input("x" * 20_000, prompt="appv231> ")

    rendered = editor.render(100)

    assert len(rendered) == 1
    assert "x" in strip_ansi(rendered[0])
    assert max(checked_lengths) < 500


def test_interactive_mode_parses_params_command() -> None:
    from appv231.tui.interactive_mode import _parse_params_command

    assert _parse_params_command("/params") == ""


def test_interactive_mode_parses_params_filter() -> None:
    from appv231.tui.interactive_mode import _parse_params_command

    assert _parse_params_command("/params temperature") == "temperature"


def test_interactive_mode_params_command_displays_constructor_params(monkeypatch) -> None:
    class FakeSession:
        model = Model(id="step-3.7-flash", name="Step", api="openai-completions", provider="stepfun", base_url="")
        thinking_level = "off"
        session_name = "test"

        def subscribe(self, callback):
            return lambda: None

    class FakeApp:
        cwd = "."
        tui = TUI(FakeTerminal())
        session = FakeSession()
        messages = []

    mode = InteractiveMode(
        FakeApp(),
        generation_params=GenerationParams(
            temperature=0.2,
            max_tokens=4096,
            sources={"temperature": "cli", "max_tokens": "cli"},
        ),
    )
    shown: dict[str, str] = {}
    monkeypatch.setattr(mode, "_show_status", lambda message, kind="info": shown.update(message=message, kind=kind))

    mode._run_params_command("")

    assert shown["kind"] == "model"
    assert shown["message"] == "stepfun/step-3.7-flash: temperature=0.2 (cli), max_tokens=4096 (cli)"


def test_interactive_mode_params_command_displays_generation_param_warnings(monkeypatch) -> None:
    class FakeSession:
        model = Model(id="step-3.7-flash", name="Step", api="openai-completions", provider="stepfun", base_url="")
        thinking_level = "off"
        session_name = "test"

        def subscribe(self, callback):
            return lambda: None

    class FakeApp:
        cwd = "."
        tui = TUI(FakeTerminal())
        session = FakeSession()
        messages = []

    mode = InteractiveMode(
        FakeApp(),
        generation_params=GenerationParams(provider_sort="latency"),
        generation_param_warnings=[
            ProviderParamWarning(
                param="provider_sort",
                action="dropped",
                reason="stepfun does not support provider routing sort preferences.",
            )
        ],
    )
    shown: dict[str, str] = {}
    monkeypatch.setattr(mode, "_show_status", lambda message, kind="info": shown.update(message=message, kind=kind))

    mode._run_params_command("")

    assert shown["kind"] == "model"
    assert shown["message"] == (
        "stepfun/step-3.7-flash: provider_sort=latency; "
        "warnings: provider_sort dropped"
    )
