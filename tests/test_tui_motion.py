from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from tests._support_tui import (
    CodingApp,
    FakeTerminal,
    InteractiveMode,
    create_faux_provider,
    faux_model,
    register_api_provider,
    text_response_events,
)
from travis.tui.builtin_themes import resolve_builtin_theme
from travis.tui.components import StatusLine
from travis.tui.dispatcher import UiDispatcher
from travis.tui.interactive_command_dispatcher import _parse_motion_command
from travis.tui.motion import MotionController, MotionSnapshot, MotionState
from travis.tui.theme import ThemeContext
from travis.tui.utils import strip_ansi


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class MotionHarness:
    enabled: bool = True
    static: bool = False
    clock: FakeClock = field(init=False)
    renders: list[bool] = field(init=False)
    snapshots: list[MotionSnapshot] = field(init=False)
    dispatcher: UiDispatcher = field(init=False)
    controller: MotionController = field(init=False)

    def __post_init__(self) -> None:
        self.clock = FakeClock()
        self.renders = []
        self.snapshots = []
        self.dispatcher = UiDispatcher(
            render=lambda force=False: self.renders.append(force),
            clock=self.clock,
            render_interval=0,
        )
        self.controller = MotionController(
            schedule=self.dispatcher.call_later,
            on_change=self.snapshots.append,
            request_render=self.dispatcher.request_render,
            enabled=self.enabled,
            static=self.static,
        )


def test_idle_motion_schedules_nothing() -> None:
    harness = MotionHarness()

    assert harness.controller.state is MotionState.IDLE
    assert harness.controller.snapshot.indicator == ""
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(1.0)
    assert harness.snapshots == [harness.controller.snapshot]


def test_working_motion_advances_at_four_frames_per_second() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)
    first = harness.controller.snapshot

    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)

    harness.clock.advance(0.25)
    harness.dispatcher.drain()

    assert harness.controller.snapshot.indicator != first.indicator
    assert harness.controller.snapshot.generation > first.generation
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)
    assert harness.renders == [False, False]


def test_option_a_working_signal_keeps_three_suffix_dots_at_fixed_width() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)
    frames = [harness.controller.snapshot.indicator]

    for _ in range(2):
        harness.clock.advance(0.25)
        harness.dispatcher.drain()
        frames.append(harness.controller.snapshot.indicator)

    assert [strip_ansi(frame) for frame in frames] == ["...", "...", "..."]
    assert len(set(frames)) == 3


def test_option_a_tool_signal_is_one_fixed_width_spinner_glyph() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("tool", MotionState.TOOL)
    frames = [harness.controller.snapshot.indicator]

    for _ in range(3):
        harness.clock.advance(0.25)
        harness.dispatcher.drain()
        frames.append(harness.controller.snapshot.indicator)

    plain_frames = [strip_ansi(frame) for frame in frames]
    assert all(frame.startswith(" ") and len(frame) == 2 for frame in plain_frames)
    assert len(set(plain_frames)) == 4


def test_equivalent_signal_does_not_restart_the_frame_deadline() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)
    first = harness.controller.snapshot
    harness.clock.advance(0.1)

    harness.controller.set_signal("turn", MotionState.WORKING)

    assert harness.controller.snapshot == first
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(0.15)


def test_core_signal_outranks_extension_and_clear_restores_extension() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("extension", MotionState.EXTENSION)
    harness.controller.set_signal("turn", MotionState.WORKING)

    assert harness.controller.state is MotionState.WORKING

    harness.controller.clear_signal("turn")

    assert harness.controller.state is MotionState.EXTENSION


def test_high_priority_retry_suppresses_a_stale_working_tick() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)
    harness.clock.advance(0.1)
    harness.controller.set_signal("retry", MotionState.RETRY, countdown=3)
    retry_snapshot = harness.controller.snapshot

    harness.clock.advance(0.15)
    harness.dispatcher.drain()

    assert harness.controller.snapshot == retry_snapshot
    assert harness.dispatcher.time_until_next_work(2.0) == pytest.approx(0.85)


