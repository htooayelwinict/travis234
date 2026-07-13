"""Thread-safe handoff queue for external coding-session messages."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Literal, Sequence

from appv231.ai.types import ImageContent

MailboxKind = Literal["steering", "follow_up"]
_KINDS: tuple[MailboxKind, ...] = ("steering", "follow_up")


@dataclass(frozen=True)
class QueuedCodingMessage:
    id: str
    kind: MailboxKind
    text: str
    images: tuple[ImageContent, ...]


class CodingTurnMailbox:
    def __init__(self) -> None:
        self._items: dict[MailboxKind, deque[QueuedCodingMessage]] = {
            "steering": deque(),
            "follow_up": deque(),
        }
        self._inflight: dict[str, QueuedCodingMessage] = {}
        self._closed = False
        self._lock = threading.RLock()

    def enqueue(
        self,
        kind: MailboxKind,
        text: str,
        images: Sequence[ImageContent] | None = None,
    ) -> QueuedCodingMessage:
        self._validate_kind(kind)
        with self._lock:
            if self._closed:
                raise RuntimeError("coding turn mailbox is closed")
            item = QueuedCodingMessage(
                id=uuid.uuid4().hex,
                kind=kind,
                text=text,
                images=tuple(images or ()),
            )
            self._items[kind].append(item)
            return item

    def drain(self, kind: MailboxKind, *, mode: str) -> list[QueuedCodingMessage]:
        self._validate_kind(kind)
        if mode not in {"one-at-a-time", "all"}:
            raise ValueError("mailbox drain mode must be one-at-a-time or all")
        with self._lock:
            queue = self._items[kind]
            count = len(queue) if mode == "all" else min(1, len(queue))
            drained = [queue.popleft() for _ in range(count)]
            self._inflight.update((item.id, item) for item in drained)
            return drained

    def acknowledge(self, message_id: str) -> bool:
        with self._lock:
            return self._inflight.pop(message_id, None) is not None

    def restore_unacknowledged(self) -> tuple[QueuedCodingMessage, ...]:
        with self._lock:
            restored = tuple(self._inflight.values())
            self._inflight.clear()
            for kind in _KINDS:
                selected = [item for item in restored if item.kind == kind]
                self._items[kind].extendleft(reversed(selected))
            return restored

    def snapshot(self, kind: MailboxKind) -> tuple[QueuedCodingMessage, ...]:
        self._validate_kind(kind)
        with self._lock:
            inflight = tuple(item for item in self._inflight.values() if item.kind == kind)
            return inflight + tuple(self._items[kind])

    def clear(self, kind: MailboxKind | None = None) -> tuple[QueuedCodingMessage, ...]:
        if kind is not None:
            self._validate_kind(kind)
        selected_kinds = _KINDS if kind is None else (kind,)
        with self._lock:
            cleared: list[QueuedCodingMessage] = []
            for selected_kind in selected_kinds:
                cleared.extend(
                    item for item in self._inflight.values() if item.kind == selected_kind
                )
                cleared.extend(self._items[selected_kind])
                self._items[selected_kind].clear()
            selected_ids = {item.id for item in cleared}
            for message_id in selected_ids:
                self._inflight.pop(message_id, None)
            return tuple(cleared)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in _KINDS:
            raise ValueError("mailbox kind must be steering or follow_up")


__all__ = ["CodingTurnMailbox", "MailboxKind", "QueuedCodingMessage"]
