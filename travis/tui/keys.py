"""Keyboard helpers. Python"""

from __future__ import annotations

import re

_kitty_protocol_active = False
_CTRL_CODES = {chr(index): f"ctrl+{chr(index + 96)}" for index in range(1, 27)}
_LOCK_MASK = 64 + 128
_KITTY_PRINTABLE_ALLOWED_MODIFIERS = 1 | _LOCK_MASK
_LEGACY_SPECIALS = {
    "\x1b": "escape",
    "\r": "enter",
    "\n": "enter",
    "\t": "tab",
    "\x7f": "backspace",
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1b[3~": "delete",
}
_ARROW_SUFFIX = {"A": "up", "B": "down", "C": "right", "D": "left"}
_FUNCTIONAL_CODES = {2: "insert", 3: "delete", 5: "pageUp", 6: "pageDown", 7: "home", 8: "end"}
_MODIFIER_BITS = {"shift": 1, "alt": 2, "ctrl": 4, "super": 8}


class _Key:
    escape = "escape"
    esc = "esc"
    enter = "enter"
    return_ = "return"
    tab = "tab"
    space = "space"
    backspace = "backspace"
    delete = "delete"
    insert = "insert"
    clear = "clear"
    home = "home"
    end = "end"
    page_up = "pageUp"
    page_down = "pageDown"
    up = "up"
    down = "down"
    left = "left"
    right = "right"
    f1 = "f1"
    f2 = "f2"
    f3 = "f3"
    f4 = "f4"
    f5 = "f5"
    f6 = "f6"
    f7 = "f7"
    f8 = "f8"
    f9 = "f9"
    f10 = "f10"
    f11 = "f11"
    f12 = "f12"
    backtick = "`"
    hyphen = "-"
    equals = "="
    leftbracket = "["
    rightbracket = "]"
    backslash = "\\"
    semicolon = ";"
    quote = "'"
    comma = ","
    period = "."
    slash = "/"
    exclamation = "!"
    at = "@"
    hash = "#"
    dollar = "$"
    percent = "%"
    caret = "^"
    ampersand = "&"
    asterisk = "*"
    leftparen = "("
    rightparen = ")"
    underscore = "_"
    plus = "+"
    pipe = "|"
    tilde = "~"
    leftbrace = "{"
    rightbrace = "}"
    colon = ":"
    lessthan = "<"
    greaterthan = ">"
    question = "?"

    def ctrl(self, key: str) -> str:
        return f"ctrl+{key}"

    def shift(self, key: str) -> str:
        return f"shift+{key}"

    def alt(self, key: str) -> str:
        return f"alt+{key}"

    def super(self, key: str) -> str:
        return f"super+{key}"

    def ctrl_shift(self, key: str) -> str:
        return f"ctrl+shift+{key}"

    def shift_ctrl(self, key: str) -> str:
        return f"shift+ctrl+{key}"

    def ctrl_alt(self, key: str) -> str:
        return f"ctrl+alt+{key}"

    def alt_ctrl(self, key: str) -> str:
        return f"alt+ctrl+{key}"

    def shift_alt(self, key: str) -> str:
        return f"shift+alt+{key}"

    def alt_shift(self, key: str) -> str:
        return f"alt+shift+{key}"

    def ctrl_super(self, key: str) -> str:
        return f"ctrl+super+{key}"

    def super_ctrl(self, key: str) -> str:
        return f"super+ctrl+{key}"

    def shift_super(self, key: str) -> str:
        return f"shift+super+{key}"

    def super_shift(self, key: str) -> str:
        return f"super+shift+{key}"

    def alt_super(self, key: str) -> str:
        return f"alt+super+{key}"

    def super_alt(self, key: str) -> str:
        return f"super+alt+{key}"

    def ctrl_shift_alt(self, key: str) -> str:
        return f"ctrl+shift+alt+{key}"

    def ctrl_shift_super(self, key: str) -> str:
        return f"ctrl+shift+super+{key}"


Key = _Key()


def set_kitty_protocol_active(active: bool) -> None:
    global _kitty_protocol_active
    _kitty_protocol_active = bool(active)




def is_kitty_protocol_active() -> bool:
    return _kitty_protocol_active




def is_key_release(data: str) -> bool:
    if "\x1b[200~" in data:
        return False
    return any(marker in data for marker in (":3u", ":3~", ":3A", ":3B", ":3C", ":3D", ":3H", ":3F"))




def is_key_repeat(data: str) -> bool:
    if "\x1b[200~" in data:
        return False
    return any(marker in data for marker in (":2u", ":2~", ":2A", ":2B", ":2C", ":2D", ":2H", ":2F"))




def decode_kitty_printable(data: str) -> str | None:
    match = re.fullmatch(r"\x1b\[(\d+)(?::(\d*))?(?::(\d+))?(?:;(\d+))?(?::(\d+))?u", data)
    if match is None:
        return None
    try:
        codepoint = int(match.group(1))
        shifted_key = int(match.group(2)) if match.group(2) else None
        modifier = int(match.group(4) or "1") - 1
    except ValueError:
        return None

    if modifier & ~_KITTY_PRINTABLE_ALLOWED_MODIFIERS:
        return None
    if modifier & (_MODIFIER_BITS["alt"] | _MODIFIER_BITS["ctrl"]):
        return None

    effective_codepoint = codepoint
    if modifier & _MODIFIER_BITS["shift"] and shifted_key is not None:
        effective_codepoint = shifted_key
    if effective_codepoint < 32:
        return None
    try:
        return chr(effective_codepoint)
    except ValueError:
        return None




def matches_key(data: str, key_id: str) -> bool:
    return parse_key(data) == key_id




def parse_key(data: str) -> str | None:
    if data in _LEGACY_SPECIALS:
        return _LEGACY_SPECIALS[data]
    if data in _CTRL_CODES:
        return _CTRL_CODES[data]
    if alt_match := re.fullmatch(r"\x1b([A-Za-z0-9])", data):
        return f"alt+{alt_match.group(1).lower()}"
    if arrow_match := re.fullmatch(r"\x1b\[1;(\d+)(?::\d+)?([ABCD])", data):
        modifiers = _modifier_prefix(int(arrow_match.group(1)) - 1)
        key = _ARROW_SUFFIX[arrow_match.group(2)]
        return _join_key(modifiers, key)
    if home_end_match := re.fullmatch(r"\x1b\[1;(\d+)(?::\d+)?([HF])", data):
        modifiers = _modifier_prefix(int(home_end_match.group(1)) - 1)
        key = "home" if home_end_match.group(2) == "H" else "end"
        return _join_key(modifiers, key)
    if func_match := re.fullmatch(r"\x1b\[(\d+)(?:;(\d+))?(?::\d+)?~", data):
        key = _FUNCTIONAL_CODES.get(int(func_match.group(1)))
        if key is None:
            return None
        modifiers = _modifier_prefix(int(func_match.group(2) or "1") - 1)
        return _join_key(modifiers, key)
    if kitty := _parse_kitty_csi_u(data):
        codepoint, modifier = kitty
        return _format_codepoint_key(codepoint, modifier)
    if len(data) == 1 and data.isprintable():
        return data.lower() if "A" <= data <= "Z" else data
    return None




def _parse_kitty_csi_u(data: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\x1b\[(\d+)(?::(\d*))?(?::(\d+))?(?:;(\d+))?(?::(\d+))?u", data)
    if match is None:
        return None
    codepoint = int(match.group(1))
    modifier = int(match.group(4) or "1") - 1
    return codepoint, modifier


def _format_codepoint_key(codepoint: int, modifier: int) -> str | None:
    try:
        key = chr(codepoint)
    except ValueError:
        return None
    if "A" <= key <= "Z":
        key = key.lower()
    elif not key.isprintable():
        return None
    return _join_key(_modifier_prefix(modifier), key)


def _modifier_prefix(modifier: int) -> list[str]:
    names: list[str] = []
    for name in ("ctrl", "alt", "shift", "super"):
        if modifier & _MODIFIER_BITS[name]:
            names.append(name)
    return names


def _join_key(modifiers: list[str], key: str) -> str:
    return "+".join([*modifiers, key]) if modifiers else key
