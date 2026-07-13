"""Width-aware text utilities."""

from __future__ import annotations

import re
import unicodedata

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b_[^\x07]*(?:\x07|\x1b\\)"
)
_CURSOR_MARKER = "\x1b_travis234:c\x07"
_MISSING = object()


class _AnsiCodeTracker:
    def __init__(self) -> None:
        self.clear()

    def process(self, ansi_code: str) -> None:
        hyperlink = _parse_osc8_hyperlink(ansi_code)
        if hyperlink is not _MISSING:
            self.active_hyperlink = hyperlink
            return

        if not ansi_code.endswith("m"):
            return

        match = re.match(r"\x1b\[([\d;]*)m", ansi_code)
        if match is None:
            return

        params = match.group(1)
        if params in {"", "0"}:
            self._reset()
            return

        parts = params.split(";")
        index = 0
        while index < len(parts):
            try:
                code = int(parts[index])
            except ValueError:
                index += 1
                continue

            if code in {38, 48}:
                if index + 2 < len(parts) and parts[index + 1] == "5":
                    color_code = ";".join(parts[index : index + 3])
                    if code == 38:
                        self.fg_color = color_code
                    else:
                        self.bg_color = color_code
                    index += 3
                    continue
                if index + 4 < len(parts) and parts[index + 1] == "2":
                    color_code = ";".join(parts[index : index + 5])
                    if code == 38:
                        self.fg_color = color_code
                    else:
                        self.bg_color = color_code
                    index += 5
                    continue

            if code == 0:
                self._reset()
            elif code == 1:
                self.bold = True
            elif code == 2:
                self.dim = True
            elif code == 3:
                self.italic = True
            elif code == 4:
                self.underline = True
            elif code == 5:
                self.blink = True
            elif code == 7:
                self.inverse = True
            elif code == 8:
                self.hidden = True
            elif code == 9:
                self.strikethrough = True
            elif code == 21:
                self.bold = False
            elif code == 22:
                self.bold = False
                self.dim = False
            elif code == 23:
                self.italic = False
            elif code == 24:
                self.underline = False
            elif code == 25:
                self.blink = False
            elif code == 27:
                self.inverse = False
            elif code == 28:
                self.hidden = False
            elif code == 29:
                self.strikethrough = False
            elif code == 39:
                self.fg_color = None
            elif code == 49:
                self.bg_color = None
            elif 30 <= code <= 37 or 90 <= code <= 97:
                self.fg_color = str(code)
            elif 40 <= code <= 47 or 100 <= code <= 107:
                self.bg_color = str(code)
            index += 1

    def _reset(self) -> None:
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.blink = False
        self.inverse = False
        self.hidden = False
        self.strikethrough = False
        self.fg_color: str | None = None
        self.bg_color: str | None = None

    def clear(self) -> None:
        self._reset()
        self.active_hyperlink: dict[str, str] | None = None

    def get_active_codes(self) -> str:
        codes: list[str] = []
        if self.bold:
            codes.append("1")
        if self.dim:
            codes.append("2")
        if self.italic:
            codes.append("3")
        if self.underline:
            codes.append("4")
        if self.blink:
            codes.append("5")
        if self.inverse:
            codes.append("7")
        if self.hidden:
            codes.append("8")
        if self.strikethrough:
            codes.append("9")
        if self.fg_color:
            codes.append(self.fg_color)
        if self.bg_color:
            codes.append(self.bg_color)

        result = f"\x1b[{';'.join(codes)}m" if codes else ""
        if self.active_hyperlink is not None:
            result += _format_osc8_hyperlink(self.active_hyperlink)
        return result


def _parse_osc8_hyperlink(ansi_code: str) -> dict[str, str] | None | object:
    if not ansi_code.startswith("\x1b]8;"):
        return _MISSING

    terminator = "\x07" if ansi_code.endswith("\x07") else "\x1b\\"
    body = ansi_code[4:-1] if terminator == "\x07" else ansi_code[4:-2]
    separator_index = body.find(";")
    if separator_index == -1:
        return _MISSING

    params = body[:separator_index]
    url = body[separator_index + 1 :]
    if not url:
        return None
    return {"params": params, "url": url, "terminator": terminator}


def _format_osc8_hyperlink(hyperlink: dict[str, str]) -> str:
    return f"\x1b]8;{hyperlink['params']};{hyperlink['url']}{hyperlink['terminator']}"


_POOLED_STYLE_TRACKER = _AnsiCodeTracker()


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text.replace(_CURSOR_MARKER, ""))


def _is_printable_ascii(text: str) -> bool:
    for char in text:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint > 0x7E:
            return False
    return True


def visible_width(text: str) -> int:
    if text == "":
        return 0
    if _is_printable_ascii(text):
        return len(text)

    width = 0
    index = 0
    length = len(text)
    while index < length:
        match = _ANSI_RE.match(text, index)
        if match:
            index = match.end()
            continue
        width += _cell_width(text[index])
        index += 1
    return width


def normalize_terminal_output(text: str) -> str:
    return text.replace("\u0e33", "\u0e4d\u0e32").replace("\u0eb3", "\u0ecd\u0eb2")


