"""Bounded read queries for operation-journal inspection."""

from __future__ import annotations

from travis.coding_agent.operations.types import OperationSnapshot


def load_operations(
    store,
    session_fingerprint: str | None,
    limit: int | None,
) -> tuple[OperationSnapshot, ...]:
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("limit must be a positive integer")
    with store._lock:
        store._require_open()
        sql = "SELECT * FROM operations"
        parameters: tuple[object, ...] = ()
        if session_fingerprint is not None:
            sql += " WHERE session_fingerprint=?"
            parameters = (session_fingerprint,)
        if limit is None:
            sql += " ORDER BY created_at_ms, operation_id"
        else:
            sql += " ORDER BY created_at_ms DESC, operation_id DESC LIMIT ?"
            parameters = (*parameters, limit)
        rows = store._connection.execute(sql, parameters).fetchall()
        return tuple(store._snapshot_from_row(row) for row in rows)


__all__ = ["load_operations"]
