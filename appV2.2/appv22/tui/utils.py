"""Width-aware text utilities. Port of pi/packages/tui/src/utils.ts (subset)."""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CURSOR_MARKER = "\x1b_pi:c\x07"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text.replace(_CURSOR_MARKER, ""))


def visible_width(text: str) -> int:
    return len(strip_ansi(text))


def truncate_to_width(text: str, width: int) -> str:
    """Truncate to `width` visible columns, passing ANSI sequences through."""
    if visible_width(text) <= width:
        return text
    out: list[str] = []
    visible = 0
    index = 0
    length = len(text)
    while index < length and visible < width:
        match = _ANSI_RE.match(text, index)
        if match:
            out.append(match.group(0))
            index = match.end()
            continue
        out.append(text[index])
        visible += 1
        index += 1
    return "".join(out)


def wrap_text(text: str, width: int) -> list[str]:
    """Greedy word-wrap to `width` visible columns. Empty text -> ['']."""
    if width <= 0:
        return [text]
    if text == "":
        return [""]
    lines: list[str] = []
    current = ""
    current_width = 0
    for word in text.split(" "):
        word_width = visible_width(word)
        if current == "":
            piece, rest = _split_long_word(word, width)
            current = piece
            current_width = visible_width(piece)
            for chunk in rest:
                lines.append(current)
                current = chunk
                current_width = visible_width(chunk)
            continue
        if current_width + 1 + word_width <= width:
            current += " " + word
            current_width += 1 + word_width
        else:
            lines.append(current)
            piece, rest = _split_long_word(word, width)
            current = piece
            current_width = visible_width(piece)
            for chunk in rest:
                lines.append(current)
                current = chunk
                current_width = visible_width(chunk)
    lines.append(current)
    return lines


def _split_long_word(word: str, width: int) -> tuple[str, list[str]]:
    if visible_width(word) <= width:
        return word, []
    chunks: list[str] = []
    remaining = word
    while visible_width(remaining) > width:
        chunks.append(remaining[:width])
        remaining = remaining[width:]
    return chunks[0], chunks[1:] + ([remaining] if remaining else [])
