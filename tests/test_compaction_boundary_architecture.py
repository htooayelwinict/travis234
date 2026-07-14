from __future__ import annotations

from pathlib import Path


def test_compaction_callers_use_only_public_coordinator() -> None:
    root = Path(__file__).parents[1] / "travis"
    forbidden = (
        "session._begin_compaction",
        "session._end_compaction",
        "compaction._last_compression_result",
        "_apply_compaction_boundary",
    )
    failures: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        failures.extend(f"{path.relative_to(root)}: {token}" for token in forbidden if token in text)
    assert failures == []
