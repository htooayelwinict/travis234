from __future__ import annotations

import hashlib
from pathlib import Path

from travis.coding_agent.operations import OperationStore
from travis.tui.interactive_operations import OperationInspector


RUNTIME_ID = "a" * 32
SESSION_ID = "session-visible"
OTHER_SESSION_ID = "session-hidden"


def _fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _operation(store: OperationStore, session_id: str, now_ms: int):
    operation = store.create_operation(
        RUNTIME_ID,
        _fingerprint(session_id),
        "turn",
        now_ms,
    )
    store.advance(
        operation.operation_id,
        "prepared",
        {"private": "DO-NOT-RENDER"},
        now_ms + 1,
    )
    effect = store.begin_effect(
        operation.operation_id,
        "tool",
        "bash",
        f"{now_ms:064x}",
        now_ms + 2,
        effect_classes=("execute",),
    )
    return operation, effect


def test_summary_is_scoped_and_contains_only_bounded_metadata(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.open_runtime(RUNTIME_ID, 123, 1.0, 1)
    visible, effect = _operation(store, SESSION_ID, 10)
    hidden, _ = _operation(store, OTHER_SESSION_ID, 20)
    store.settle_effect(effect.effect_id, "settled", "ok", 13)
    store.settle_operation(visible.operation_id, "settled", "ok", 14)

    lines = OperationInspector(store, SESSION_ID).summary()
    rendered = "\n".join(lines)

    assert "Operations: 1" in rendered
    assert visible.operation_id in rendered
    assert hidden.operation_id not in rendered
    assert "turn" in rendered
    assert "settled" in rendered
    assert "pc=1" in rendered
    assert "effects=1" in rendered
    assert "DO-NOT-RENDER" not in rendered
    assert _fingerprint(SESSION_ID) not in rendered
    store.close()


def test_detail_hides_other_sessions_and_sensitive_fields(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.open_runtime(RUNTIME_ID, 123, 1.0, 1)
    visible, effect = _operation(store, SESSION_ID, 10)
    hidden, _ = _operation(store, OTHER_SESSION_ID, 20)

    inspector = OperationInspector(store, SESSION_ID)
    visible_text = "\n".join(inspector.detail(visible.operation_id))

    assert visible.operation_id in visible_text
    assert effect.effect_id in visible_text
    assert "kind=tool" in visible_text
    assert "name=bash" in visible_text
    assert "state=intent" in visible_text
    assert "replay=never" in visible_text
    assert "createdAtMs=10" in visible_text
    assert "settledAtMs=-" in visible_text
    assert "DO-NOT-RENDER" not in visible_text
    assert effect.fingerprint not in visible_text
    assert "execute" not in visible_text
    assert inspector.detail(hidden.operation_id) == ("Unknown operation.",)
    assert inspector.detail("op_" + "f" * 32) == ("Unknown operation.",)
    store.close()


def test_inspector_is_read_only_and_degrades_when_store_is_unavailable(
    tmp_path: Path,
) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.open_runtime(RUNTIME_ID, 123, 1.0, 1)
    operation, _ = _operation(store, SESSION_ID, 10)
    before = store.snapshot(operation.operation_id)

    inspector = OperationInspector(store, SESSION_ID)
    inspector.summary()
    inspector.detail(operation.operation_id)

    assert store.snapshot(operation.operation_id) == before
    store.close()
    assert inspector.summary() == ("Operation journal is unavailable.",)


def test_inspection_output_is_bounded_to_one_hundred_rows(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3", max_effects_per_operation=200)
    store.open_runtime(RUNTIME_ID, 123, 1.0, 1)
    newest = None
    for index in range(105):
        newest = store.create_operation(
            RUNTIME_ID,
            _fingerprint(SESSION_ID),
            "turn",
            100 + index,
        )
    assert newest is not None
    for index in range(105):
        store.begin_effect(
            newest.operation_id,
            "tool",
            f"probe-{index}",
            f"{index + 1:064x}",
            1_000 + index,
        )

    inspector = OperationInspector(store, SESSION_ID)
    summary = inspector.summary()
    detail = inspector.detail(newest.operation_id)

    assert len(summary) == 101
    assert "newest 100" in summary[0]
    assert len(detail) == 103
    assert "effects=100/105" in detail[1]
    assert "probe-99" in detail[-1]
    assert "probe-100" not in "\n".join(detail)
    store.close()
