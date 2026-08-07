from __future__ import annotations

import os
from pathlib import Path
import stat

from travis234_mcp_adapter.output_guard import (
    MAX_INLINE_BYTES,
    MAX_INLINE_LINES,
    OutputGuard,
    SpillRegistry,
)


def test_guard_accepts_exact_byte_and_line_limits(tmp_path: Path) -> None:
    guard = OutputGuard(SpillRegistry(tmp_path))

    exact_bytes = guard.guard("x" * MAX_INLINE_BYTES)
    exact_lines = guard.guard("x\n" * (MAX_INLINE_LINES - 1) + "x")

    assert exact_bytes.spill_path is None
    assert exact_lines.spill_path is None


def test_guard_spills_one_byte_over_with_secure_random_file(tmp_path: Path) -> None:
    spills = SpillRegistry(tmp_path)
    guard = OutputGuard(spills)
    original = "é" * (MAX_INLINE_BYTES // 2 + 1)

    first = guard.guard(original)
    second = guard.guard(original)

    assert first.truncated_by == "bytes"
    assert first.spill_path is not None
    assert first.spill_path.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(first.spill_path.stat().st_mode) == 0o600
    assert first.spill_path != second.spill_path
    assert str(first.spill_path) in first.text
    assert len(first.text.encode("utf-8")) <= MAX_INLINE_BYTES

    spills.cleanup()
    assert not first.spill_path.exists()
    assert second.spill_path is not None and not second.spill_path.exists()


def test_guard_spills_one_line_over_and_cleans_only_owned_paths(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    spills = SpillRegistry(tmp_path)
    guarded = OutputGuard(spills).guard("x\n" * MAX_INLINE_LINES + "x")

    assert guarded.truncated_by == "lines"
    assert guarded.spill_path is not None

    spills.cleanup()

    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_spill_permissions_ignore_process_umask(tmp_path: Path) -> None:
    previous = os.umask(0)
    try:
        guarded = OutputGuard(SpillRegistry(tmp_path)).guard("x" * (MAX_INLINE_BYTES + 1))
    finally:
        os.umask(previous)

    assert guarded.spill_path is not None
    assert stat.S_IMODE(guarded.spill_path.stat().st_mode) == 0o600
