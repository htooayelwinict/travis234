"""Streaming-safe semantic Markdown for terminal output."""

from __future__ import annotations

import re

from travis.tui.components.base import Text
from travis.tui.terminal_image import get_capabilities
from travis.tui.utils import truncate_to_width, visible_width, wrap_text


_INLINE_TOKEN_RE = re.compile(
    r"\[([^\]]+)\]\(([^)]+)\)"
    r"|`([^`]*)`"
    r"|\*\*(.+?)\*\*"
    r"|~~(.+?)~~"
    r"|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"
    r"|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)"
)
_ORDERED_LIST_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
_UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
_SAFE_LINK_RE = re.compile(r"^https?://[^\s\x00-\x1f\x7f\x1b]+$", re.I)


class Markdown(Text):
    """Bounded Markdown renderer that remains stable for partial streaming input."""

    def __init__(
        self,
        text: str = "",
        *,
        theme_context: object | None = None,
        role: str | None = None,
        background_role: str | None = None,
    ) -> None:
        super().__init__(
            text,
            theme_context=theme_context,
            role=role,
            background_role=background_role,
        )

    def render(self, width: int) -> list[str]:
        width = max(1, int(width))
        theme = getattr(self.theme_context, "theme", None)
        generation = getattr(self.theme_context, "generation", -1)
        hyperlinks_enabled = bool(get_capabilities().get("hyperlinks"))
        key = (self._text, width, generation, hyperlinks_enabled, self.role, self.background_role)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        lines = _render_markdown_lines(
            self._text,
            width,
            theme=theme,
            default_role=self.role or "text",
            hyperlinks_enabled=hyperlinks_enabled,
        )
        if self.background_role and theme is not None:
            lines = [theme.bg(self.background_role, line) for line in lines]
        self._cache = lines
        self._cache_key = key  # type: ignore[assignment]
        return lines


def _render_markdown_lines(
    text: str,
    width: int,
    *,
    theme: object | None,
    default_role: str,
    hyperlinks_enabled: bool,
) -> list[str]:
    source = text.splitlines()
    if not source:
        return [""]
    output: list[str] = []
    index = 0
    while index < len(source):
        raw = source[index]
        stripped = raw.strip()
        if stripped.startswith("```"):
            index += 1
            code_lines: list[str] = []
            while index < len(source) and not source[index].strip().startswith("```"):
                code_lines.append(source[index])
                index += 1
            if index < len(source):
                index += 1
            if not code_lines:
                code_lines = [""]
            for code_line in code_lines:
                role = _code_role(code_line)
                prefix = _style(theme, "mdCodeBlockBorder", "│ ")
                content = _style(theme, role, code_line)
                output.extend(_wrap_prefixed(prefix, content, width))
            continue

        if _looks_like_table(source, index):
            header = _table_cells(source[index])
            rows: list[list[str]] = []
            index += 2
            while index < len(source) and "|" in source[index] and source[index].strip():
                rows.append(_table_cells(source[index]))
                index += 1
            output.extend(
                _render_table(
                    header,
                    rows,
                    width,
                    theme=theme,
                    default_role=default_role,
                    hyperlinks_enabled=hyperlinks_enabled,
                )
            )
            continue

        if stripped == "":
            output.append("")
            index += 1
            continue
        if heading := _HEADING_RE.match(raw):
            content = _inline(heading.group(2), theme, "mdHeading", hyperlinks_enabled)
            if theme is not None:
                content = theme.bold(content)
            output.extend(wrap_text(content, width))
            index += 1
            continue
        if quote := _QUOTE_RE.match(raw):
            prefix = _style(theme, "mdQuoteBorder", "│ ")
            content = _inline(quote.group(1), theme, "mdQuote", hyperlinks_enabled)
            output.extend(_wrap_prefixed(prefix, content, width))
            index += 1
            continue
        if unordered := _UNORDERED_LIST_RE.match(raw):
            prefix = _style(theme, "mdListBullet", "• ")
            content = _inline(unordered.group(1), theme, default_role, hyperlinks_enabled)
            output.extend(_wrap_prefixed(prefix, content, width))
            index += 1
            continue
        if ordered := _ORDERED_LIST_RE.match(raw):
            prefix = _style(theme, "mdListBullet", f"{ordered.group(1)}. ")
            content = _inline(ordered.group(2), theme, default_role, hyperlinks_enabled)
            output.extend(_wrap_prefixed(prefix, content, width))
            index += 1
            continue
        if _RULE_RE.match(raw):
            output.append(_style(theme, "mdHr", "─" * width))
            index += 1
            continue

        content = _inline(raw, theme, default_role, hyperlinks_enabled)
        output.extend(wrap_text(content, width))
        index += 1

    return [truncate_to_width(line, width) for line in output]


