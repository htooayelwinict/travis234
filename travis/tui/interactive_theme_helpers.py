"""Theme reload helpers shared by the interactive view controller."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.themes import Theme, ThemeRegistry


class InteractiveThemeAppPort(Protocol):
    session: object


class InteractiveThemeViewPort(Protocol):
    app: InteractiveThemeAppPort
    theme_registry: ThemeRegistry
    _builtin_theme_records: Iterable[Theme]


def ensure_builtin_themes(registry: ThemeRegistry, builtins: Iterable[Theme]) -> None:
    existing = {theme.name for theme in registry.list()}
    missing = [theme for theme in builtins if theme.name not in existing]
    if missing:
        registry.register_many(missing)


def reload_resource_themes(
    registry: ThemeRegistry,
    builtins: Iterable[Theme],
    resource_loader: DefaultResourceLoader | None,
) -> str | None:
    discovered = resource_loader.get_themes().get("themes", []) if resource_loader is not None else []
    resource_themes = [theme for theme in discovered if isinstance(theme, Theme)]
    resource_names = {theme.name for theme in resource_themes}
    return registry.reload([*resource_themes, *(theme for theme in builtins if theme.name not in resource_names)])


def ensure_view_builtin_themes(view: InteractiveThemeViewPort) -> None:
    ensure_builtin_themes(view.theme_registry, view._builtin_theme_records)


def reload_view_resource_themes(view: InteractiveThemeViewPort) -> str | None:
    session = view.app.session
    return reload_resource_themes(
        view.theme_registry,
        view._builtin_theme_records,
        getattr(session, "resource_loader", None),
    )


__all__ = (
    "ensure_builtin_themes",
    "ensure_view_builtin_themes",
    "reload_resource_themes",
    "reload_view_resource_themes",
)
