from __future__ import annotations

import signal
from pathlib import Path

from tests._support_tui import CodingApp, FakeTerminal, faux_model
from tests.test_tui_terminal_and_input import test_wait_for_active_turn_has_a_shutdown_deadline
from travis.tui.interactive_mode import InteractiveMode


def test_shutdown_deadline_characterization(tmp_path: Path) -> None:
    test_wait_for_active_turn_has_a_shutdown_deadline(tmp_path)


def test_os_sigint_defers_tui_mutation_to_owner_dispatcher(tmp_path: Path, monkeypatch) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    runtime = InteractiveMode(app)._runtime
    runtime.tui.drain_dispatcher()
    installed: dict[int, object] = {}
    handled: list[tuple[object, object]] = []

    monkeypatch.setattr(signal, "getsignal", lambda signum: "previous")
    monkeypatch.setattr(signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))
    monkeypatch.setattr(runtime, "_handle_sigint", lambda signum, frame: handled.append((signum, frame)))

    assert runtime._install_sigint_handler() == "previous"
    handler = installed[signal.SIGINT]
    assert callable(handler)

    handler(signal.SIGINT, None)

    assert handled == []
    assert runtime.tui.drain_dispatcher() == 1
    assert handled == [(signal.SIGINT, None)]
    assert runtime.tui.drain_dispatcher() == 0
