"""Native modifier-key probe surface matching Pi's safe fallback behavior."""

from __future__ import annotations


ModifierKey = str


def is_native_modifier_pressed(key: ModifierKey) -> bool:
    return False


isNativeModifierPressed = is_native_modifier_pressed
