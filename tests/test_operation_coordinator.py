from __future__ import annotations

import gc
import hashlib
import sqlite3
import time
import weakref
from pathlib import Path

import pytest

from travis.ai.providers.faux import faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.agent_session_services import (
    create_agent_session_from_services,
    create_agent_session_services,
)
from travis.coding_agent.settings_manager import SettingsManager
from travis.app import CodingApp
from travis.coding_agent.operations.coordinator import (
    EffectHandle,
    NullOperationCoordinator,
    OperationCoordinatorOrderError,
    OperationRuntime,
)
from travis.coding_agent.operations.recovery import RecoveryReport
from travis.coding_agent.operations.store import OperationStore


RUNTIME_ID = "a" * 32
EFFECT_FP = "b" * 64
SOURCE_KEY = "c" * 64


class Clock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _runtime(
    tmp_path: Path,
    *,
    clock: Clock | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> tuple[OperationRuntime, OperationStore]:
    store = OperationStore(tmp_path / "operations.sqlite3")
    runtime = OperationRuntime(
        store,
        runtime_id=RUNTIME_ID,
        pid=123,
        process_create_time=10.5,
        clock_ms=clock or Clock(),
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    return runtime, store


def test_coordinator_records_normal_sequence_without_raw_session_content(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    coordinator = runtime.for_session("private-session-id")

    operation_id = coordinator.start("turn", "private-session-id")
    counter = coordinator.advance("turn_started", {"turn_sequence": 1})
    effect = coordinator.begin_effect("provider", "openrouter", EFFECT_FP)
    assert isinstance(effect, EffectHandle)
    coordinator.settle_effect(effect, "ok")
    coordinator.record_usage(
        SOURCE_KEY,
        provider="openrouter",
        model="model",
        input_tokens=10,
        output_tokens=4,
        cache_read_tokens=2,
        cache_write_tokens=0,
        cost=0.25,
    )
    coordinator.complete("ok")

    assert isinstance(operation_id, str)
    assert counter == 1
    snapshot = store.snapshot(operation_id)
    assert snapshot is not None
    assert snapshot.operation.session_fingerprint == hashlib.sha256(
        b"private-session-id"
    ).hexdigest()
    assert snapshot.operation.state == "settled"
    assert snapshot.effects[0].state == "settled"
    assert snapshot.usage[0].input_tokens == 10
    assert b"private-session-id" not in (tmp_path / "operations.sqlite3").read_bytes()

    coordinator.close()
    runtime.close()


def test_null_coordinator_is_a_shape_compatible_noop() -> None:
    coordinator = NullOperationCoordinator("disabled")

    assert coordinator.enabled is False
    assert coordinator.start("turn", "session") is None
    assert coordinator.advance("turn_started", {}) == 0
    assert coordinator.begin_effect("tool", "read", EFFECT_FP) is None
    assert coordinator.settle_effect(None, "ok") is False
    assert coordinator.record_usage(
        SOURCE_KEY,
        provider="p",
        model="m",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost=0,
    ) is False
    assert coordinator.complete("ok") is False
    coordinator.disable("journal_unavailable")
    coordinator.close()


def test_invalid_coordinator_order_is_rejected_without_disabling(tmp_path: Path) -> None:
    runtime, _store = _runtime(tmp_path)
    coordinator = runtime.for_session("session")

    with pytest.raises(OperationCoordinatorOrderError, match="operation_not_started"):
        coordinator.advance("turn_started", {})
    with pytest.raises(OperationCoordinatorOrderError, match="operation_not_started"):
        coordinator.begin_effect("tool", "read", EFFECT_FP)
    with pytest.raises(OperationCoordinatorOrderError, match="operation_not_started"):
        coordinator.complete("ok")

    assert coordinator.enabled is True
    runtime.close()


@pytest.mark.parametrize(
    "failing_method",
    [
        "create_operation",
        "advance",
        "begin_effect",
        "settle_effect",
        "record_usage",
        "settle_operation",
    ],
)
def test_store_failure_disables_once_and_emits_only_sanitized_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_method: str
) -> None:
    runtime, store = _runtime(tmp_path)
    diagnostics: list[object] = []
    coordinator = runtime.for_session(
        "DO_NOT_REPORT_SESSION", diagnostic_sink=diagnostics.append
    )
    original = getattr(store, failing_method)
    operation_id = None
    handle = None
    if failing_method != "create_operation":
        operation_id = coordinator.start("turn", "DO_NOT_REPORT_SESSION")
    if failing_method == "settle_effect":
        handle = coordinator.begin_effect("tool", "read", EFFECT_FP)

    def explode(*_args, **_kwargs):
        raise RuntimeError("PRIVATE FAILURE DETAILS")

    monkeypatch.setattr(store, failing_method, explode)
    if failing_method == "create_operation":
        assert coordinator.start("turn", "DO_NOT_REPORT_SESSION") is None
    elif failing_method == "advance":
        assert coordinator.advance("turn_started", {}) == 0
    elif failing_method == "begin_effect":
        assert coordinator.begin_effect("tool", "read", EFFECT_FP) is None
    elif failing_method == "settle_effect":
        assert coordinator.settle_effect(handle, "ok") is False
    elif failing_method == "record_usage":
        assert coordinator.record_usage(
            SOURCE_KEY,
            provider="p",
            model="m",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0,
        ) is False
    else:
        assert coordinator.complete("ok") is False

    assert operation_id is None or operation_id.startswith("op_")
    assert coordinator.enabled is False
    assert len(diagnostics) == 1
    assert diagnostics[0].as_dict() == {
        "type": "operation_journal_diagnostic",
        "kind": "turn",
        "code": "journal_unavailable",
    }
    assert "PRIVATE" not in repr(diagnostics)
    assert "DO_NOT_REPORT_SESSION" not in repr(diagnostics)
    assert coordinator.start("turn", "another-secret") is None
    assert len(diagnostics) == 1

    monkeypatch.setattr(store, failing_method, original)
    runtime.close()


def test_invalid_operation_kind_cannot_escape_through_diagnostic(tmp_path: Path) -> None:
    runtime, _store = _runtime(tmp_path)
    diagnostics: list[object] = []
    coordinator = runtime.for_session("secret", diagnostic_sink=diagnostics.append)

    assert coordinator.start("PRIVATE KIND", "secret") is None

    assert diagnostics[0].as_dict() == {
        "type": "operation_journal_diagnostic",
        "kind": "session",
        "code": "journal_unavailable",
    }
    assert "PRIVATE" not in repr(diagnostics)
    runtime.close()


def test_cancellation_settles_open_effects_and_operation(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    coordinator = runtime.for_session("session")
    operation_id = coordinator.start("turn", "session")
    coordinator.begin_effect("provider", "model", EFFECT_FP)

    assert coordinator.complete("cancelled") is True

    snapshot = store.snapshot(operation_id)
    assert snapshot is not None
    assert snapshot.operation.state == "cancelled"
    assert snapshot.effects[0].state == "cancelled"
    runtime.close()


def test_runtime_heartbeats_and_closes_lease_after_coordinators(tmp_path: Path) -> None:
    clock = Clock()
    runtime, store = _runtime(
        tmp_path, clock=clock, heartbeat_interval_seconds=0.01
    )
    coordinator = runtime.for_session(None)
    operation_id = coordinator.start("turn", None)
    deadline = time.monotonic() + 1
    while clock.value < 1_003 and time.monotonic() < deadline:
        time.sleep(0.01)

    runtime.close()

    connection = sqlite3.connect(tmp_path / "operations.sqlite3")
    lease = connection.execute(
        "SELECT heartbeat_at_ms, closed_at_ms FROM runtime_leases WHERE runtime_id=?",
        (RUNTIME_ID,),
    ).fetchone()
    operation = connection.execute(
        "SELECT state FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()
    connection.close()
    assert lease is not None and lease[0] >= 1_003 and lease[1] is not None
    assert operation == ("cancelled",)
    assert runtime.heartbeat_thread_alive is False


def test_heartbeat_failure_disables_existing_and_future_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics: list[object] = []
    runtime, store = _runtime(tmp_path, heartbeat_interval_seconds=0.01)
    coordinator = runtime.for_session("first", diagnostic_sink=diagnostics.append)

    def fail_heartbeat(*_args, **_kwargs):
        raise RuntimeError("PRIVATE HEARTBEAT FAILURE")

    monkeypatch.setattr(store, "heartbeat_runtime", fail_heartbeat)
    deadline = time.monotonic() + 1
    while coordinator.enabled and time.monotonic() < deadline:
        time.sleep(0.01)

    assert coordinator.enabled is False
    assert isinstance(runtime.for_session("later"), NullOperationCoordinator)
    assert len(diagnostics) == 1
    assert "PRIVATE" not in repr(diagnostics)
    runtime.close()


def test_heartbeat_thread_does_not_retain_an_abandoned_runtime(tmp_path: Path) -> None:
    runtime, _store = _runtime(tmp_path, heartbeat_interval_seconds=0.01)
    thread = runtime._heartbeat_thread
    runtime_ref = weakref.ref(runtime)

    del runtime
    gc.collect()
    assert thread is not None
    thread.join(timeout=0.2)

    assert runtime_ref() is None
    assert thread.is_alive() is False


def test_runtime_close_is_fail_open_when_store_checkpoint_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, store = _runtime(tmp_path, heartbeat_interval_seconds=None)
    original_close = store.close

    def fail_close() -> None:
        raise RuntimeError("PRIVATE CLOSE FAILURE")

    monkeypatch.setattr(store, "close", fail_close)

    runtime.close()

    monkeypatch.setattr(store, "close", original_close)
    original_close()


def test_runtime_from_settings_uses_only_canonical_path_and_honors_disabled(
    tmp_path: Path,
) -> None:
    observed = OperationRuntime.from_settings(
        tmp_path,
        {"mode": "observe", "maxBytes": 1024 * 1024},
        heartbeat_interval_seconds=None,
    )
    assert observed.path == tmp_path / "operations.sqlite3"
    observed.close()

    disabled_dir = tmp_path / "disabled"
    disabled = OperationRuntime.from_settings(
        disabled_dir,
        {"mode": "disabled", "maxBytes": 1024 * 1024},
    )
    assert isinstance(disabled.for_session("session"), NullOperationCoordinator)
    assert not (disabled_dir / "operations.sqlite3").exists()
    disabled.close()


def test_runtime_from_settings_closes_store_when_lease_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingStore:
        def __init__(self, path, **_kwargs) -> None:
            self.path = Path(path)
            self.closed = False

        def open_runtime(self, *_args, **_kwargs) -> None:
            raise RuntimeError("lease open failed")

        def close(self) -> None:
            self.closed = True

    created: list[FailingStore] = []

    def create_store(path, **kwargs):
        store = FailingStore(path, **kwargs)
        created.append(store)
        return store

    monkeypatch.setattr(
        "travis.coding_agent.operations.coordinator.OperationStore", create_store
    )

    runtime = OperationRuntime.from_settings(
        tmp_path,
        {"mode": "observe", "maxBytes": 1024},
        heartbeat_interval_seconds=None,
    )

    assert created[0].closed is True
    assert isinstance(runtime.for_session("session"), NullOperationCoordinator)
    runtime.close()


class _TrackingCoordinator(NullOperationCoordinator):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _TrackingRuntime:
    def __init__(self) -> None:
        self.sessions: list[str | None] = []
        self.coordinators: list[_TrackingCoordinator] = []
        self.closed = False

    def for_session(self, session_id, *, diagnostic_sink=None):
        del diagnostic_sink
        self.sessions.append(session_id)
        coordinator = _TrackingCoordinator()
        self.coordinators.append(coordinator)
        return coordinator

    def close(self) -> None:
        self.closed = True


def test_agent_session_binds_and_closes_injected_coordinator(tmp_path: Path) -> None:
    runtime = _TrackingRuntime()
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "session.jsonl"),
        operation_runtime=runtime,
    )

    assert runtime.sessions == [session.session_id]
    assert session.operation_coordinator is runtime.coordinators[0]

    session.dispose()

    assert runtime.coordinators[0].closed is True
    assert runtime.closed is False


def test_agent_session_services_create_and_forward_owned_runtime(tmp_path: Path) -> None:
    services = create_agent_session_services(
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / "agent")}
    )

    result = create_agent_session_from_services(
        {"services": services, "model": faux_model()}
    )

    assert services["operationRuntime"] is result.session.operation_runtime
    assert result.session.operation_coordinator.enabled is True
    result.session.dispose()
    assert services["operationRuntime"].heartbeat_thread_alive is False


