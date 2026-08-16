from __future__ import annotations

import inspect
import threading
from pathlib import Path

import psutil

from travis.coding_agent.operations import OperationRuntime, OperationStore
from travis.coding_agent.operations.recovery import OperationRecovery, RecoveryReport


RUNTIME_ID = "a" * 32
SESSION_FP = "b" * 64
EFFECT_FP = "c" * 64


class _Process:
    def __init__(self, create_time: float) -> None:
        self._create_time = create_time

    def create_time(self) -> float:
        return self._create_time


def _store_with_intents(
    tmp_path: Path,
    *,
    effect_count: int = 1,
    pid: int = 321,
    process_create_time: float = 10.5,
    heartbeat_at_ms: int = 100,
) -> tuple[OperationStore, str]:
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.open_runtime(RUNTIME_ID, pid, process_create_time, 1)
    store.heartbeat_runtime(RUNTIME_ID, heartbeat_at_ms)
    operation = store.create_operation(RUNTIME_ID, SESSION_FP, "turn", 2)
    for index in range(effect_count):
        store.begin_effect(
            operation.operation_id,
            "tool",
            f"probe-{index}",
            f"{index + 1:064x}",
            3 + index,
        )
    return store, operation.operation_id


def test_no_rows_and_settled_rows_need_no_recovery(tmp_path: Path) -> None:
    empty = OperationStore(tmp_path / "empty.sqlite3")
    assert OperationRecovery.inspect(empty, now_ms=1_000) == RecoveryReport()
    empty.close()

    store, operation_id = _store_with_intents(tmp_path / "settled")
    effect = store.snapshot(operation_id).effects[0]
    store.settle_effect(effect.effect_id, "settled", "ok", 200)
    store.settle_operation(operation_id, "settled", "ok", 201)

    report = OperationRecovery.inspect(
        store,
        now_ms=1_000,
        process_lookup=lambda _pid: (_ for _ in ()).throw(psutil.NoSuchProcess(321)),
    )

    assert report.uncertain_effect_count == 0
    assert store.snapshot(operation_id).operation.state == "settled"
    store.close()


def test_dead_runtime_marks_all_intents_and_running_operation_uncertain(
    tmp_path: Path,
) -> None:
    store, operation_id = _store_with_intents(tmp_path, effect_count=2)

    report = OperationRecovery.inspect(
        store,
        now_ms=1_000,
        process_lookup=lambda _pid: (_ for _ in ()).throw(psutil.NoSuchProcess(321)),
    )

    snapshot = store.snapshot(operation_id)
    assert report == RecoveryReport(
        inspected_runtime_count=1,
        live_runtime_count=0,
        stale_runtime_count=1,
        uncertain_effect_count=2,
        uncertain_operation_count=1,
    )
    assert snapshot.operation.state == "uncertain"
    assert [effect.state for effect in snapshot.effects] == ["uncertain", "uncertain"]
    assert [effect.replay_policy for effect in snapshot.effects] == ["never", "never"]
    store.close()


def test_live_runtime_is_untouched_even_with_old_heartbeat(tmp_path: Path) -> None:
    store, operation_id = _store_with_intents(tmp_path, heartbeat_at_ms=1)

    report = OperationRecovery.inspect(
        store,
        now_ms=1_000_000,
        process_lookup=lambda _pid: _Process(10.5),
    )

    assert report.live_runtime_count == 1
    assert report.uncertain_effect_count == 0
    assert store.snapshot(operation_id).effects[0].state == "intent"
    store.close()


def test_reused_pid_with_mismatched_creation_time_is_dead(tmp_path: Path) -> None:
    store, operation_id = _store_with_intents(tmp_path)

    report = OperationRecovery.inspect(
        store,
        now_ms=1_000,
        process_lookup=lambda _pid: _Process(99.0),
    )

    assert report.stale_runtime_count == 1
    assert store.snapshot(operation_id).operation.state == "uncertain"
    store.close()


