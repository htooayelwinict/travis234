"""Read-only language-service status for the native TUI."""

from __future__ import annotations

from collections.abc import Mapping

from travis.tui.components import Text
from travis.tui.interactive_surfaces import InteractiveLspSurface


def format_lsp_status(status: Mapping[str, object]) -> str:
    configured = int(str(status.get("configured", 0)))
    active = int(str(status.get("active", 0)))
    limits = status.get("limits")
    safe_limits = limits if isinstance(limits, Mapping) else {}
    max_active = int(str(safe_limits.get("maxActiveServers", 0)))
    startup = safe_limits.get("startupSeconds", "?")
    request = safe_limits.get("requestSeconds", "?")
    restarts = safe_limits.get("maxRestarts", "?")
    restart_window = safe_limits.get("restartWindowSeconds", "?")
    lines = [
        f"Configured: {configured}",
        f"Active: {active}/{max_active}",
        f"Bounds: startup {startup}s, request {request}s, restarts {restarts}/{restart_window}s",
    ]
    servers = status.get("servers")
    if isinstance(servers, list):
        for raw in servers[:max_active]:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name", "unnamed"))[:80]
            state = "running" if raw.get("running") is True else "stopped"
            if raw.get("restartExhausted") is True:
                state = "restart-exhausted"
            generation = int(str(raw.get("generation", 0)))
            encoding = str(raw.get("positionEncoding", "unknown"))[:20]
            lines.append(f"{name}: {state}, generation {generation}, {encoding}")
    return "\n".join(lines)


class InteractiveLsp(InteractiveLspSurface):
    """Owns the bounded, local-only `/lsp status` view."""

    __slots__ = ()

    def _run_lsp_status_command(self) -> None:
        manager = getattr(self.app.session, "_language_services", None)
        if manager is None:
            self.history.add(Text("Language services are not configured for this session."))
        else:
            self.history.add(Text(format_lsp_status(manager.status())))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()


__all__ = ("InteractiveLsp", "format_lsp_status")
