from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from travis.coding_agent.session_index import SessionIndex, SessionIndexRecord, SessionScanStats


def _large_session(path: Path, *, records: int = 2_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "session",
        "version": 3,
        "id": "large",
        "timestamp": "2026-07-13T00:00:00Z",
        "cwd": str(path.parent),
    }
    lines = [json.dumps(header)]
    for index in range(records):
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "id": str(index),
                    "message": {"role": "user", "content": f"message-{index}-" + "x" * 1_000},
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.resolve()


def test_session_index_round_trips_summary(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path / "catalog.sqlite3")
    record = SessionIndexRecord(
        path=tmp_path / "one.jsonl",
        session_id="one",
        cwd=tmp_path,
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        modified_ns=10,
        size_bytes=20,
        device=1,
        inode=2,
        name="Demo",
        preview="hello",
        model="provider/model",
    )

    index.upsert(record)

    assert index.query() == (record,)
    index.close()


def test_warm_reconcile_reads_no_history_bytes(tmp_path: Path) -> None:
    session = _large_session(tmp_path / "large.jsonl")
    index = SessionIndex(tmp_path / "catalog.sqlite3")

    cold = index.reconcile([session])
    warm = index.reconcile([session])

    assert cold.files_backfilled == 1
    assert cold.bytes_read <= 73_728
    assert cold.records_decoded <= 257
    assert warm == SessionScanStats(files_statted=1, cache_hits=1)
