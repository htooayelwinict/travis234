"""Read-only explicit-memory status for the interactive TUI."""

from __future__ import annotations

import time

from travis.coding_agent.memory import MemorySettings, MemoryStore
from travis.tui.components import StatusLine, Text


class MemoryInspector:
    """Project bounded memory metadata without opening or mutating storage."""

    def __init__(
        self,
        settings: MemorySettings,
        store: MemoryStore | None,
        project_key: str | None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._project_key = project_key

    @classmethod
    def from_session(cls, session: object) -> "MemoryInspector":
        settings = getattr(session, "_memory_settings", MemorySettings())
        if not isinstance(settings, MemorySettings):
            settings = MemorySettings()
        store = getattr(session, "_memory_store", None)
        if not isinstance(store, MemoryStore):
            store = None
        project_key = getattr(session, "_memory_project_key", None)
        return cls(
            settings,
            store,
            project_key if isinstance(project_key, str) else None,
        )

    def status(self) -> tuple[str, ...]:
        settings = self._settings
        if not settings.enabled:
            state = "disabled; store=not-open"
            counts = None
        elif self._store is None or self._project_key is None:
            state = "enabled; store=unavailable"
            counts = None
        else:
            try:
                counts = self._store.counts(
                    project_key=self._project_key,
                    now_ms=time.time_ns() // 1_000_000,
                )
            except Exception:
                state = "enabled; store=unavailable"
                counts = None
            else:
                state = "enabled; store=available"
        count_line = (
            f"Counts: project={counts['project']} global={counts['global']}"
            if counts is not None
            else "Counts: unavailable"
        )
        return (
            f"Memory: {state}",
            "Allowed scopes: " + ", ".join(settings.allowed_scopes),
            (
                f"Limits: factBytes={settings.max_fact_bytes} "
                f"factsPerScope={settings.max_facts_per_scope}"
            ),
            (
                f"Limits: totalBytes={settings.max_total_bytes} "
                f"recallRecords={settings.recall_limit} "
                f"recallBytes={settings.recall_bytes}"
            ),
            count_line,
            "Automatic retention: false",
            "Automatic injection: false",
        )


class InteractiveMemory:
    """TUI mixin exposing memory metadata without fact operations."""

    def _run_memory_status_command(self) -> None:
        lines = MemoryInspector.from_session(self.app.session).status()
        self.history.add(StatusLine("Memory status", kind="session"))
        for line in lines:
            self.history.add(Text(line))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()


__all__ = ["InteractiveMemory", "MemoryInspector"]
