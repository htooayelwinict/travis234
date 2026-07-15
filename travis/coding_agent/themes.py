"""Discovered theme records and active-theme lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from travis.coding_agent.source_info import SourceInfo


@dataclass
class Theme:
    name: str
    colors: dict[str, object]
    vars: dict[str, object]
    source_path: str
    source_info: SourceInfo


class ThemeRegistry:
    def __init__(self) -> None:
        self._themes: dict[str, Theme] = {}
        self._active_name: str | None = None

    @property
    def active_name(self) -> str | None:
        return self._active_name

    @property
    def active_theme(self) -> Theme | None:
        return self._themes.get(self._active_name or "")

    def register_many(self, themes: list[Theme]) -> None:
        for theme in themes:
            self._themes[theme.name] = theme
        if self._active_name is None and self._themes:
            self._active_name = next(iter(self._themes))

    def select(self, name: str) -> Theme:
        try:
            theme = self._themes[name]
        except KeyError as error:
            raise ValueError(f"Unknown theme: {name}") from error
        self._active_name = name
        return theme

    def reload(self, themes: list[Theme]) -> str | None:
        previous = self._active_name
        self._themes = {theme.name: theme for theme in themes}
        if previous in self._themes:
            self._active_name = previous
            return None
        self._active_name = next(iter(self._themes), None)
        if previous is None:
            return None
        fallback = self._active_name or "built-in default"
        return f'Theme "{previous}" was removed; using "{fallback}".'

    def list(self) -> tuple[Theme, ...]:
        return tuple(self._themes.values())


__all__ = ["Theme", "ThemeRegistry"]
