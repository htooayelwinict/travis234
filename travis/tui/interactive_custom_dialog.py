"""Custom extension-dialog lifecycle for the interactive TUI."""

from __future__ import annotations

import inspect
from typing import Callable

from travis.tui.interactive_extensions import _coerce_extension_component, _dispose_extension_widget


def prompt_extension_custom(
    view: object,
    factory: Callable[..., object],
    options: dict | None = None,
) -> object:
    """Run an extension-owned custom component and restore the main editor exactly."""
    del options
    previous_children = list(view.editor_container.children)
    saved_editor = view.active_editor
    saved_text = saved_editor.get_value() if saved_editor is not None else view.editor_text
    result: dict[str, object] = {"closed": False, "value": None}
    component_holder: dict[str, object] = {"component": None}

    def restore_editor() -> None:
        view.editor_container.clear()
        if saved_editor is not None:
            saved_editor.set_value(saved_text)
            view.active_editor = saved_editor
        else:
            view.editor_text = saved_text
        for child in previous_children:
            view.editor_container.add(child)
        view.tui.request_render()

    def close(value: object = None) -> None:
        if result["closed"]:
            return
        result["closed"] = True
        result["value"] = value
        restore_editor()
        _dispose_extension_widget(component_holder["component"])

    try:
        component = factory(view.tui, None, None, close)
        if inspect.isawaitable(component):
            import asyncio

            component = asyncio.run(component)
    except Exception:
        restore_editor()
        raise

    component_holder["component"] = component
    if result["closed"]:
        _dispose_extension_widget(component)
        return result["value"]

    view.editor_container.clear()
    view.editor_container.add(_coerce_extension_component(component))
    view.tui.request_render(force=True)

    while not result["closed"]:
        try:
            data = view._read_prompt_from_line_input("")
        except EOFError:
            close(None)
            break
        handle_result = getattr(component, "handle_input", lambda _data: None)(data)
        if inspect.isawaitable(handle_result):
            import asyncio

            asyncio.run(handle_result)
        if not result["closed"]:
            view.tui.request_render()
    return result["value"]
