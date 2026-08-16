"""Classify abandoned operation intents without replaying their effects."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import psutil

from travis.coding_agent.operations.store import OperationStore
from travis.coding_agent.operations.types import RuntimeLease


_HEARTBEAT_LEASE_MS = 60_000
ProcessLookup = Callable[[int], object]


@dataclass(frozen=True)
class RecoveryReport:
    inspected_runtime_count: int = 0
    live_runtime_count: int = 0
    stale_runtime_count: int = 0
    uncertain_effect_count: int = 0
    uncertain_operation_count: int = 0
    unavailable: bool = False

    @property
    def has_diagnostic(self) -> bool:
        return self.unavailable or self.uncertain_effect_count > 0

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "operation_recovery",
            "inspectedRuntimeCount": self.inspected_runtime_count,
            "liveRuntimeCount": self.live_runtime_count,
            "staleRuntimeCount": self.stale_runtime_count,
            "uncertainEffectCount": self.uncertain_effect_count,
            "uncertainOperationCount": self.uncertain_operation_count,
            "unavailable": self.unavailable,
        }


class OperationRecovery:
    @staticmethod
    def inspect(
        store: OperationStore,
        *,
        now_ms: int | None = None,
        process_lookup: ProcessLookup = psutil.Process,
        exclude_runtime_id: str | None = None,
    ) -> RecoveryReport:
        observed_at = int(time.time() * 1000) if now_ms is None else now_ms
        try:
            leases = tuple(
                lease
                for lease in store.recovery_leases()
                if lease.runtime_id != exclude_runtime_id
            )
            stale: list[str] = []
            live = 0
            for lease in leases:
                if _runtime_is_live(
                    lease,
                    observed_at,
                    process_lookup=process_lookup,
                ):
                    live += 1
                else:
                    stale.append(lease.runtime_id)
            counts = store.mark_runtimes_uncertain(tuple(stale), observed_at)
            return RecoveryReport(
                inspected_runtime_count=len(leases),
                live_runtime_count=live,
                stale_runtime_count=len(stale),
                uncertain_effect_count=counts["effects"],
                uncertain_operation_count=counts["operations"],
            )
        except Exception:
            return RecoveryReport(unavailable=True)


def _runtime_is_live(
    lease: RuntimeLease,
    now_ms: int,
    *,
    process_lookup: ProcessLookup,
) -> bool:
    if lease.closed_at_ms is not None:
        return False
    try:
        process = process_lookup(lease.pid)
        create_time = float(process.create_time())
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError, ValueError):
        return now_ms - lease.heartbeat_at_ms <= _HEARTBEAT_LEASE_MS
    return abs(create_time - lease.process_create_time) <= 0.01


__all__ = ["OperationRecovery", "RecoveryReport"]
