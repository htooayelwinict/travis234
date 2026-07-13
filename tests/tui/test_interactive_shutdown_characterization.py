from __future__ import annotations

from pathlib import Path

from tests.test_tui_terminal_and_input import test_wait_for_active_turn_has_a_shutdown_deadline


def test_shutdown_deadline_characterization(tmp_path: Path) -> None:
    test_wait_for_active_turn_has_a_shutdown_deadline(tmp_path)
