from __future__ import annotations


def test_component_owners_expose_focused_types() -> None:
    from travis.tui.components.autocomplete import SimpleAutocompleteProvider
    from travis.tui.components.base import Box, Component, Text
    from travis.tui.components.editor import Input
    from travis.tui.components.footer import FooterComponent
    from travis.tui.components.pickers import SelectList, SettingsList

    assert Text("hello").render(80) == ["hello"]
    assert isinstance(Box(Text("body")), Component)
    assert Input().get_value() == ""
    assert SimpleAutocompleteProvider(commands=[]).get_suggestions([""], 0, 0, {}) is None
    assert issubclass(SelectList, Component)
    assert issubclass(SettingsList, Component)
    assert FooterComponent(cwd="/tmp", model="m").render(80)