def test_retry_countdown_advances_once_per_second_and_settles_at_zero() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("retry", MotionState.RETRY, countdown=2)

    assert harness.controller.snapshot.indicator == " 2s"
    assert harness.controller.snapshot.countdown == 2
    assert harness.dispatcher.time_until_next_work(2.0) == pytest.approx(1.0)

    harness.clock.advance(1.0)
    harness.dispatcher.drain()
    assert harness.controller.snapshot.indicator == " 1s"
    assert harness.controller.snapshot.countdown == 1

    harness.clock.advance(1.0)
    harness.dispatcher.drain()
    assert harness.controller.snapshot.indicator == " 0s"
    assert harness.controller.snapshot.countdown == 0
    assert harness.dispatcher.time_until_next_work(2.0) == pytest.approx(2.0)


@pytest.mark.parametrize("state", [MotionState.SUCCESS, MotionState.ERROR])
def test_terminal_transition_runs_once_then_settles(state: MotionState) -> None:
    harness = MotionHarness()
    harness.controller.set_signal("terminal", state)

    while harness.dispatcher.time_until_next_work(1.0) < 1.0:
        harness.clock.advance(harness.dispatcher.time_until_next_work(1.0))
        harness.dispatcher.drain()

    settled = harness.controller.snapshot
    harness.clock.advance(1.0)
    harness.dispatcher.drain()

    assert harness.controller.snapshot == settled
    assert settled.indicator in {" ✓", " !"}


def test_disabled_motion_uses_static_frame_and_cancels_future_ticks() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)

    harness.controller.set_enabled(False)
    settled = harness.controller.snapshot
    harness.clock.advance(1.0)
    harness.dispatcher.drain()

    assert settled.indicator == "..."
    assert harness.controller.snapshot == settled
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(1.0)


def test_static_terminal_never_schedules_frames() -> None:
    harness = MotionHarness(static=True)

    harness.controller.set_signal("turn", MotionState.WORKING)

    assert harness.controller.snapshot.indicator == "..."
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(1.0)


def test_stop_is_idempotent_and_suppresses_late_callbacks() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)

    harness.controller.stop()
    harness.controller.stop()
    stopped = harness.controller.snapshot
    harness.clock.advance(1.0)
    harness.dispatcher.drain()

    assert stopped.state is MotionState.IDLE
    assert stopped.indicator == ""
    assert harness.controller.snapshot == stopped
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(1.0)


def test_motion_controller_isolates_presentation_callback_failures() -> None:
    clock = FakeClock()
    dispatcher = UiDispatcher(render=lambda force=False: None, clock=clock)
    changes = 0

    def broken_on_change(snapshot: MotionSnapshot) -> None:
        nonlocal changes
        changes += 1
        if snapshot.state is not MotionState.IDLE:
            raise RuntimeError("broken status component")

    def broken_render_request() -> None:
        raise RuntimeError("broken render request")

    controller = MotionController(
        schedule=dispatcher.call_later,
        on_change=broken_on_change,
        request_render=broken_render_request,
    )

    controller.set_signal("turn", MotionState.WORKING)
    clock.advance(0.25)
    dispatcher.drain()

    assert changes == 3
    assert controller.state is MotionState.WORKING
    assert dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)


def test_motion_types_are_exported_from_the_tui_package() -> None:
    import travis.tui as tui

    assert tui.MotionController is MotionController
    assert tui.MotionSnapshot is MotionSnapshot
    assert tui.MotionState is MotionState


def test_status_line_renders_exactly_one_motion_indicator_with_theme() -> None:
    theme, _diagnostics = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    status = StatusLine("Running", theme_context=ThemeContext(theme))

    status.set_indicator("..")
    rendered = "".join(status.render(80))

    assert strip_ansi(rendered) == "status: .. Running"
    assert theme.foreground_ansi["text"] in rendered


def test_status_line_renders_option_a_motion_after_the_semantic_label() -> None:
    status = StatusLine("Thinking")

    status.set_indicator("...", position="suffix")

    assert status.render(80) == ["status: Thinking..."]


