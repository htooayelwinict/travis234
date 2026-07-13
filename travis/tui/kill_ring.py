"""kill ring for editor yank/yank-pop behavior."""

from __future__ import annotations

from collections.abc import Mapping


class KillRing:
    def __init__(self) -> None:
        self._ring: list[str] = []

    def push(self, text: str, opts: Mapping[str, object] | None = None) -> None:
        if not text:
            return
        opts = opts or {}
        prepend = bool(opts.get("prepend"))
        accumulate = bool(opts.get("accumulate"))
        if accumulate and self._ring:
            last = self._ring.pop()
            self._ring.append(text + last if prepend else last + text)
        else:
            self._ring.append(text)

    def peek(self) -> str | None:
        return self._ring[-1] if self._ring else None

    def rotate(self) -> None:
        if len(self._ring) > 1:
            last = self._ring.pop()
            self._ring.insert(0, last)

    @property
    def length(self) -> int:
        return len(self._ring)