def _inline(text: str, theme: object | None, default_role: str, hyperlinks_enabled: bool) -> str:
    rendered: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN_RE.finditer(text):
        if match.start() > cursor:
            rendered.append(_style(theme, default_role, text[cursor : match.start()]))
        if match.group(1) is not None:
            label = _style(theme, "mdLink", match.group(1))
            url = match.group(2).strip()
            if hyperlinks_enabled and _SAFE_LINK_RE.fullmatch(url):
                rendered.append(f"\x1b]8;;{url}\x07{label}\x1b]8;;\x07")
            else:
                rendered.append(label + _style(theme, "mdLinkUrl", f" ({url})"))
        elif match.group(3) is not None:
            rendered.append(_style(theme, "mdCode", match.group(3)))
        elif match.group(4) is not None:
            value = _inline(match.group(4), theme, default_role, hyperlinks_enabled)
            rendered.append(theme.bold(value) if theme is not None else value)
        elif match.group(5) is not None:
            value = _inline(match.group(5), theme, default_role, hyperlinks_enabled)
            rendered.append(theme.strikethrough(value) if theme is not None else value)
        else:
            emphasis = match.group(6) if match.group(6) is not None else match.group(7)
            value = _inline(emphasis or "", theme, default_role, hyperlinks_enabled)
            rendered.append(theme.italic(value) if theme is not None else value)
        cursor = match.end()
    if cursor < len(text):
        rendered.append(_style(theme, default_role, text[cursor:]))
    return "".join(rendered)


def _looks_like_table(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index] or "|" not in lines[index + 1]:
        return False
    separator = _table_cells(lines[index + 1])
    return bool(separator) and all(_TABLE_SEPARATOR_RE.fullmatch(cell.replace(" ", "")) for cell in separator)


def _table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _render_table(
    header: list[str],
    rows: list[list[str]],
    width: int,
    *,
    theme: object | None,
    default_role: str,
    hyperlinks_enabled: bool,
) -> list[str]:
    normalized_rows = [row + [""] * max(0, len(header) - len(row)) for row in rows]
    column_widths = [
        max([visible_width(header[column]), *(visible_width(row[column]) for row in normalized_rows)])
        for column in range(len(header))
    ]
    required_width = sum(column_widths) + 3 * len(column_widths) + 1
    if required_width <= width:
        rendered: list[str] = []
        table_rows = [header, *normalized_rows]
        for row_index, row in enumerate(table_rows):
            cells: list[str] = []
            for column, cell in enumerate(row):
                padded = cell + " " * max(0, column_widths[column] - visible_width(cell))
                role = "mdHeading" if row_index == 0 else default_role
                cells.append(_inline(padded, theme, role, hyperlinks_enabled))
            rendered.append(
                _style(theme, "mdCodeBlockBorder", "| ")
                + _style(theme, "mdCodeBlockBorder", " | ").join(cells)
                + _style(theme, "mdCodeBlockBorder", " |")
            )
        return rendered

    stacked: list[str] = []
    for row_index, row in enumerate(normalized_rows):
        if row_index > 0:
            stacked.append("")
        for column, label in enumerate(header):
            prefix = _style(theme, "mdHeading", f"{label}: ")
            value = _inline(row[column], theme, default_role, hyperlinks_enabled)
            stacked.extend(_wrap_prefixed(prefix, value, width))
    return stacked


def _wrap_prefixed(prefix: str, content: str, width: int) -> list[str]:
    prefix_width = visible_width(prefix)
    available = max(1, width - prefix_width)
    chunks = wrap_text(content, available)
    continuation = " " * prefix_width
    return [prefix + chunks[0], *(continuation + chunk for chunk in chunks[1:])]


def _style(theme: object | None, role: str, text: str) -> str:
    return theme.fg(role, text) if theme is not None and text else text


def _code_role(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return "toolDiffAdded"
    if line.startswith("-") and not line.startswith("---"):
        return "toolDiffRemoved"
    if line.startswith(" "):
        return "toolDiffContext"
    return "mdCodeBlock"


__all__ = ["Markdown"]
