from __future__ import annotations

from travis.tui import Component, FakeTerminal, TUI


class MutableLines(Component):
    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self.lines = lines

    def render(self, width: int) -> list[str]:
        del width
        return list(self.lines)


def test_diff_render_preserves_noop_append_and_shrink_boundaries() -> None:
    terminal = FakeTerminal(columns=40, rows=10)
    tui = TUI(terminal)
    component = MutableLines(["a", "b", "c"])
    tui.add(component)

    initial = tui.request_render()
    unchanged = tui.request_render()
    component.lines.append("d")
    appended = tui.request_render()
    component.lines[:] = ["a"]
    shrunk = tui.request_render()

    assert initial.full is True
    assert (unchanged.full, unchanged.first_changed, unchanged.last_changed) == (
        False,
        -1,
        -1,
    )
    assert (appended.full, appended.first_changed, appended.last_changed) == (
        False,
        3,
        3,
    )
    assert (shrunk.full, shrunk.first_changed, shrunk.last_changed) == (
        True,
        0,
        0,
    )
