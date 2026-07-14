"""Output accounting shared by live, durable, and in-memory spools."""

from __future__ import annotations


def line_count(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") + int(not content.endswith("\n"))