def test_status_line_preserves_option_a_suffix_separator() -> None:
    status = StatusLine("Running bash")

    status.set_indicator(" ⠋", position="suffix")

    assert status.render(80) == ["status: Running bash ⠋"]


def test_interactive_runtime_owns_one_motion_controller(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TRAVIS234_MOTION", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        assert mode.motion_controller.enabled is True
        assert mode.motion_controller.state is MotionState.IDLE
        assert mode.status._indicator is None
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_option_a_active_turn_uses_thinking_label_with_fixed_suffix_dots(tmp_path) -> None:
    provider_started = threading.Event()
    release_provider = threading.Event()
    release_exit = threading.Event()

    def provider(model, context):
        provider_started.set()
        release_provider.wait(timeout=2)
        return text_response_events(model, "done")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    input_calls = 0

    def input_fn(_prompt: str) -> str:
        nonlocal input_calls
        input_calls += 1
        if input_calls == 1:
            return "hello"
        release_exit.wait(timeout=2)
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)
    run_thread = threading.Thread(target=mode.run)
    run_thread.start()

    try:
        assert provider_started.wait(timeout=2)
        assert mode.status._message == "Thinking"
        rendered = strip_ansi("\n".join(app.tui.render(100)))
        assert "status: Thinking..." in rendered
    finally:
        release_provider.set()
        release_exit.set()
        run_thread.join(timeout=2)
        app.close()

    assert not run_thread.is_alive()


