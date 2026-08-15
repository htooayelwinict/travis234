"""Fail-open session coordination for the observe-only operation journal."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import psutil

from travis.coding_agent.operations.store import OperationStore


Clock = Callable[[], int]
DiagnosticSink = Callable[[object], None]
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


def _session_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _settlement_state(outcome_code: str) -> str:
    if outcome_code == "ok":
        return "settled"
    if outcome_code == "cancelled":
        return "cancelled"
    return "failed"


@dataclass(frozen=True)
class EffectHandle:
    operation_id: str
    effect_id: str


@dataclass(frozen=True)
class OperationJournalDiagnostic:
    kind: str
    code: str = "journal_unavailable"
    type: str = "operation_journal_diagnostic"

    def __post_init__(self) -> None:
        if _SAFE_CODE.fullmatch(self.kind) is None:
            object.__setattr__(self, "kind", "session")

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "kind": self.kind, "code": self.code}


class OperationCoordinatorOrderError(RuntimeError):
    """A caller attempted an operation boundary in an impossible order."""


class NullOperationCoordinator:
    """Shape-compatible no-op coordinator for disabled or degraded journaling."""

    enabled = False

    def __init__(
        self,
        reason_code: str = "disabled",
        *,
        diagnostic_sink: DiagnosticSink | None = None,
    ) -> None:
        self.reason_code = reason_code
        if reason_code != "disabled" and diagnostic_sink is not None:
            diagnostic_sink(OperationJournalDiagnostic("session"))

    def start(self, kind: str, session_id: str | None = None) -> None:
        del kind, session_id
        return None

    def advance(self, phase: str, registers: Mapping[str, object] | None = None) -> int:
        del phase, registers
        return 0

    def begin_effect(
        self, kind: str, name: str, fingerprint: str
    ) -> None:
        del kind, name, fingerprint
        return None

    def settle_effect(
        self, handle: EffectHandle | None, outcome_code: str
    ) -> bool:
        del handle, outcome_code
        return False

    def record_usage(
        self,
        source_key: str,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost: float,
    ) -> bool:
        del (
            source_key,
            provider,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            cost,
        )
        return False

    def complete(self, outcome_code: str) -> bool:
        del outcome_code
        return False

    def disable(self, reason_code: str) -> None:
        del reason_code

    def close(self) -> None:
        return None


class OperationCoordinator:
    """Owns journal handles for one session without owning its execution."""

    def __init__(
        self,
        store: OperationStore,
        runtime_id: str,
        session_id: str | None,
        *,
        clock_ms: Clock = _wall_clock_ms,
        diagnostic_sink: DiagnosticSink | None = None,
    ) -> None:
        self._store = store
        self._runtime_id = runtime_id
        self._session_seed = session_id or uuid.uuid4().hex
        self._clock_ms = clock_ms
        self._diagnostic_sink = diagnostic_sink
        self._lock = threading.RLock()
        self._operation_id: str | None = None
        self._operation_kind: str | None = None
        self._effects: dict[str, EffectHandle] = {}
        self._enabled = True
        self._closed = False
        self._diagnostic_emitted = False

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._closed

    @property
    def operation_id(self) -> str | None:
        return self._operation_id

    def start(self, kind: str, session_id: str | None = None) -> str | None:
        with self._lock:
            if not self.enabled:
                return None
            if self._operation_id is not None:
                raise OperationCoordinatorOrderError("operation_already_started")
            if session_id is not None and session_id != self._session_seed:
                raise OperationCoordinatorOrderError("session_identity_mismatch")
            self._operation_kind = kind
            try:
                operation = self._store.create_operation(
                    self._runtime_id,
                    _session_fingerprint(self._session_seed),
                    kind,
                    self._clock_ms(),
                )
            except Exception:
                self.disable("journal_unavailable")
                return None
            self._operation_id = operation.operation_id
            return operation.operation_id

    def advance(
        self, phase: str, registers: Mapping[str, object] | None = None
    ) -> int:
        with self._lock:
            operation_id = self._require_operation()
            if operation_id is None:
                return 0
            try:
                operation = self._store.advance(
                    operation_id, phase, registers, self._clock_ms()
                )
            except Exception:
                self.disable("journal_unavailable")
                return 0
            return operation.program_counter

    def begin_effect(
        self, kind: str, name: str, fingerprint: str
    ) -> EffectHandle | None:
        with self._lock:
            operation_id = self._require_operation()
            if operation_id is None:
                return None
            try:
                effect = self._store.begin_effect(
                    operation_id, kind, name, fingerprint, self._clock_ms()
                )
            except Exception:
                self.disable("journal_unavailable")
                return None
            handle = EffectHandle(operation_id, effect.effect_id)
            self._effects[effect.effect_id] = handle
            return handle

    def settle_effect(
        self, handle: EffectHandle | None, outcome_code: str
    ) -> bool:
        with self._lock:
            operation_id = self._require_operation()
            if operation_id is None:
                return False
            if (
                handle is None
                or handle.operation_id != operation_id
                or handle.effect_id not in self._effects
            ):
                raise OperationCoordinatorOrderError("effect_handle_unknown")
            try:
                self._store.settle_effect(
                    handle.effect_id,
                    _settlement_state(outcome_code),
                    outcome_code,
                    self._clock_ms(),
                )
            except Exception:
                self.disable("journal_unavailable")
                return False
            self._effects.pop(handle.effect_id, None)
            return True

    def record_usage(
        self,
        source_key: str,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost: float,
    ) -> bool:
        with self._lock:
            operation_id = self._require_operation()
            if operation_id is None:
                return False
            try:
                self._store.record_usage(
                    operation_id,
                    source_key,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    cost,
                    self._clock_ms(),
                )
            except Exception:
                self.disable("journal_unavailable")
                return False
            return True

    def complete(self, outcome_code: str) -> bool:
        with self._lock:
            operation_id = self._require_operation()
            if operation_id is None:
                return False
            state = _settlement_state(outcome_code)
            try:
                for handle in tuple(self._effects.values()):
                    self._store.settle_effect(
                        handle.effect_id,
                        "cancelled" if state == "cancelled" else "failed",
                        "cancelled" if state == "cancelled" else "operation_ended",
                        self._clock_ms(),
                    )
                self._store.settle_operation(
                    operation_id, state, outcome_code, self._clock_ms()
                )
            except Exception:
                self.disable("journal_unavailable")
                return False
            self._effects.clear()
            self._operation_id = None
            self._operation_kind = None
            return True

    def disable(self, reason_code: str) -> None:
        del reason_code
        with self._lock:
            if not self._enabled:
                return
            self._enabled = False
            if self._diagnostic_emitted:
                return
            self._diagnostic_emitted = True
            if self._diagnostic_sink is not None:
                try:
                    self._diagnostic_sink(
                        OperationJournalDiagnostic(self._operation_kind or "session")
                    )
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._enabled and self._operation_id is not None:
                self.complete("cancelled")
            self._closed = True

    def _require_operation(self) -> str | None:
        if not self.enabled:
            return None
        if self._operation_id is None:
            raise OperationCoordinatorOrderError("operation_not_started")
        return self._operation_id


class OperationRuntime:
    """Process-scoped lease and coordinator factory for one journal store."""

    def __init__(
        self,
        store: OperationStore | None,
        *,
        runtime_id: str | None = None,
        pid: int | None = None,
        process_create_time: float | None = None,
        clock_ms: Clock = _wall_clock_ms,
        heartbeat_interval_seconds: float | None = 20.0,
        path: Path | None = None,
        unavailable_reason: str | None = None,
    ) -> None:
        self._store = store
        self.runtime_id = runtime_id or uuid.uuid4().hex
        self._pid = pid or os.getpid()
        self._process_create_time = (
            float(process_create_time)
            if process_create_time is not None
            else float(psutil.Process(self._pid).create_time())
        )
        self._clock_ms = clock_ms
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._coordinators: list[OperationCoordinator] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._closed = False
        self._unavailable_reason = unavailable_reason
        self.path = path or (store.path if store is not None else None)
        if store is None:
            return
        store.open_runtime(
            self.runtime_id,
            self._pid,
            self._process_create_time,
            self._clock_ms(),
        )
        if heartbeat_interval_seconds is not None:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="travis-operation-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    @classmethod
    def from_settings(
        cls,
        agent_dir: str | Path,
        settings: Mapping[str, object],
        *,
        heartbeat_interval_seconds: float | None = 20.0,
    ) -> OperationRuntime:
        path = Path(agent_dir).expanduser().resolve() / "operations.sqlite3"
        if settings.get("mode") == "disabled":
            return cls(
                None,
                path=path,
                unavailable_reason="disabled",
                heartbeat_interval_seconds=None,
            )
        store: OperationStore | None = None
        try:
            store = OperationStore(path, max_bytes=int(settings["maxBytes"]))
            return cls(
                store,
                path=path,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
        except Exception:
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass
            return cls(
                None,
                path=path,
                unavailable_reason="journal_unavailable",
                heartbeat_interval_seconds=None,
            )

    @property
    def store(self) -> OperationStore | None:
        return self._store

    @property
    def heartbeat_thread_alive(self) -> bool:
        return bool(self._heartbeat_thread and self._heartbeat_thread.is_alive())

    def for_session(
        self,
        session_id: str | None,
        *,
        diagnostic_sink: DiagnosticSink | None = None,
    ) -> OperationCoordinator | NullOperationCoordinator:
        with self._lock:
            if (
                self._closed
                or self._store is None
                or self._unavailable_reason is not None
            ):
                return NullOperationCoordinator(
                    self._unavailable_reason or "journal_unavailable",
                    diagnostic_sink=diagnostic_sink,
                )
            coordinator = OperationCoordinator(
                self._store,
                self.runtime_id,
                session_id,
                clock_ms=self._clock_ms,
                diagnostic_sink=diagnostic_sink,
            )
            self._coordinators.append(coordinator)
            return coordinator

    def _heartbeat_loop(self) -> None:
        assert self._heartbeat_interval_seconds is not None
        while not self._stop.wait(self._heartbeat_interval_seconds):
            try:
                assert self._store is not None
                self._store.heartbeat_runtime(self.runtime_id, self._clock_ms())
            except Exception:
                with self._lock:
                    self._unavailable_reason = "journal_unavailable"
                    coordinators = tuple(self._coordinators)
                for coordinator in coordinators:
                    coordinator.disable("journal_unavailable")
                return

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            coordinators = tuple(self._coordinators)
            self._coordinators.clear()
            store = self._store
        for coordinator in coordinators:
            coordinator.close()
        if store is not None:
            try:
                store.close_runtime(self.runtime_id, self._clock_ms())
            except Exception:
                pass
            try:
                store.close()
            except Exception:
                pass


__all__ = [
    "EffectHandle",
    "NullOperationCoordinator",
    "OperationCoordinator",
    "OperationCoordinatorOrderError",
    "OperationJournalDiagnostic",
    "OperationRuntime",
]
