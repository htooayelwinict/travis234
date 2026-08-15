from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from travis.coding_agent.operations import OperationRecovery, OperationStore


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests/fixtures/operation_crash_worker.py"


def _spawn(agent_dir: Path, session_id: str, checkpoint: str, *, hold: bool = False):
    command = [
        sys.executable,
        str(WORKER),
        "--agent-dir",
        str(agent_dir),
        "--session-id",
        session_id,
        "--checkpoint",
        checkpoint,
    ]
    if hold:
        command.append("--hold")
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run(agent_dir: Path, session_id: str, checkpoint: str) -> None:
    process = _spawn(agent_dir, session_id, checkpoint)
    assert process.wait(timeout=10) != 0


def _operation_id(agent_dir: Path, session_id: str) -> str:
    return (agent_dir / f"{session_id}.ready").read_text(encoding="utf-8")


def test_crash_windows_classify_intents_without_replaying(tmp_path: Path) -> None:
    _run(tmp_path, "before", "before_intent")
    assert not (tmp_path / "operations.sqlite3").exists()

    expected_lines = {
        "after": [],
        "during": ["effect-started"],
        "before-settle": ["effect-started", "effect-complete"],
    }
    checkpoints = {
        "after": "after_intent",
        "during": "during_effect",
        "before-settle": "after_effect_before_settlement",
    }
    for session_id, checkpoint in checkpoints.items():
        _run(tmp_path, session_id, checkpoint)

    store = OperationStore(tmp_path / "operations.sqlite3")
    report = OperationRecovery.inspect(store)

    assert report.uncertain_effect_count == 3
    assert report.uncertain_operation_count == 3
    for session_id, lines in expected_lines.items():
        operation = store.snapshot(_operation_id(tmp_path, session_id))
        assert operation is not None
        assert operation.operation.state == "uncertain"
        assert operation.effects[0].state == "uncertain"
        assert operation.effects[0].replay_policy == "never"
        effect_path = tmp_path / f"{session_id}.effects"
        actual = (
            effect_path.read_text(encoding="utf-8").splitlines()
            if effect_path.exists()
            else []
        )
        assert actual == lines
        assert not (tmp_path / f"{session_id}.jsonl").exists()
    store.close()


def test_settled_effect_stays_settled_independently_of_jsonl(tmp_path: Path) -> None:
    _run(tmp_path, "settled-only", "after_settlement")
    _run(tmp_path, "persisted", "after_jsonl_persistence")
    store = OperationStore(tmp_path / "operations.sqlite3")

    report = OperationRecovery.inspect(store)

    assert report.uncertain_effect_count == 0
    for session_id in ("settled-only", "persisted"):
        snapshot = store.snapshot(_operation_id(tmp_path, session_id))
        assert snapshot is not None
        assert snapshot.operation.state == "settled"
        assert snapshot.effects[0].state == "settled"
    assert not (tmp_path / "settled-only.jsonl").exists()
    assert (tmp_path / "persisted.jsonl").read_text(encoding="utf-8").endswith("\n")
    store.close()


def test_recovery_leaves_a_simultaneously_live_runtime_untouched(tmp_path: Path) -> None:
    live = _spawn(tmp_path, "live", "after_intent", hold=True)
    ready = tmp_path / "live.ready"
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    _run(tmp_path, "dead", "after_intent")
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        report = OperationRecovery.inspect(store)
        assert report.live_runtime_count == 1
        assert store.snapshot(_operation_id(tmp_path, "live")).effects[0].state == "intent"
        assert store.snapshot(_operation_id(tmp_path, "dead")).effects[0].state == "uncertain"
    finally:
        store.close()
        live.terminate()
        live.wait(timeout=10)


def test_corrupt_journal_does_not_change_readable_jsonl(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    session.write_text('{"type":"session","content":"KEEP"}\n', encoding="utf-8")
    before = session.read_bytes()
    journal = tmp_path / "operations.sqlite3"
    journal.write_bytes(b"not sqlite")

    try:
        store = OperationStore(journal)
    except Exception:
        store = None
    if store is not None:
        OperationRecovery.inspect(store)
        store.close()

    assert session.read_bytes() == before
