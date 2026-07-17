from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from tests._support_tui import CodingApp, FakeTerminal, InteractiveMode, faux_model
from travis.tui.builtin_themes import resolve_builtin_theme
from travis.tui.components import StatusLine
from travis.tui.dispatcher import UiDispatcher
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

    assert harness.controller.snapshot.indicator == "2s"
    assert harness.controller.snapshot.countdown == 2
    assert harness.dispatcher.time_until_next_work(2.0) == pytest.approx(1.0)

    harness.clock.advance(1.0)
    harness.dispatcher.drain()
    assert harness.controller.snapshot.indicator == "1s"
    assert harness.controller.snapshot.countdown == 1

    harness.clock.advance(1.0)
    harness.dispatcher.drain()
    assert harness.controller.snapshot.indicator == "0s"
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
    assert settled.indicator in {"✓", "!"}


def test_disabled_motion_uses_static_frame_and_cancels_future_ticks() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)

    harness.controller.set_enabled(False)
    settled = harness.controller.snapshot
    harness.clock.advance(1.0)
    harness.dispatcher.drain()

    assert settled.indicator == "·"
    assert harness.controller.snapshot == settled
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(1.0)


def test_static_terminal_never_schedules_frames() -> None:
    harness = MotionHarness(static=True)

    harness.controller.set_signal("turn", MotionState.WORKING)

    assert harness.controller.snapshot.indicator == "·"
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
        assert mode.motion_controller.snapshot.indicator == "·"
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
        assert mode.motion_controller.snapshot.indicator == "·"
        assert mode.tui.time_until_next_work(1.0) == pytest.approx(1.0)
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()
