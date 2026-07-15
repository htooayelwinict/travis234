"""Semantic terminal themes for the Travis234 presentation layer."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping


REQUIRED_THEME_ROLES: tuple[str, ...] = (
    "accent",
    "border",
    "borderAccent",
    "borderMuted",
    "success",
    "error",
    "warning",
    "muted",
    "dim",
    "text",
    "thinkingText",
    "selectedBg",
    "userMessageBg",
    "userMessageText",
    "customMessageBg",
    "customMessageText",
    "customMessageLabel",
    "toolPendingBg",
    "toolSuccessBg",
    "toolErrorBg",
    "toolTitle",
    "toolOutput",
    "mdHeading",
    "mdLink",
    "mdLinkUrl",
    "mdCode",
    "mdCodeBlock",
    "mdCodeBlockBorder",
    "mdQuote",
    "mdQuoteBorder",
    "mdHr",
    "mdListBullet",
    "toolDiffAdded",
    "toolDiffRemoved",
    "toolDiffContext",
    "syntaxComment",
    "syntaxKeyword",
    "syntaxFunction",
    "syntaxVariable",
    "syntaxString",
    "syntaxNumber",
    "syntaxType",
    "syntaxOperator",
    "syntaxPunctuation",
    "thinkingOff",
    "thinkingMinimal",
    "thinkingLow",
    "thinkingMedium",
    "thinkingHigh",
    "thinkingXhigh",
    "bashMode",
)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_COLOR_MODES = {"truecolor", "256color", "none"}
_CUBE_VALUES = (0, 95, 135, 175, 215, 255)
_GRAY_VALUES = tuple(8 + index * 10 for index in range(24))


@dataclass(frozen=True)
class ThemeDiagnostic:
    role: str
    code: str
    message: str


@dataclass(frozen=True)
class ResolvedTheme:
    name: str
    colors: Mapping[str, str]
    foreground_ansi: Mapping[str, str]
    background_ansi: Mapping[str, str]
    color_mode: str

    def fg(self, role: str, text: str) -> str:
        code = self.foreground_ansi.get(_role_alias(role), "")
        return f"{code}{text}\x1b[39m" if code else text

    def bg(self, role: str, text: str) -> str:
        resolved_role = _role_alias(role)
        if resolved_role not in self.background_ansi and f"{resolved_role}Bg" in self.background_ansi:
            resolved_role = f"{resolved_role}Bg"
        code = self.background_ansi.get(resolved_role, "")
        return f"{code}{text}\x1b[49m" if code else text

    def bold(self, text: str) -> str:
        return self._style("\x1b[1m", "\x1b[22m", text)

    def italic(self, text: str) -> str:
        return self._style("\x1b[3m", "\x1b[23m", text)

    def underline(self, text: str) -> str:
        return self._style("\x1b[4m", "\x1b[24m", text)

    def strikethrough(self, text: str) -> str:
        return self._style("\x1b[9m", "\x1b[29m", text)

    def _style(self, start: str, end: str, text: str) -> str:
        return text if self.color_mode == "none" else f"{start}{text}{end}"


class ThemeContext:
    """Mutable presentation-only reference with cache invalidation generation."""

    def __init__(self, theme: ResolvedTheme) -> None:
        self._theme = theme
        self._generation = 0

    @property
    def theme(self) -> ResolvedTheme:
        return self._theme

    @property
    def generation(self) -> int:
        return self._generation

    def set_theme(self, theme: ResolvedTheme) -> None:
        if theme == self._theme:
            return
        self._theme = theme
        self._generation += 1

    def snapshot(self) -> tuple[ResolvedTheme, int]:
        return self._theme, self._generation

    def restore(self, snapshot: tuple[ResolvedTheme, int]) -> None:
        theme, generation = snapshot
        self._theme = theme
        self._generation = generation


def resolve_theme(
    name: str,
    colors: Mapping[str, object],
    variables: Mapping[str, object],
    *,
    color_mode: str,
    fallback: ResolvedTheme | None,
) -> tuple[ResolvedTheme, tuple[ThemeDiagnostic, ...]]:
    """Resolve a possibly partial Pi-compatible palette without raising on bad roles."""

    if color_mode not in _COLOR_MODES:
        raise ValueError(f"Unsupported color mode: {color_mode}")

    diagnostics: list[ThemeDiagnostic] = []
    resolved: dict[str, str] = {}

    def diagnose(role: str, code: str, message: str) -> None:
        diagnostics.append(ThemeDiagnostic(role=role, code=code, message=message))

    def resolve_value(role: str, value: object, active: tuple[str, ...]) -> str | None:
        if isinstance(value, bool):
            diagnose(role, "invalid-value", "boolean values are not terminal colors")
            return None
        if isinstance(value, int):
            if 0 <= value <= 255:
                return str(value)
            diagnose(role, "invalid-index", "xterm color index must be between 0 and 255")
            return None
        if not isinstance(value, str):
            diagnose(role, "invalid-value", "color must be #RRGGBB, an xterm index, a variable, or empty")
            return None
        if value == "":
            return ""
        if value.startswith("#"):
            if _HEX_COLOR_RE.fullmatch(value):
                return value.lower()
            diagnose(role, "invalid-hex", "hex colors must use exactly six hexadecimal digits")
            return None
        if value in active:
            chain = " -> ".join((*active, value))
            diagnose(role, "variable-cycle", f"theme variable cycle: {chain}")
            return None
        if value not in variables:
            diagnose(role, "missing-variable", f'theme variable "{value}" is not defined')
            return None
        return resolve_value(role, variables[value], (*active, value))

    for role in REQUIRED_THEME_ROLES:
        if role not in colors:
            if fallback is not None:
                resolved[role] = fallback.colors[role]
            else:
                resolved[role] = ""
                diagnose(role, "missing-role", f'theme role "{role}" is not defined')
            continue
        value = resolve_value(role, colors[role], ())
        if value is None:
            resolved[role] = fallback.colors[role] if fallback is not None else ""
        else:
            resolved[role] = value

    foreground = {role: _ansi_for(value, background=False, color_mode=color_mode) for role, value in resolved.items()}
    background = {role: _ansi_for(value, background=True, color_mode=color_mode) for role, value in resolved.items()}
    theme = ResolvedTheme(
        name=str(name),
        colors=MappingProxyType(resolved),
        foreground_ansi=MappingProxyType(foreground),
        background_ansi=MappingProxyType(background),
        color_mode=color_mode,
    )
    return theme, tuple(diagnostics)


def _role_alias(role: str) -> str:
    return "thinkingXhigh" if role == "thinkingMax" else role


def _ansi_for(value: str, *, background: bool, color_mode: str) -> str:
    if color_mode == "none" or value == "":
        return ""
    prefix = 48 if background else 38
    if value.isdecimal():
        return f"\x1b[{prefix};5;{int(value)}m"
    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    if color_mode == "truecolor":
        return f"\x1b[{prefix};2;{red};{green};{blue}m"
    return f"\x1b[{prefix};5;{_rgb_to_256(red, green, blue)}m"


def _rgb_to_256(red: int, green: int, blue: int) -> int:
    cube_red = min(range(6), key=lambda index: abs(red - _CUBE_VALUES[index]))
    cube_green = min(range(6), key=lambda index: abs(green - _CUBE_VALUES[index]))
    cube_blue = min(range(6), key=lambda index: abs(blue - _CUBE_VALUES[index]))
    cube_index = 16 + 36 * cube_red + 6 * cube_green + cube_blue
    cube_distance = _color_distance(
        red,
        green,
        blue,
        _CUBE_VALUES[cube_red],
        _CUBE_VALUES[cube_green],
        _CUBE_VALUES[cube_blue],
    )
    gray = round(0.299 * red + 0.587 * green + 0.114 * blue)
    gray_offset = min(range(24), key=lambda index: abs(gray - _GRAY_VALUES[index]))
    gray_value = _GRAY_VALUES[gray_offset]
    gray_distance = _color_distance(red, green, blue, gray_value, gray_value, gray_value)
    return 232 + gray_offset if gray_distance < cube_distance else cube_index


def _color_distance(red_a: int, green_a: int, blue_a: int, red_b: int, green_b: int, blue_b: int) -> float:
    return (
        (red_a - red_b) ** 2 * 0.299
        + (green_a - green_b) ** 2 * 0.587
        + (blue_a - blue_b) ** 2 * 0.114
    )


__all__ = [
    "REQUIRED_THEME_ROLES",
    "ResolvedTheme",
    "ThemeContext",
    "ThemeDiagnostic",
    "resolve_theme",
]
