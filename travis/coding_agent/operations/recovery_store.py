"""Focused recovery queries over an operation-store owner."""

from __future__ import annotations

from travis.coding_agent.operations.rows import runtime_from_row
from travis.coding_agent.operations.types import RuntimeLease


def load_recovery_leases(store) -> tuple[RuntimeLease, ...]:
    with store._lock:
        store._require_open()
        rows = store._connection.execute(
            """SELECT DISTINCT runtime_leases.*
               FROM runtime_leases
               JOIN effects ON effects.runtime_id = runtime_leases.runtime_id
               WHERE effects.state = 'intent'
               ORDER BY runtime_leases.started_at_ms, runtime_leases.runtime_id"""
        ).fetchall()
        return tuple(runtime_from_row(row) for row in rows)


def claim_uncertain_runtimes(
    store, runtime_ids: tuple[str, ...], now_ms: int
) -> dict[str, int]:
    ids = tuple(dict.fromkeys(runtime_ids))
    if not ids:
        return {"effects": 0, "operations": 0}
    placeholders = ",".join("?" for _ in ids)
    with store._transaction() as connection:
        effects = connection.execute(
            f"""UPDATE effects
                SET state='uncertain', settled_at_ms=?, outcome_code='runtime_lost'
                WHERE state='intent' AND runtime_id IN ({placeholders})""",
            (now_ms, *ids),
        ).rowcount
        operations = connection.execute(
            f"""UPDATE operations
                SET state='uncertain', updated_at_ms=?, settled_at_ms=?,
                    outcome_code='runtime_lost'
                WHERE state='running' AND runtime_id IN ({placeholders})
                  AND EXISTS (
                      SELECT 1 FROM effects
                      WHERE effects.operation_id = operations.operation_id
                        AND effects.state='uncertain'
                  )""",
            (now_ms, now_ms, *ids),
        ).rowcount
        return {"effects": effects, "operations": operations}


__all__ = ["claim_uncertain_runtimes", "load_recovery_leases"]
