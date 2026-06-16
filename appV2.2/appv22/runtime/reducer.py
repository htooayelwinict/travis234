from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol


class StateEventHandler(Protocol):
    event_type: str

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class FieldAssignmentHandler:
    event_type: str
    field_name: str

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        setattr(state, self.field_name, payload)


@dataclass(frozen=True)
class MappingUpsertHandler:
    event_type: str
    field_name: str
    key_name: str

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        getattr(state, self.field_name)[payload[self.key_name]] = payload


class ModeChangedHandler:
    event_type = "ModeChanged"

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        state.mode = payload["mode"]


class WorldRefAddedHandler:
    event_type = "WorldRefAdded"

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        ref_id = payload["ref_id"]
        state.world_refs[ref_id] = payload
        summary = state.context_summary
        evidence_refs = summary.setdefault("evidence_refs", [])
        if ref_id not in evidence_refs:
            evidence_refs.append(ref_id)
        progress = summary.setdefault("progress", [])
        kind = payload.get("kind")
        ref_summary = payload.get("summary")
        if isinstance(kind, str) and isinstance(ref_summary, str):
            progress_item = f"{ref_id} ({kind}): {ref_summary}"
            if progress_item not in progress:
                progress.append(progress_item)


class RunCompletedHandler:
    event_type = "RunCompleted"

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        state.terminal = True
        state.mode = "FINALIZE"
        state.result = payload


class RunFailedHandler:
    event_type = "RunFailed"

    def apply(self, state: Any, payload: dict[str, Any]) -> None:
        state.terminal = True
        state.mode = "FAILED"
        state.result = payload


class ReducerRegistry:
    def __init__(self, handlers: list[StateEventHandler] | tuple[StateEventHandler, ...]) -> None:
        self._handlers = {handler.event_type: handler for handler in handlers}

    def has_handler(self, event_type: str) -> bool:
        return event_type in self._handlers

    def register(self, handler: StateEventHandler) -> None:
        self._handlers[handler.event_type] = handler

    def apply(self, state: Any, event: Any) -> None:
        handler = self._handlers.get(event.event_type)
        if handler is None:
            return
        handler.apply(state, deepcopy(event.payload))


DEFAULT_REDUCER = ReducerRegistry(
    (
        ModeChangedHandler(),
        WorldRefAddedHandler(),
        MappingUpsertHandler("ToolCallCompleted", "tool_results", "tool_result_id"),
        MappingUpsertHandler("ToolCallDenied", "tool_results", "tool_result_id"),
        FieldAssignmentHandler("PlanAccepted", "runtime_plan"),
        MappingUpsertHandler("MutationLeaseIssued", "mutation_leases", "lease_id"),
        MappingUpsertHandler("MutationApplied", "mutation_receipts", "receipt_id"),
        MappingUpsertHandler("VerificationRecorded", "verification_receipts", "verification_id"),
        FieldAssignmentHandler("ContextSummaryUpdated", "context_summary"),
        RunCompletedHandler(),
        RunFailedHandler(),
    )
)


def apply_event(state: Any, event: Any) -> None:
    DEFAULT_REDUCER.apply(state, event)