def test_interactive_view_exposes_narrow_motion_signal_helpers(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode._set_motion_signal("turn", MotionState.WORKING)
        assert mode.motion_controller.state is MotionState.WORKING
        mode._clear_motion_signal("turn")
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_travis234_motion_zero_disables_runtime_motion(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRAVIS234_MOTION", "0")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        assert mode.motion_controller.enabled is False
        mode.motion_controller.set_signal("turn", MotionState.WORKING)
        assert mode.motion_controller.snapshot.indicator == "..."
        assert mode.tui.time_until_next_work(1.0) == pytest.approx(1.0)
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


@pytest.mark.parametrize(
    ("environment", "value"),
    [("NO_COLOR", "1"), ("TERM", "dumb")],
)
def test_plain_terminal_runtime_uses_static_motion(
    tmp_path,
    monkeypatch,
    environment: str,
    value: str,
) -> None:
    monkeypatch.delenv("TRAVIS234_MOTION", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv(environment, value)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode.motion_controller.set_signal("turn", MotionState.WORKING)
        assert mode.motion_controller.snapshot.indicator == "..."
        assert mode.tui.time_until_next_work(1.0) == pytest.approx(1.0)
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [("/motion", None), ("/motion on", True), ("/motion off", False)],
)
def test_parse_motion_command(prompt: str, expected: bool | None) -> None:
    assert _parse_motion_command(prompt) is expected


def test_motion_command_appears_in_help_and_autocomplete(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        suggestions = mode.create_base_autocomplete_provider().get_suggestions(
            ["/mot"],
            0,
            4,
        )
        assert suggestions is not None
        assert any(item["value"] == "motion" for item in suggestions["items"])

        mode._run_help_command()
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "/motion [on|off] - Inspect or change restrained TUI motion for this process." in history
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_motion_command_is_local_and_never_invokes_the_provider(tmp_path) -> None:
    calls = {"provider": 0}

    def provider(model, context):
        calls["provider"] += 1
        return text_response_events(model, "provider should not run")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    inputs = iter(["/motion off", "/motion", "/motion on", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda _prompt: next(inputs))

    try:
        assert mode.run() == 0
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "Motion disabled for this TUI process." in history
        assert "Motion is disabled for this TUI process." in history
        assert "Motion enabled for this TUI process." in history
        assert calls == {"provider": 0}
    finally:
        app.close()


def test_invalid_motion_command_reports_usage_without_a_model_turn(tmp_path) -> None:
    calls = {"provider": 0}

    def provider(model, context):
        calls["provider"] += 1
        return text_response_events(model, "provider should not run")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    inputs = iter(["/motion neon", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda _prompt: next(inputs))

    try:
        assert mode.run() == 0
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "Usage: /motion [on|off]" in history
        assert calls == {"provider": 0}
    finally:
        app.close()


def test_extension_footer_status_is_static_unless_it_reports_working(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode.set_extension_status("system", "CPU 42% · RAM 61%")
        assert mode.motion_controller.state is MotionState.IDLE

        mode.set_extension_status(
            "system",
            "CPU scan",
            {"state": "working"},
        )
        assert mode.motion_controller.state is MotionState.EXTENSION

        mode._set_motion_signal("turn", MotionState.WORKING)
        assert mode.motion_controller.state is MotionState.WORKING
        mode._clear_motion_signal("turn")
        assert mode.motion_controller.state is MotionState.EXTENSION

        mode.set_extension_status("system", None)
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_extension_ui_status_accepts_semantic_working_state(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )

    def report_working(ctx) -> None:
        ctx["ui"].set_status("monitor", "Scanning workspace", {"state": "working"})

    app.session.extension_runner.register_shortcut(
        "ctrl+m",
        {"description": "Monitor", "handler": report_working},
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode.init()
        assert mode._dispatch_extension_shortcut("ctrl+m") is True
        assert mode.extension_statuses == {"monitor": "Scanning workspace"}
        assert mode.motion_controller.state is MotionState.EXTENSION
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        mode.tui.stop()
        app.close()


def test_auto_retry_maps_to_countdown_motion_and_clears_on_end(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode._handle_session_event(
            SimpleNamespace(
                type="auto_retry_start",
                delay_ms=3_000,
                attempt=1,
                max_attempts=3,
            )
        )
        assert mode.motion_controller.state is MotionState.RETRY
        assert mode.motion_controller.snapshot.countdown == 3

        mode._handle_session_event(
            SimpleNamespace(
                type="auto_retry_end",
                success=True,
                attempt=1,
            )
        )
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_turn_is_working_while_provider_runs_and_controller_stops_on_exit(tmp_path) -> None:
    observed: list[MotionState] = []
    holder: dict[str, InteractiveMode] = {}
    provider_started = threading.Event()

    def provider(model, context):
        observed.append(holder["mode"].motion_controller.state)
        provider_started.set()
        return text_response_events(model, "done")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    prompt_count = 0

    def input_fn(_prompt: str) -> str:
        nonlocal prompt_count
        prompt_count += 1
        if prompt_count == 1:
            return "hello"
        assert provider_started.wait(timeout=2)
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)
    holder["mode"] = mode

    try:
        assert mode.run() == 0
        assert observed == [MotionState.WORKING]
        assert mode.motion_controller.state is MotionState.IDLE
        mode.motion_controller.set_signal("late", MotionState.WORKING)
        assert mode.motion_controller.state is MotionState.IDLE
        assert mode.tui.time_until_next_work(1.0) == pytest.approx(1.0)
    finally:
        app.close()


def test_manual_compaction_uses_maintenance_motion_and_clears_in_finally(
    tmp_path,
    monkeypatch,
) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    observed: list[MotionState] = []

    def compact(*, focus=None, deep=False):
        observed.append(mode.motion_controller.state)
        return SimpleNamespace(
            headline="Compressed",
            token_line="Approx request size: 10",
            note=None,
            warning=None,
            info=None,
        )

    monkeypatch.setattr(app.session, "compact", compact)
    try:
        mode._run_manual_compress("/compact")

        assert observed == [MotionState.MAINTENANCE]
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_reload_uses_maintenance_motion_and_clears_after_completion(
    tmp_path,
    monkeypatch,
) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    observed: list[MotionState] = []

    monkeypatch.setattr(
        app.session,
        "reload",
        lambda: observed.append(mode.motion_controller.state),
    )
    try:
        mode._run_reload_command()

        assert observed == [MotionState.MAINTENANCE]
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_reload_clears_maintenance_motion_after_late_presentation_failure(
    tmp_path,
    monkeypatch,
) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    monkeypatch.setattr(app.session, "reload", lambda: None)
    monkeypatch.setattr(
        mode.theme_registry,
        "reload",
        lambda _themes: (_ for _ in ()).throw(RuntimeError("broken theme registry")),
    )

    try:
        with pytest.raises(RuntimeError, match="broken theme registry"):
            mode._run_reload_command()
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_pending_agent_tools_publish_one_tool_signal_from_footer_refresh(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        app.session.agent.state.pending_tool_calls.add("tool-1")
        mode._refresh_footer()
        assert mode.motion_controller.state is MotionState.TOOL

        app.session.agent.state.pending_tool_calls.clear()
        mode._refresh_footer()
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_user_bash_uses_tool_motion_until_the_command_settles(
    tmp_path,
    monkeypatch,
) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    observed: list[MotionState] = []

    def start(command, binding):
        observed.append(mode.motion_controller.state)
        return SimpleNamespace(command_id="command-1")

    assert mode._user_commands is not None
    monkeypatch.setattr(mode._user_commands, "start", start)
    try:
        mode._run_bash_command("printf hi", exclude_from_context=False)
        assert observed == [MotionState.TOOL]
        assert mode.motion_controller.state is MotionState.TOOL

        mode._fail_user_command("command-1", "test completion")
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_package_mutation_uses_maintenance_motion_and_clears_after_reload(
    tmp_path,
    monkeypatch,
) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    observed: list[MotionState] = []
    manager = app.session.resource_loader.package_manager

    def install(source, *, scope):
        observed.append(mode.motion_controller.state)
        return SimpleNamespace(source=SimpleNamespace(raw=str(source)))

    monkeypatch.setattr(manager, "install", install)
    monkeypatch.setattr(mode, "prompt_extension_confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(mode, "_run_reload_command", lambda: None)
    try:
        assert mode._run_package_command("/install example/package") is True
        assert observed == [MotionState.MAINTENANCE]
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_extension_working_message_uses_shared_extension_signal(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode.set_working_message("Indexing workspace")
        assert mode.motion_controller.state is MotionState.EXTENSION

        mode.set_working_visible(False)
        assert mode.motion_controller.state is MotionState.IDLE

        mode.set_working_visible(True)
        assert mode.motion_controller.state is MotionState.EXTENSION

        mode.set_working_indicator({"frames": ["*", "+"]})
        assert mode.status._indicator == "*"
        assert mode.motion_controller.state is MotionState.EXTENSION

        mode.set_working_message()
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()


def test_motion_ticks_leave_context_compaction_and_native_scrollback_unchanged(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode.init()
        messages_before = tuple(app.messages)
        usage_before = app.session.get_context_usage()
        compression_count_before = app.compaction.compressor.compression_count
        history_before = tuple(mode.history.render(100))
        full_redraws_before = mode.tui.full_redraws
        generation_before = mode.motion_controller.snapshot.generation

        mode._set_motion_signal("isolation", MotionState.WORKING)
        deadline = time.monotonic() + 1.0
        while mode.motion_controller.snapshot.generation < generation_before + 2:
            if time.monotonic() >= deadline:
                pytest.fail("motion frame did not advance before the isolation deadline")
            time.sleep(0.01)
            mode.tui.drain_dispatcher()

        assert tuple(app.messages) == messages_before
        assert app.session.get_context_usage() == usage_before
        assert app.compaction.compressor.compression_count == compression_count_before
        assert tuple(mode.history.render(100)) == history_before
        assert mode.tui.full_redraws == full_redraws_before
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        mode.tui.stop()
        app.close()


def test_motion_core_has_no_agent_provider_compaction_or_coding_agent_imports() -> None:
    source_path = Path(__file__).parents[1] / "travis" / "tui" / "motion.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)

    forbidden = (
        "travis.agent",
        "travis.ai",
        "travis.compaction",
        "travis.coding_agent",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden
    )
