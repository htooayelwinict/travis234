from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from appv231.coding_agent.processes.output import SanitizedOutputSpool
from appv231.coding_agent.processes.types import (
    InvalidCursorError,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
)


def test_process_snapshot_serializes_stable_tool_details() -> None:
    snapshot = ProcessSnapshot(
        session_id="proc_0123",
        state=ProcessState.RUNNING,
        output="ready\n",
        cursor=0,
        next_cursor=6,
        output_size=6,
        exit_code=None,
        tty=False,
        elapsed_ms=10_001,
        command="npm test",
        cwd="/workspace",
    )

    assert snapshot.as_details() == {
        "status": "running",
        "sessionId": "proc_0123",
        "cursor": 0,
        "nextCursor": 6,
        "outputSize": 6,
        "exitCode": None,
        "tty": False,
        "elapsedMs": 10_001,
        "suggestedPollDelayMs": 1000,
    }
    assert ProcessState.EXITED.terminal is True
    assert ProcessState.DRAINING.terminal is False


def test_process_owner_is_workspace_and_origin_scoped() -> None:
    owner = ProcessOwner("app-1", "/workspace", "agent")

    assert owner == ProcessOwner("app-1", "/workspace", "agent")
    assert owner != ProcessOwner("app-1", "/other", "agent")
    assert owner != ProcessOwner("app-1", "/workspace", "user")


def test_output_cursor_is_stable_across_split_utf8_and_terminal_controls(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path)
    encoded = "before \N{SNOWMAN} after\r\n".encode("utf-8")
    snowman_start = encoded.index("\N{SNOWMAN}".encode("utf-8"))

    spool.append(encoded[: snowman_start + 1])
    spool.append(encoded[snowman_start + 1 :] + b"\x1b]52;c;c2VjcmV0")
    spool.append(b"\x07\x1b[31mred\x1b[0m\x00\x03")

    first = spool.read(0, 8)
    second = spool.read(first.next_cursor, 512)

    assert first.text + second.text == "before \N{SNOWMAN} after\nred"
    assert spool.read(0, 8) == first
    assert "52" not in second.text
    assert "\x1b" not in second.text


def test_output_spool_discards_control_sequence_split_at_finish(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path)
    spool.append(b"kept\x1b]52;c;unfinished")

    spool.finish()

    assert spool.read(0, 1024).text == "kept"


def test_output_spool_removes_c1_string_and_csi_sequences(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path)

    spool.append("safe\u009d52;c;secret\u009cafter\u009b31mred\u009b0m".encode("utf-8"))
    spool.finish()

    assert spool.read(0, 1024).text == "safeafterred"


def test_output_spool_validates_cursor_and_utf8_boundary(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path)
    spool.append("a\N{SNOWMAN}b".encode("utf-8"))

    first = spool.read(0, 2)
    rest = spool.read(first.next_cursor, 16)

    assert first.text == "a"
    assert rest.text == "\N{SNOWMAN}b"
    with pytest.raises(InvalidCursorError, match="cursor 99"):
        spool.read(99, 16)
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        spool.read(0, 0)


def test_output_spool_is_private_and_export_copy_has_independent_lifetime(tmp_path: Path) -> None:
    spool_dir = tmp_path / "spools"
    export_dir = tmp_path / "exports"
    spool = SanitizedOutputSpool(spool_dir)
    spool.append(b"complete\n")
    spool.finish()

    exported = spool.export_copy(export_dir)

    assert spool_dir.stat().st_mode & 0o777 == 0o700
    assert spool.path.stat().st_mode & 0o777 == 0o600
    assert exported.stat().st_mode & 0o777 == 0o600
    assert exported.read_text(encoding="utf-8") == "complete\n"
    spool.close(remove=True)
    assert not spool.path.exists()
    assert exported.exists()


def test_output_spool_export_failure_preserves_original_error_and_removes_copy(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path / "spools")
    spool.append(b"complete")
    spool.finish()

    with patch("appv231.coding_agent.processes.output.shutil.copyfileobj", side_effect=OSError("copy failed")):
        with pytest.raises(OSError, match="copy failed"):
            spool.export_copy(tmp_path / "exports")

    assert list((tmp_path / "exports").iterdir()) == []


def test_output_spool_serializes_concurrent_writers_without_losing_bytes(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path)
    barrier = threading.Barrier(3)

    def write(prefix: str) -> None:
        barrier.wait()
        for index in range(100):
            spool.append(f"{prefix}{index}\n".encode("utf-8"))

    threads = [threading.Thread(target=write, args=(prefix,)) for prefix in ("a", "b")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    spool.finish()
    output = spool.read(0, 16_384).text.splitlines()
    assert len(output) == 200
    assert {f"a{index}" for index in range(100)} <= set(output)
    assert {f"b{index}" for index in range(100)} <= set(output)


def test_output_spool_rejects_append_after_finish(tmp_path: Path) -> None:
    spool = SanitizedOutputSpool(tmp_path)
    spool.finish()

    with pytest.raises(RuntimeError, match="finished output spool"):
        spool.append(b"late")


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode assertion")
def test_export_copy_does_not_inherit_permissive_umask(tmp_path: Path) -> None:
    previous = os.umask(0)
    try:
        spool = SanitizedOutputSpool(tmp_path / "spool")
        spool.append(b"private")
        spool.finish()
        exported = spool.export_copy(tmp_path / "export")
    finally:
        os.umask(previous)

    assert exported.stat().st_mode & 0o777 == 0o600
