from __future__ import annotations

import threading

import pytest

from appv231.ai.types import ImageContent
from appv231.coding_agent.mailbox import CodingTurnMailbox


def test_concurrent_enqueue_during_drain_is_not_lost() -> None:
    mailbox = CodingTurnMailbox()
    first = mailbox.enqueue("steering", "same text")
    barrier = threading.Barrier(2)
    drained = []

    def drain() -> None:
        barrier.wait(timeout=1)
        drained.extend(mailbox.drain("steering", mode="one-at-a-time"))

    thread = threading.Thread(target=drain)
    thread.start()
    barrier.wait(timeout=1)
    second = mailbox.enqueue("steering", "same text")
    thread.join(timeout=1)

    assert [item.id for item in drained] == [first.id]
    assert [item.id for item in mailbox.snapshot("steering")] == [first.id, second.id]
    assert first.id != second.id


def test_acknowledge_uses_id_and_keeps_equal_text_distinct() -> None:
    mailbox = CodingTurnMailbox()
    first = mailbox.enqueue("steering", "duplicate")
    second = mailbox.enqueue("steering", "duplicate")
    mailbox.drain("steering", mode="all")

    assert mailbox.acknowledge(second.id) is True
    assert [item.id for item in mailbox.snapshot("steering")] == [first.id]
    assert mailbox.acknowledge("missing") is False


def test_restore_unacknowledged_preserves_original_fifo() -> None:
    mailbox = CodingTurnMailbox()
    first = mailbox.enqueue("steering", "one")
    second = mailbox.enqueue("steering", "two")
    mailbox.drain("steering", mode="all")
    mailbox.enqueue("steering", "three")

    restored = mailbox.restore_unacknowledged()

    assert [item.id for item in restored] == [first.id, second.id]
    assert [item.text for item in mailbox.snapshot("steering")] == ["one", "two", "three"]


def test_follow_up_isolation_images_and_drain_modes() -> None:
    mailbox = CodingTurnMailbox()
    image = ImageContent(data="abc", mime_type="image/png")
    mailbox.enqueue("steering", "one")
    mailbox.enqueue("steering", "two")
    follow_up = mailbox.enqueue("follow_up", "later", [image])

    assert [item.text for item in mailbox.drain("steering", mode="one-at-a-time")] == ["one"]
    assert [item.text for item in mailbox.drain("steering", mode="all")] == ["two"]
    assert mailbox.snapshot("follow_up") == (follow_up,)
    assert follow_up.images == (image,)


def test_clear_returns_and_removes_queued_and_inflight_items() -> None:
    mailbox = CodingTurnMailbox()
    first = mailbox.enqueue("steering", "one")
    second = mailbox.enqueue("steering", "two")
    mailbox.drain("steering", mode="one-at-a-time")

    cleared = mailbox.clear("steering")

    assert [item.id for item in cleared] == [first.id, second.id]
    assert mailbox.snapshot("steering") == ()


def test_close_rejects_new_messages_but_retains_accepted_items() -> None:
    mailbox = CodingTurnMailbox()
    accepted = mailbox.enqueue("steering", "accepted")
    mailbox.close()

    with pytest.raises(RuntimeError, match="closed"):
        mailbox.enqueue("steering", "late")

    assert mailbox.drain("steering", mode="all") == [accepted]
    with pytest.raises(ValueError, match="mode"):
        mailbox.drain("follow_up", mode="invalid")
