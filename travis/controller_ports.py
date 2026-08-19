"""Shared binding primitive for explicitly composed runtime collaborators."""

from __future__ import annotations

from types import MethodType
from typing import cast


class BoundController[ControllerPortT]:
    """Bind a characterized controller method to its injected structural port."""

    __slots__ = ("_port",)

    def __init__(self, port: ControllerPortT) -> None:
        object.__setattr__(self, "_port", port)

    def __getattribute__[AttributeT](self, name: str) -> AttributeT:
        attribute = object.__getattribute__(self, name)
        if isinstance(attribute, MethodType) and attribute.__self__ is self:
            try:
                port = object.__getattribute__(self, "_port")
            except AttributeError:
                return cast(AttributeT, attribute)
            return cast(AttributeT, attribute.__func__.__get__(port, type(port)))
        return cast(AttributeT, attribute)


__all__ = ("BoundController",)
