from __future__ import annotations

from travis.tui import utils


def test_ansi_tracker_preserves_all_styles_and_extended_colors_in_order() -> None:
    tracker = utils._AnsiCodeTracker()

    tracker.process("\x1b[1;2;3;4;5;7;8;9;38;5;42;48;2;1;2;3m")

    assert tracker.get_active_codes() == "\x1b[1;2;3;4;5;7;8;9;38;5;42;48;2;1;2;3m"


def test_ansi_tracker_applies_selective_resets_and_hyperlink_lifecycle() -> None:
    tracker = utils._AnsiCodeTracker()
    tracker.process("\x1b[1;2;3;4;5;7;8;9;31;44m")
    tracker.process("\x1b]8;id=docs;https://example.com\x1b\\")
    tracker.process("\x1b[21;22;23;24;25;27;28;29;39;49m")

    assert tracker.get_active_codes() == "\x1b]8;id=docs;https://example.com\x1b\\"

    tracker.process("\x1b]8;;\x1b\\")
    assert tracker.get_active_codes() == ""


def test_ansi_tracker_ignores_non_sgr_and_malformed_codes_then_resets() -> None:
    tracker = utils._AnsiCodeTracker()
    tracker.process("\x1b[31m")
    tracker.process("\x1b[2J")
    tracker.process("\x1b[not-a-codem")

    assert tracker.get_active_codes() == "\x1b[31m"

    tracker.process("\x1b[0m")
    assert tracker.get_active_codes() == ""
