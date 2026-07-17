"""Focused State Signals and extension-status ownership for the TUI."""

from __future__ import annotations

from travis.tui.motion import MotionState


class InteractiveMotion:
    """Own the presentation-only motion surface for interactive mode."""

    def set_extension_status(
        self,
        key: str,
        text: str | None,
        options: dict | None = None,
    ) -> None:
        status_key = str(key)
        if text is None:
            self.extension_statuses.pop(status_key, None)
            self.extension_status_states.pop(status_key, None)
        else:
            self.extension_statuses[status_key] = str(text)
            state = options.get("state") if isinstance(options, dict) else None
            if state == "working":
                self.extension_status_states[status_key] = "working"
            else:
                self.extension_status_states.pop(status_key, None)
        self._refresh_extension_motion_signal()
        self._refresh_footer()
        self.tui.request_render()

    def _set_motion_signal(
        self,
        source: str,
        state: MotionState,
        *,
        countdown: int | None = None,
    ) -> None:
        self.motion_controller.set_signal(source, state, countdown=countdown)

    def _clear_motion_signal(self, source: str) -> None:
        self.motion_controller.clear_signal(source)

    def _refresh_extension_motion_signal(self) -> None:
        visible_working_message = self.extension_working_active and self.status.visible
        if visible_working_message or self.extension_status_states:
            self._set_motion_signal("extension", MotionState.EXTENSION)
        else:
            self._clear_motion_signal("extension")

    def set_working_message(self, message: str | None = None) -> None:
        self.extension_working_active = message is not None
        self.status.set_message(message if message is not None else self.default_working_message)
        self._refresh_extension_motion_signal()
        self.tui.request_render()

    def set_working_visible(self, visible: bool) -> None:
        self.status.set_visible(bool(visible))
        self._refresh_extension_motion_signal()
        self.tui.request_render()

    def set_working_indicator(self, options: dict | None = None) -> None:
        indicator: str | None = None
        if isinstance(options, dict):
            frames = options.get("frames")
            if isinstance(frames, list) and frames:
                indicator = str(frames[0])
            elif isinstance(frames, tuple) and frames:
                indicator = str(frames[0])
            elif frames == []:
                indicator = ""
        self.status.set_indicator(indicator)
        self.tui.request_render()


__all__ = ("InteractiveMotion",)
