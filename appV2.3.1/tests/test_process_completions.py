from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

from appv231.coding_agent.processes.completions import ProcessCompletionStore
from appv231.coding_agent.processes.types import (
    InvalidCursorError,
    ProcessClosedError,
    ProcessCompletionRecord,
    ProcessOwner,
    ProcessState,
)
from appv231.coding_agent.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode assertion")
def test_completion_survives_restart_without_cross_workspace_access(tmp_path: Path) -> None:
    root = tmp_path / "results"
    output = tmp_path / "terminal.log"
    output.write_text("build complete\n", encoding="utf-8")
    process_id = "proc_" + "a" * 32
    first = ProcessOwner("app-one", str(tmp_path / "workspace"), "agent")
    restarted = ProcessOwner("app-two", str(tmp_path / "workspace"), "agent")
    foreign = ProcessOwner("app-two", str(tmp_path / "other"), "agent")

    store = ProcessCompletionStore(root, clock=lambda: 1_700_000_001.0)
    persisted = store.persist(
        first,
        ProcessCompletionRecord(
            session_id=process_id,
            state=ProcessState.EXITED,
            exit_code=0,
            output_size=15,
            elapsed_ms=125_000,
            completed_at=1_700_000_000.0,
            launch_session_id="session-a",
            failure_code=None,
        ),
        output,
    )
    store.close()

    reopened = ProcessCompletionStore(root, clock=lambda: 1_700_000_001.0)
    try:
        recovered = reopened.resolve(restarted, process_id, cursor=0, max_bytes=51_200)

        assert recovered is not None
        assert recovered.state is ProcessState.EXITED
        assert recovered.output == "build complete\n"
        assert recovered.next_cursor == 15
        assert recovered.durable_output is True
        assert recovered.full_output_path == str(persisted)
        assert reopened.resolve(foreign, process_id, cursor=0, max_bytes=51_200) is None
        assert root.stat().st_mode & 0o777 == 0o700
        assert persisted.stat().st_mode & 0o777 == 0o600
        assert (root / "index.sqlite3").stat().st_mode & 0o777 == 0o600
    finally:
        reopened.close()


def test_completion_tail_is_bounded_and_keeps_terminal_end(tmp_path: Path) -> None:
    root = tmp_path / "results"
    output = tmp_path / "large.log"
    content = "".join(f"line-{index:04d}-{'x' * 30}\n" for index in range(3_000))
    output.write_text(content, encoding="utf-8")
    process_id = "proc_" + "b" * 32
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    store = ProcessCompletionStore(root, clock=lambda: 101.0)
    try:
        store.persist(
            owner,
            ProcessCompletionRecord(
                session_id=process_id,
                state=ProcessState.EXITED,
                exit_code=0,
                output_size=len(content.encode("utf-8")),
                elapsed_ms=1_000,
                completed_at=100.0,
                launch_session_id=None,
                failure_code=None,
            ),
            output,
        )

        tail = store.tail_snapshot(owner, process_id)

        assert tail.truncated is True
        assert tail.output_bytes <= DEFAULT_MAX_BYTES
        assert tail.output_lines <= DEFAULT_MAX_LINES
        assert tail.total_lines == 3_000
        assert tail.total_bytes == len(content.encode("utf-8"))
        assert tail.content.endswith("line-2999-" + "x" * 30)
        assert tail.last_line_partial is False
    finally:
        store.close()


def test_retention_prunes_oldest_by_count_size_and_ttl(tmp_path: Path) -> None:
    now = [1.0]
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    store = ProcessCompletionStore(
        tmp_path / "results",
        clock=lambda: now[0],
        retention_seconds=10.0,
        max_total_bytes=6,
        max_records=2,
    )
    ids = ["proc_" + character * 32 for character in "456"]
    try:
        for index, process_id in enumerate(ids, start=1):
            now[0] = float(index)
            output = tmp_path / f"retained-{index}.log"
            output.write_text("abc", encoding="utf-8")
            store.persist(
                owner,
                ProcessCompletionRecord(
                    session_id=process_id,
                    state=ProcessState.EXITED,
                    exit_code=0,
                    output_size=3,
                    elapsed_ms=index,
                    completed_at=float(index),
                    launch_session_id=None,
                    failure_code=None,
                ),
                output,
            )

        assert store.inspect(owner, ids[0]) is None
        assert store.inspect(owner, ids[1]) is not None
        assert store.inspect(owner, ids[2]) is not None
        now[0] = 20.0
        store.prune()
        assert store.inspect_many(owner, (ids[1], ids[2])) == (None, None)
        assert list((store.root / "objects").rglob("*.log")) == []
    finally:
        store.close()


