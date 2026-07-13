"""Shared helpers for optional runtime collaborators."""

from __future__ import annotations

from typing import Any


def first_defined(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def call_optional(target: object, *names: str) -> Any:
    for name in names:
        method = getattr(target, name, None)
        if callable(method):
            return method()
    return None


def settings_value(settings_manager: object, *names: str) -> Any:
    for name in names:
        value = getattr(settings_manager, name, None)
        if callable(value):
            result = value()
            if result is not None:
                return result
        elif value is not None:
            return value
    return None