def test_owned_runtime_recovery_report_becomes_sanitized_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _TrackingRuntime()
    runtime.recovery_report = RecoveryReport(
        inspected_runtime_count=1,
        stale_runtime_count=1,
        uncertain_effect_count=2,
        uncertain_operation_count=1,
    )
    monkeypatch.setattr(
        "travis.coding_agent.agent_session_services.OperationRuntime.from_settings",
        lambda *_args, **_kwargs: runtime,
    )
    services = create_agent_session_services(
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / "agent")}
    )

    result = create_agent_session_from_services(
        {"services": services, "model": faux_model()}
    )

    assert services["diagnostics"] == [runtime.recovery_report.as_dict()]
    result.session.dispose()


def test_agent_session_services_borrow_supplied_runtime(tmp_path: Path) -> None:
    runtime = _TrackingRuntime()
    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "operationRuntime": runtime,
        }
    )

    result = create_agent_session_from_services(
        {"services": services, "model": faux_model()}
    )
    result.session.dispose()

    assert services["operationRuntime"] is runtime
    assert runtime.coordinators[0].closed is True
    assert runtime.closed is False


def test_agent_session_factory_closes_owned_runtime_when_construction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _TrackingRuntime()

    def fail_session(**_kwargs: object):
        raise RuntimeError("session construction failed")

    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "sessionFactory": fail_session,
        }
    )
    monkeypatch.setattr(
        "travis.coding_agent.agent_session_services.OperationRuntime.from_settings",
        lambda *_args, **_kwargs: runtime,
    )

    with pytest.raises(RuntimeError, match="session construction failed"):
        create_agent_session_from_services(
            {"services": services, "model": faux_model()}
        )

    assert runtime.closed is True


def test_coding_app_owns_canonical_runtime_and_closes_it_after_session(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agent"
    settings = SettingsManager.in_memory(
        {"operations": {"mode": "observe", "maxBytes": 1024 * 1024}}
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        enable_tui=False,
        agent_dir=str(agent_dir),
        settings_manager=settings,
    )

    assert app.operation_runtime.path == agent_dir / "operations.sqlite3"
    assert app.session.operation_runtime is app.operation_runtime
    app.close()

    assert app.operation_runtime.heartbeat_thread_alive is False


def test_coding_app_closes_operation_runtime_when_session_construction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _TrackingRuntime()
    monkeypatch.setattr(
        "travis.app.OperationRuntime.from_settings",
        lambda *_args, **_kwargs: runtime,
    )

    def fail_session(*_args, **_kwargs):
        raise RuntimeError("session construction failed")

    monkeypatch.setattr(CodingApp, "_create_session", fail_session)

    with pytest.raises(RuntimeError, match="session construction failed"):
        CodingApp(
            cwd=str(tmp_path),
            model=faux_model(),
            enable_tui=False,
            agent_dir=str(tmp_path / "agent"),
        )

    assert runtime.closed is True
