from __future__ import annotations

from typing import Protocol

from appv22_ui.renderers.json import JsonRenderer
from appv22_ui.renderers.plain import PlainRenderer
from appv22_ui.renderers.tui import TuiRenderer


class Renderer(Protocol):
    def render(self, result: dict | None) -> str:
        ...


def create_renderer(name: str) -> Renderer:
    normalized = name.lower().strip()
    if normalized == "json":
        return JsonRenderer()
    if normalized == "tui":
        return TuiRenderer()
    if normalized == "plain":
        return PlainRenderer()
    raise ValueError(f"unknown renderer: {name}")
