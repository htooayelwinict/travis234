"""Native modifier-key probe surface matching the established safe fallback behavior."""

from __future__ import annotations


ModifierKey = str


def is_native_modifier_pressed(key: ModifierKey) -> bool:
    return False