def test_duplicate_completion_does_not_destroy_first_output(tmp_path: Path) -> None:
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    process_id = "proc_" + "7" * 32
    store = ProcessCompletionStore(tmp_path / "results", clock=lambda: 2.0)
    try:
        paths: list[Path] = []
        for name, content in (("first", "first"), ("second", "later")):
            path = tmp_path / f"{name}.log"
            path.write_text(content, encoding="utf-8")
            paths.append(path)
        record = ProcessCompletionRecord(
            session_id=process_id,
            state=ProcessState.EXITED,
            exit_code=0,
            output_size=5,
            elapsed_ms=1,
            completed_at=1.0,
            launch_session_id=None,
            failure_code=None,
        )
        first_path = store.persist(owner, record, paths[0])

        with pytest.raises(sqlite3.IntegrityError):
            store.persist(owner, record, paths[1])

        assert store.resolve(owner, process_id, cursor=0, max_bytes=100).output == "first"
        assert first_path.read_text(encoding="utf-8") == "first"
        assert len(list(first_path.parent.glob("*.log"))) == 1
    finally:
        store.close()


def test_two_store_instances_persist_without_lost_records(tmp_path: Path) -> None:
    root = tmp_path / "results"
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    stores = [ProcessCompletionStore(root, clock=lambda: 2.0) for _ in range(2)]
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []
    ids = ("proc_" + "8" * 32, "proc_" + "9" * 32)

    def persist(index: int) -> None:
        try:
            output = tmp_path / f"concurrent-{index}.log"
            output.write_text(str(index), encoding="utf-8")
            barrier.wait(timeout=2)
            stores[index].persist(
                owner,
                ProcessCompletionRecord(
                    session_id=ids[index],
                    state=ProcessState.EXITED,
                    exit_code=index,
                    output_size=1,
                    elapsed_ms=1,
                    completed_at=1.0,
                    launch_session_id=None,
                    failure_code=None,
                ),
                output,
            )
        except BaseException as error:  # noqa: BLE001 - captured for deterministic thread assertion.
            errors.append(error)

    threads = [threading.Thread(target=persist, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)
    try:
        assert errors == []
        assert all(not thread.is_alive() for thread in threads)
        assert [snapshot.exit_code for snapshot in stores[0].inspect_many(owner, ids) if snapshot] == [0, 1]
    finally:
        for store in stores:
            store.close()


def test_closed_completion_store_rejects_writes(tmp_path: Path) -> None:
    store = ProcessCompletionStore(tmp_path / "results")
    store.close()
    output = tmp_path / "output.log"
    output.write_text("x", encoding="utf-8")

    with pytest.raises(ProcessClosedError, match="closed"):
        store.persist(
            ProcessOwner("app", str(tmp_path), "agent"),
            ProcessCompletionRecord(
                session_id="proc_" + "a" * 32,
                state=ProcessState.EXITED,
                exit_code=0,
                output_size=1,
                elapsed_ms=1,
                completed_at=1.0,
                launch_session_id=None,
                failure_code=None,
            ),
            output,
        )


def test_sparse_large_tail_reads_bounded_bytes(tmp_path: Path, monkeypatch) -> None:
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    process_id = "proc_" + "f" * 32
    output = tmp_path / "sparse.log"
    with output.open("wb") as handle:
        handle.seek(64 * 1024 * 1024 - 5)
        handle.write(b"done\n")
    store = ProcessCompletionStore(tmp_path / "results", clock=lambda: 2.0)
    try:
        store.persist(
            owner,
            ProcessCompletionRecord(
                session_id=process_id,
                state=ProcessState.EXITED,
                exit_code=0,
                output_size=64 * 1024 * 1024,
                elapsed_ms=1,
                completed_at=1.0,
                launch_session_id=None,
                failure_code=None,
            ),
            output,
        )
        persisted = Path(store.inspect(owner, process_id).full_output_path)
        original_open = Path.open
        bytes_read = 0

        class CountingFile:
            def __init__(self, handle) -> None:
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def seek(self, *args):
                return self.handle.seek(*args)

            def read(self, *args):
                nonlocal bytes_read
                value = self.handle.read(*args)
                bytes_read += len(value)
                return value

        def measured_open(path: Path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            return CountingFile(handle) if path == persisted else handle

        monkeypatch.setattr(Path, "open", measured_open)

        tail = store.tail_snapshot(owner, process_id)

        assert tail.content.endswith("done")
        assert bytes_read <= DEFAULT_MAX_BYTES * 4
    finally:
        store.close()


def test_completion_resolve_rejects_cursor_inside_utf8_sequence(tmp_path: Path) -> None:
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    process_id = "proc_" + "0" * 32
    output = tmp_path / "utf8.log"
    encoded = ("a" + chr(0x2603) + "b").encode("utf-8")
    output.write_bytes(encoded)
    store = ProcessCompletionStore(tmp_path / "results", clock=lambda: 2.0)
    try:
        store.persist(
            owner,
            ProcessCompletionRecord(
                session_id=process_id,
                state=ProcessState.EXITED,
                exit_code=0,
                output_size=len(encoded),
                elapsed_ms=1,
                completed_at=1.0,
                launch_session_id=None,
                failure_code=None,
            ),
            output,
        )

        with pytest.raises(InvalidCursorError):
            store.resolve(owner, process_id, cursor=2, max_bytes=100)
    finally:
        store.close()


def test_corrupt_completion_index_is_quarantined_and_recreated(tmp_path: Path) -> None:
    root = tmp_path / "results"
    store = ProcessCompletionStore(root)
    store.close()
    index = root / "index.sqlite3"
    index.write_bytes(b"not a sqlite database")

    recovered = ProcessCompletionStore(root)
    try:
        assert recovered.inspect(
            ProcessOwner("app", str(tmp_path / "workspace"), "agent"),
            "proc_" + "c" * 32,
        ) is None
        quarantined = list(root.glob("index.sqlite3.corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == b"not a sqlite database"
        assert index.stat().st_mode & 0o777 == 0o600
    finally:
        recovered.close()


def test_store_cleans_only_stale_unindexed_output_objects(tmp_path: Path) -> None:
    root = tmp_path / "results"
    objects = root / "objects" / ("d" * 64)
    objects.mkdir(parents=True)
    stale = objects / f"proc_{'d' * 32}-stale.log"
    recent = objects / f"proc_{'e' * 32}-recent.log"
    stale.write_text("stale", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")
    os.utime(stale, (80.0, 80.0))
    os.utime(recent, (99.0, 99.0))

    store = ProcessCompletionStore(
        root,
        clock=lambda: 100.0,
        orphan_grace_seconds=10.0,
    )
    try:
        assert not stale.exists()
        assert recent.read_text(encoding="utf-8") == "recent"
    finally:
        store.close()


def test_inspect_many_is_one_ordered_query_and_rejects_duplicates(tmp_path: Path) -> None:
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    store = ProcessCompletionStore(tmp_path / "results", clock=lambda: 101.0)
    process_ids = ("proc_" + "1" * 32, "proc_" + "2" * 32)
    try:
        for index, process_id in enumerate(process_ids):
            output = tmp_path / f"output-{index}.log"
            output.write_text(f"result-{index}", encoding="utf-8")
            store.persist(
                owner,
                ProcessCompletionRecord(
                    session_id=process_id,
                    state=ProcessState.EXITED,
                    exit_code=index,
                    output_size=8,
                    elapsed_ms=100 + index,
                    completed_at=100.0,
                    launch_session_id=None,
                    failure_code=None,
                ),
                output,
            )
        statements: list[str] = []
        store._connection.set_trace_callback(statements.append)  # noqa: SLF001 - query-count contract.

        snapshots = store.inspect_many(owner, (process_ids[1], process_ids[0]))

        selects = [statement for statement in statements if "SELECT * FROM completions" in statement]
        assert len(selects) == 1
        assert [snapshot.session_id for snapshot in snapshots if snapshot is not None] == [
            process_ids[1],
            process_ids[0],
        ]
        with pytest.raises(ValueError, match="unique"):
            store.inspect_many(owner, (process_ids[0], process_ids[0]))
    finally:
        store.close()


def test_invalid_completion_row_is_removed_without_escaping(tmp_path: Path) -> None:
    owner = ProcessOwner("app", str(tmp_path / "workspace"), "agent")
    process_id = "proc_" + "3" * 32
    output = tmp_path / "output.log"
    output.write_text("result", encoding="utf-8")
    store = ProcessCompletionStore(tmp_path / "results", clock=lambda: 101.0)
    try:
        store.persist(
            owner,
            ProcessCompletionRecord(
                session_id=process_id,
                state=ProcessState.EXITED,
                exit_code=0,
                output_size=6,
                elapsed_ms=1,
                completed_at=100.0,
                launch_session_id=None,
                failure_code=None,
            ),
            output,
        )
        store._connection.execute(  # noqa: SLF001 - deliberate durable-row corruption.
            "UPDATE completions SET state = 'running' WHERE session_id = ?",
            (process_id,),
        )

        assert store.inspect(owner, process_id) is None
        assert store.inspect(owner, process_id) is None
        assert store._connection.execute("SELECT record_count FROM store_meta").fetchone()[0] == 0  # noqa: SLF001
    finally:
        store.close()
