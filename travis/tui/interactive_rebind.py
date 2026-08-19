"""Transactional cached-session binding for interactive controllers."""

from __future__ import annotations


def rebind_cached_session(controller: object, session: object) -> object:
    try:
        previous = object.__getattribute__(controller, "_bound_session")
    except AttributeError:
        dependencies = object.__getattribute__(controller, "dependencies")
        app = dependencies.port.read("app")
        previous = getattr(app, "session")
    object.__setattr__(controller, "_bound_session", session)
    return previous


__all__ = ("rebind_cached_session",)
