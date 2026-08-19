"""Read-only operation-journal inspection for the interactive TUI."""

from __future__ import annotations

import hashlib

from travis.coding_agent.operations import OperationSnapshot, OperationStore
from travis.tui.components import StatusLine, Text
from travis.tui.interactive_surfaces import InteractiveOperationsSurface

_UNKNOWN = ("Unknown operation.",)
_UNAVAILABLE = ("Operation journal is unavailable.",)
_DISPLAY_LIMIT = 100


def _session_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _timestamp(value: int | None) -> str:
    return "-" if value is None else str(value)


class OperationInspector:
    """Render bounded metadata authorized to one session identity."""

    def __init__(self, store: OperationStore | None, session_id: str | None) -> None:
        self._store = store
        self._fingerprint = (
            _session_fingerprint(session_id) if isinstance(session_id, str) else None
        )

    def summary(self) -> tuple[str, ...]:
        if self._store is None or self._fingerprint is None:
            return _UNAVAILABLE
        try:
            candidates = self._store.list_operations(
                self._fingerprint,
                limit=_DISPLAY_LIMIT + 1,
            )
        except Exception:
            return _UNAVAILABLE
        truncated = len(candidates) > _DISPLAY_LIMIT
        snapshots = candidates[:_DISPLAY_LIMIT]
        counts = {
            state: sum(item.operation.state == state for item in snapshots)
            for state in ("running", "settled", "failed", "cancelled", "uncertain")
        }
        suffix = "; showing newest 100" if truncated else ""
        header = (
            f"Operations: {len(snapshots)} "
            f"(running={counts['running']} settled={counts['settled']} "
            f"failed={counts['failed']} cancelled={counts['cancelled']} "
            f"uncertain={counts['uncertain']}{suffix})"
        )
        return (header, *(_summary_line(snapshot) for snapshot in snapshots))

    def detail(self, operation_id: str) -> tuple[str, ...]:
        if self._store is None or self._fingerprint is None:
            return _UNAVAILABLE
        try:
            snapshot = self._store.snapshot(operation_id)
        except Exception:
            return _UNKNOWN
        if (
            snapshot is None
            or snapshot.operation.session_fingerprint != self._fingerprint
        ):
            return _UNKNOWN
        operation = snapshot.operation
        effects = snapshot.effects[:_DISPLAY_LIMIT]
        effect_count = (
            str(len(snapshot.effects))
            if len(snapshot.effects) <= _DISPLAY_LIMIT
            else f"{len(effects)}/{len(snapshot.effects)}"
        )
        lines = [
            f"Operation {operation.operation_id}",
            (
                f"kind={operation.kind} state={operation.state} "
                f"pc={operation.program_counter} effects={effect_count} "
                f"usage={len(snapshot.usage)}"
            ),
            (
                f"createdAtMs={operation.created_at_ms} "
                f"updatedAtMs={operation.updated_at_ms} "
                f"settledAtMs={_timestamp(operation.settled_at_ms)}"
            ),
        ]
        lines.extend(
            (
                f"Effect {effect.effect_id} ordinal={effect.ordinal} "
                f"kind={effect.kind} name={effect.name} state={effect.state} "
                f"replay={effect.replay_policy} createdAtMs={effect.created_at_ms} "
                f"settledAtMs={_timestamp(effect.settled_at_ms)}"
            )
            for effect in effects
        )
        return tuple(lines)


def _summary_line(snapshot: OperationSnapshot) -> str:
    operation = snapshot.operation
    return (
        f"{operation.operation_id} kind={operation.kind} state={operation.state} "
        f"pc={operation.program_counter} effects={len(snapshot.effects)} "
        f"createdAtMs={operation.created_at_ms} "
        f"updatedAtMs={operation.updated_at_ms} "
        f"settledAtMs={_timestamp(operation.settled_at_ms)}"
    )


class InteractiveOperations(InteractiveOperationsSurface):
    """TUI mixin that exposes operation metadata without mutations."""

    __slots__ = ()

    def _run_operations_command(self, operation_id: str | None) -> None:
        runtime = getattr(self.app.session, "operation_runtime", None)
        inspector = OperationInspector(
            getattr(runtime, "store", None),
            getattr(self.app.session, "session_id", None),
        )
        lines = inspector.detail(operation_id) if operation_id else inspector.summary()
        self.history.add(StatusLine("Operation journal", kind="session"))
        for line in lines:
            self.history.add(Text(line))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()


__all__ = ["InteractiveOperations", "OperationInspector"]
