"""Owner-thread modal input loop shared by native TUI prompts."""

from __future__ import annotations

import queue
import threading

from travis.tui.components import Input


def prompt_tui_value(
    view: object,
    prompt: str,
    *,
    mask: bool = False,
    cancel_event: threading.Event | None = None,
    cancel_on_escape: bool = False,
) -> str | None:
    submitted: queue.Queue[str | None] = queue.Queue()
    component = Input(prompt=prompt, on_submit=lambda value: submitted.put(value), mask=mask)
    if cancel_on_escape:
        component.on_escape = lambda: submitted.put(None)
    tui = view.tui
    previous_focus = tui.focused_component
    view.active_editor = component
    view.editor_container.add(component)
    tui.set_focus(component)
    tui.request_render()
    view._emit_pending_model_picker_trace()
    try:
        while not view._shutdown_requested:
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                value = submitted.get(timeout=tui.time_until_next_work(0.05))
                if tui.dispatcher.is_owner_thread():
                    tui.drain_dispatcher()
                return value
            except queue.Empty:
                if tui.dispatcher.is_owner_thread():
                    tui.drain_dispatcher()
        return None
    finally:
        if component in view.editor_container.children:
            view.editor_container.remove(component)
        if view.active_editor is component:
            view.active_editor = None
        tui.set_focus(previous_focus)
        tui.request_render()


__all__ = ["prompt_tui_value"]
