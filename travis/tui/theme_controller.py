"""Presentation-only lifecycle for semantic TUI themes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from travis.tui.builtin_themes import resolve_builtin_theme
from travis.tui.theme import ResolvedTheme, ThemeContext, ThemeDiagnostic, resolve_theme


class ThemeController:
    def __init__(
        self,
        registry: object,
        settings_reader: object,
        context: ThemeContext,
        request_render: Callable[[], object],
        *,
        color_mode: str,
    ) -> None:
        self.registry = registry
        self.settings_reader = settings_reader
        self.context = context
        self.request_render = request_render
        self.color_mode = color_mode
        self.diagnostics: tuple[ThemeDiagnostic, ...] = ()
        self._fingerprint: tuple[object, ...] | None = None
        self._preview_snapshot: tuple[ResolvedTheme, int] | None = None
        self._preview_name: str | None = None
        self._fallback, _ = resolve_builtin_theme("Signal Glass", color_mode=color_mode)

    def select_persisted(self) -> str | None:
        requested = self._read_persisted_name()
        available = {str(theme.name) for theme in self._themes()}
        select = getattr(self.registry, "select", None)
        if requested in available and callable(select):
            select(requested)
        elif "Signal Glass" in available and callable(select):
            select("Signal Glass")
        self.sync()
        return getattr(self.registry, "active_name", None)

    def sync(self) -> None:
        if self._preview_snapshot is not None:
            return
        record = getattr(self.registry, "active_theme", None)
        if record is None:
            return
        fingerprint = self._record_fingerprint(record)
        if fingerprint == self._fingerprint:
            return

        resolved = self._resolve_record(record, load_source=True)
        self._fingerprint = fingerprint
        if resolved is None:
            return
        theme, diagnostics = resolved
        self.diagnostics = diagnostics
        generation = self.context.generation
        self.context.set_theme(theme)
        if self.context.generation != generation:
            self.request_render()

    def preview(self, name: str) -> bool:
        record = self._find_theme(str(name))
        if record is None:
            self.diagnostics = (
                ThemeDiagnostic(role="__theme__", code="missing-theme", message=f'Unknown theme: {name}'),
            )
            return False
        resolved = self._resolve_record(record, load_source=True)
        if resolved is None:
            return False
        theme, diagnostics = resolved
        if self._preview_snapshot is None:
            self._preview_snapshot = self.context.snapshot()
        self._preview_name = str(name)
        self.diagnostics = diagnostics
        before = self.context.snapshot()
        self.context.set_theme(theme)
        if self.context.snapshot() != before:
            self.request_render()
        return True

    def restore_preview(self) -> None:
        if self._preview_snapshot is None:
            self._preview_name = None
            return
        snapshot = self._preview_snapshot
        before = self.context.snapshot()
        self.context.restore(snapshot)
        self._preview_snapshot = None
        self._preview_name = None
        if self.context.snapshot() != before:
            self.request_render()

    def commit_preview_result(self) -> str | None:
        selected = self._preview_name
        self.restore_preview()
        return selected

    def _resolve_record(
        self,
        record: object,
        *,
        load_source: bool,
    ) -> tuple[ResolvedTheme, tuple[ThemeDiagnostic, ...]] | None:
        name = str(getattr(record, "name", ""))
        colors = getattr(record, "colors", {})
        variables = getattr(record, "vars", {})
        source_path = str(getattr(record, "source_path", "") or "")
        if load_source and source_path and Path(source_path).is_file():
            try:
                payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("theme root must be an object")
                source_colors = payload.get("colors")
                source_variables = payload.get("vars", {})
                if not isinstance(source_colors, dict) or not isinstance(source_variables, dict):
                    raise ValueError("theme colors and vars must be objects")
                colors = source_colors
                variables = source_variables
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                self.diagnostics = (
                    ThemeDiagnostic(role="__theme__", code="invalid-source", message=f"{source_path}: {error}"),
                )
                return None
        if not isinstance(colors, Mapping) or not isinstance(variables, Mapping):
            self.diagnostics = (
                ThemeDiagnostic(role="__theme__", code="invalid-source", message=f'{name}: colors and vars must be mappings'),
            )
            return None
        return resolve_theme(
            name,
            colors,
            variables,
            color_mode=self.color_mode,
            fallback=self._fallback,
        )

    def _record_fingerprint(self, record: object) -> tuple[object, ...]:
        source_path = str(getattr(record, "source_path", "") or "")
        source_signature: tuple[int, int] | None = None
        if source_path:
            try:
                stat = Path(source_path).stat()
                source_signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                source_signature = None
        return (
            str(getattr(record, "name", "")),
            source_path,
            source_signature,
            _stable_value(getattr(record, "colors", {})),
            _stable_value(getattr(record, "vars", {})),
        )

    def _find_theme(self, name: str) -> object | None:
        return next((theme for theme in self._themes() if str(getattr(theme, "name", "")) == name), None)

    def _themes(self) -> tuple[object, ...]:
        list_themes = getattr(self.registry, "list", None)
        return tuple(list_themes()) if callable(list_themes) else ()

    def _read_persisted_name(self) -> str | None:
        reader = self.settings_reader
        if callable(reader):
            value = reader()
        else:
            method = getattr(reader, "get_theme", None) or getattr(reader, "getTheme", None)
            value = method() if callable(method) else None
        return str(value) if value else None


def _stable_value(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr)
    except (TypeError, ValueError):
        return repr(value)


__all__ = ["ThemeController"]