def test_unavailable_liveness_uses_sixty_second_heartbeat_lease(
    tmp_path: Path,
) -> None:
    live_store, live_operation = _store_with_intents(
        tmp_path / "fresh", heartbeat_at_ms=50_000
    )
    stale_store, stale_operation = _store_with_intents(
        tmp_path / "stale", heartbeat_at_ms=1
    )

    def unavailable(_pid):
        raise psutil.AccessDenied(321)

    live = OperationRecovery.inspect(
        live_store, now_ms=100_000, process_lookup=unavailable
    )
    stale = OperationRecovery.inspect(
        stale_store, now_ms=100_000, process_lookup=unavailable
    )

    assert live.live_runtime_count == 1
    assert live_store.snapshot(live_operation).operation.state == "running"
    assert stale.stale_runtime_count == 1
    assert stale_store.snapshot(stale_operation).operation.state == "uncertain"
    live_store.close()
    stale_store.close()


def test_two_inspectors_claim_once_and_repeated_startup_is_idempotent(
    tmp_path: Path,
) -> None:
    store, operation_id = _store_with_intents(tmp_path, effect_count=2)
    reports: list[RecoveryReport] = []

    def inspect_store() -> None:
        reports.append(
            OperationRecovery.inspect(
                store,
                now_ms=1_000,
                process_lookup=lambda _pid: (_ for _ in ()).throw(
                    psutil.NoSuchProcess(321)
                ),
            )
        )

    threads = [threading.Thread(target=inspect_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(report.uncertain_effect_count for report in reports) == 2
    assert sum(report.uncertain_operation_count for report in reports) == 1
    repeated = OperationRecovery.inspect(store, now_ms=2_000)
    assert repeated.uncertain_effect_count == 0
    assert store.snapshot(operation_id).operation.state == "uncertain"
    store.close()


def test_recovery_ignores_jsonl_and_exports_no_replay_api(tmp_path: Path) -> None:
    store, _operation_id = _store_with_intents(tmp_path)
    session = tmp_path / "session.jsonl"
    session.write_text('{"type":"session","private":"KEEP"}\n', encoding="utf-8")
    before = session.read_bytes()

    OperationRecovery.inspect(
        store,
        now_ms=1_000,
        process_lookup=lambda _pid: (_ for _ in ()).throw(psutil.NoSuchProcess(321)),
    )

    assert session.read_bytes() == before
    assert not hasattr(OperationRecovery, "replay")
    assert not hasattr(OperationRecovery, "resume_effect")
    assert "effect_executor()" not in inspect.getsource(OperationRecovery.inspect)
    store.close()


def test_runtime_startup_runs_recovery_and_exposes_bounded_report(tmp_path: Path) -> None:
    store, operation_id = _store_with_intents(tmp_path, pid=999_999)
    store.close()

    runtime = OperationRuntime.from_settings(
        tmp_path,
        {"mode": "observe", "maxBytes": 1024 * 1024},
        heartbeat_interval_seconds=None,
    )

    assert runtime.recovery_report.uncertain_effect_count == 1
    assert runtime.store.snapshot(operation_id).operation.state == "uncertain"
    diagnostic = runtime.recovery_report.as_dict()
    assert set(diagnostic) == {
        "type",
        "inspectedRuntimeCount",
        "liveRuntimeCount",
        "staleRuntimeCount",
        "uncertainEffectCount",
        "uncertainOperationCount",
        "unavailable",
    }
    runtime.close()


def test_corrupt_or_unavailable_store_degrades_to_sanitized_report() -> None:
    class _UnavailableStore:
        def recovery_leases(self):
            raise RuntimeError("private database detail")

    report = OperationRecovery.inspect(_UnavailableStore())  # type: ignore[arg-type]

    assert report == RecoveryReport(unavailable=True)
    assert "private database detail" not in str(report.as_dict())