def truncate_to_width(text: str, width: int, ellipsis: str = "", pad: bool = False) -> str:
    """Truncate to `width` visible columns, passing ANSI sequences through."""
    if width <= 0:
        return ""
    if text == "":
        return (" " * width) if pad else ""

    ellipsis_width = visible_width(ellipsis)
    if ellipsis_width >= width:
        text_width = visible_width(text)
        if text_width <= width:
            return text + (" " * max(0, width - text_width) if pad else "")
        clipped_ellipsis, clipped_width = _truncate_fragment_to_width(ellipsis, width)
        if clipped_width == 0:
            return (" " * width) if pad else ""
        return _finalize_truncated("", 0, clipped_ellipsis, clipped_width, width, pad)

    if _is_printable_ascii(text):
        if len(text) <= width:
            return text + (" " * max(0, width - len(text)) if pad else "")
        target_width = width - ellipsis_width
        return _finalize_truncated(text[:target_width], target_width, ellipsis, ellipsis_width, width, pad)

    target_width = width - ellipsis_width if ellipsis else width
    prefix: list[str] = []
    pending_ansi = ""
    visible_so_far = 0
    kept_width = 0
    keep_contiguous_prefix = True
    overflowed = False
    index = 0
    length = len(text)

    while index < length:
        match = _ANSI_RE.match(text, index)
        if match:
            pending_ansi += match.group(0)
            index = match.end()
            continue

        char = text[index]
        char_width = _cell_width(char)
        if keep_contiguous_prefix and kept_width + char_width <= target_width:
            if pending_ansi:
                prefix.append(pending_ansi)
                pending_ansi = ""
            prefix.append(char)
            kept_width += char_width
        else:
            keep_contiguous_prefix = False
            pending_ansi = ""

        visible_so_far += char_width
        if visible_so_far > width:
            overflowed = True
            break
        index += 1

    if not overflowed and index >= length:
        return text + (" " * max(0, width - visible_so_far) if pad else "")

    return _finalize_truncated("".join(prefix), kept_width, ellipsis, ellipsis_width, width, pad)


def _truncate_fragment_to_width(text: str, width: int) -> tuple[str, int]:
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
        char_width = _cell_width(text[index])
        if visible + char_width > width:
            break
        out.append(text[index])
        visible += char_width
        index += 1
    return "".join(out), visible


def _finalize_truncated(
    prefix: str,
    prefix_width: int,
    ellipsis: str,
    ellipsis_width: int,
    width: int,
    pad: bool,
) -> str:
    if ellipsis:
        result = f"{prefix}\x1b[0m{ellipsis}\x1b[0m"
    else:
        result = f"{prefix}\x1b[0m"
    if not pad:
        return result
    return result + (" " * max(0, width - prefix_width - ellipsis_width))


def slice_by_column(text: str, start_col: int, width: int, strict: bool = False) -> str:
    return str(slice_with_width(text, start_col, width, strict=strict)["text"])


def slice_with_width(text: str, start_col: int, width: int, strict: bool = False) -> dict[str, object]:
    if width <= 0:
        return {"text": "", "width": 0}
    end_col = start_col + width
    result: list[str] = []
    result_width = 0
    current_col = 0
    index = 0
    pending_ansi = ""

    while index < len(text):
        match = _ANSI_RE.match(text, index)
        if match:
            if start_col <= current_col < end_col:
                result.append(match.group(0))
            elif current_col < start_col:
                pending_ansi += match.group(0)
            index = match.end()
            continue

        char = text[index]
        char_width = _cell_width(char)
        in_range = start_col <= current_col < end_col
        fits = not strict or current_col + char_width <= end_col
        if in_range and fits:
            if pending_ansi:
                result.append(pending_ansi)
                pending_ansi = ""
            result.append(char)
            result_width += char_width
        current_col += char_width
        index += 1
        if current_col >= end_col:
            break
    return {"text": "".join(result), "width": result_width}


def extract_segments(
    line: str,
    before_end: int,
    after_start: int,
    after_len: int,
    strict_after: bool = False,
) -> dict[str, object]:
    before: list[str] = []
    after: list[str] = []
    before_width = 0
    after_width = 0
    current_col = 0
    index = 0
    pending_ansi_before = ""
    after_started = False
    after_end = after_start + after_len

    _POOLED_STYLE_TRACKER.clear()

    while index < len(line):
        match = _ANSI_RE.match(line, index)
        if match:
            ansi_code = match.group(0)
            _POOLED_STYLE_TRACKER.process(ansi_code)
            if current_col < before_end:
                pending_ansi_before += ansi_code
            elif after_start <= current_col < after_end and after_started:
                after.append(ansi_code)
            index = match.end()
            continue

        char = line[index]
        char_width = _cell_width(char)
        if current_col < before_end:
            if pending_ansi_before:
                before.append(pending_ansi_before)
                pending_ansi_before = ""
            before.append(char)
            before_width += char_width
        elif after_start <= current_col < after_end:
            fits = not strict_after or current_col + char_width <= after_end
            if fits:
                if not after_started:
                    after.append(_POOLED_STYLE_TRACKER.get_active_codes())
                    after_started = True
                after.append(char)
                after_width += char_width

        current_col += char_width
        index += 1
        if current_col >= (before_end if after_len <= 0 else after_end):
            break

    return {
        "before": "".join(before),
        "beforeWidth": before_width,
        "after": "".join(after),
        "afterWidth": after_width,
    }


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
        chunk, _ = _truncate_fragment_to_width(remaining, width)
        if not chunk:
            break
        chunks.append(chunk)
        remaining = remaining[len(chunk) :]
    return chunks[0], chunks[1:] + ([remaining] if remaining else [])


def _cell_width(char: str) -> int:
    if char == "\t":
        return 3
    category = unicodedata.category(char)
    if category[0] in {"C", "M"} or unicodedata.combining(char):
        return 0
    codepoint = ord(char)
    if 0x1F000 <= codepoint <= 0x1FAFF or 0x1F1E6 <= codepoint <= 0x1F1FF:
        return 2
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def truncateToWidth(text: str, maxWidth: int, ellipsis: str = "...", pad: bool = False) -> str:
    return truncate_to_width(text, maxWidth, ellipsis, pad)


extractSegments = extract_segments
visibleWidth = visible_width
wrapTextWithAnsi = wrap_text
