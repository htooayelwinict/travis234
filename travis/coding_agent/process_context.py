"""Structured managed-process reconciliation for provider and compaction context."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from travis.agent.types import AgentMessage
from travis.ai.types import AssistantMessage, ToolCall
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner
from travis.coding_agent.session_store import CustomMessage
from travis.ai.types import now_ms

_PROCESS_ID = re.compile(r"^proc_[0-9a-f]{32}$")
_ACTIVE_STATUSES = frozenset({"starting", "running", "stopping", "draining"})
_TERMINAL_STATUSES = frozenset({"exited", "timed_out", "terminated", "failed"})
_CONTEXT_STATUSES = _ACTIVE_STATUSES | _TERMINAL_STATUSES | {"unavailable"}


@dataclass(frozen=True)
class ProcessReference:
    session_id: str
    status: str | None
    cursor: int
    output_size: int
    exit_code: int | None
    durable_output: bool
    position: int


@dataclass(frozen=True)
class ProcessContextRecord:
    session_id: str
    status: str
    cursor: int
    output_size: int
    exit_code: int | None
    durable_output: bool
    reason: str | None = None

    def as_compaction_details(self) -> dict[str, object]:
        return {
            "sessionId": self.session_id,
            "status": self.status,
            "cursor": self.cursor,
            "outputSize": self.output_size,
            "exitCode": self.exit_code,
            "durableOutput": self.durable_output,
        }


def referenced_process_ids(messages: Sequence[AgentMessage]) -> tuple[ProcessReference, ...]:
    ordered: dict[str, ProcessReference] = {}
    for position, message in enumerate(messages):
        details = getattr(message, "details", None)
        if isinstance(details, Mapping):
            _collect_detail_reference(details, ordered, position)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolCall) and block.name == "process":
                    _collect_process_argument(block.arguments, ordered, position)
        if getattr(message, "role", None) == "compactionSummary" and isinstance(details, Mapping):
            managed = details.get("managedProcesses")
            if isinstance(managed, list):
                for item in managed:
                    if isinstance(item, Mapping):
                        _collect_detail_reference(item, ordered, position)
    candidates = sorted(
        ordered.values(),
        key=lambda item: (
            0 if item.status in _ACTIVE_STATUSES else 1,
            -item.position,
        ),
    )
    return tuple(candidates[:64])


class ProcessContextResolver:
    def __init__(self, service: ProcessSessionService, owner: ProcessOwner) -> None:
        self._service = service
        self._owner = owner

    def resolve(self, messages: Sequence[AgentMessage]) -> tuple[ProcessContextRecord, ...]:
        references = referenced_process_ids(messages)
        if not references:
            return ()
        snapshots = self._service.inspect_many(
            self._owner,
            [reference.session_id for reference in references],
        )
        records: list[ProcessContextRecord] = []
        for reference, snapshot in zip(references, snapshots):
            if snapshot is not None:
                records.append(
                    ProcessContextRecord(
                        session_id=snapshot.session_id,
                        status=snapshot.state.value,
                        cursor=snapshot.next_cursor,
                        output_size=snapshot.output_size,
                        exit_code=snapshot.exit_code,
                        durable_output=snapshot.durable_output,
                    )
                )
            elif reference.status in _TERMINAL_STATUSES:
                records.append(
                    ProcessContextRecord(
                        reference.session_id,
                        reference.status,
                        reference.cursor,
                        reference.output_size,
                        reference.exit_code,
                        reference.durable_output,
                    )
                )
            else:
                records.append(
                    ProcessContextRecord(
                        reference.session_id,
                        "unavailable",
                        reference.cursor,
                        reference.output_size,
                        reference.exit_code,
                        False,
                        "application-restarted",
                    )
                )
        records.sort(
            key=lambda record: (
                0
                if record.status in _ACTIVE_STATUSES
                else 1
                if record.durable_output and record.status in _TERMINAL_STATUSES
                else 2
                if record.status in _TERMINAL_STATUSES
                else 3,
            )
        )
        return tuple(records[:16])

    def overlay(self, messages: Sequence[AgentMessage]) -> CustomMessage | None:
        return process_context_message(self.resolve(messages))


def process_context_message(
    records: Sequence[ProcessContextRecord],
) -> CustomMessage | None:
    if not records:
        return None
    lines = ["<managed-process-state>"]
    for record in records[:16]:
        fields = [
            record.session_id,
            f"status={record.status}",
            f"cursor={record.cursor}",
            f"outputSize={record.output_size}",
        ]
        if record.exit_code is not None:
            fields.append(f"exitCode={record.exit_code}")
        if record.durable_output:
            fields.append("durableOutput=true")
        if record.reason:
            fields.append(f"reason={record.reason}")
        lines.append(" ".join(fields))
    lines.append("</managed-process-state>")
    return CustomMessage(
        custom_type="managed_process_state",
        content="\n".join(lines),
        display=False,
        details=None,
        timestamp=now_ms(),
    )


def _collect_detail_reference(
    details: Mapping[str, object],
    ordered: dict[str, ProcessReference],
    position: int,
) -> None:
    session_id = details.get("sessionId")
    if not isinstance(session_id, str) or _PROCESS_ID.fullmatch(session_id) is None:
        return
    previous = ordered.get(session_id)
    status_value = details.get("status")
    status = status_value if isinstance(status_value, str) and status_value in _CONTEXT_STATUSES else None
    cursor = _nonnegative_int(details.get("nextCursor", details.get("cursor")))
    output_size = _nonnegative_int(details.get("outputSize"))
    ordered[session_id] = ProcessReference(
        session_id=session_id,
        status=status if status is not None else previous.status if previous else None,
        cursor=cursor if cursor is not None else previous.cursor if previous else 0,
        output_size=output_size if output_size is not None else previous.output_size if previous else 0,
        exit_code=details.get("exitCode") if isinstance(details.get("exitCode"), int) else previous.exit_code if previous else None,
        durable_output=bool(details.get("durableOutput", previous.durable_output if previous else False)),
        position=position,
    )


def _collect_process_argument(
    arguments: object,
    ordered: dict[str, ProcessReference],
    position: int,
) -> None:
    if not isinstance(arguments, Mapping):
        return
    session_id = arguments.get("session_id")
    if not isinstance(session_id, str) or _PROCESS_ID.fullmatch(session_id) is None:
        return
    previous = ordered.get(session_id)
    ordered[session_id] = ProcessReference(
        session_id=session_id,
        status=previous.status if previous else None,
        cursor=previous.cursor if previous else 0,
        output_size=previous.output_size if previous else 0,
        exit_code=previous.exit_code if previous else None,
        durable_output=previous.durable_output if previous else False,
        position=position,
    )


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


__all__ = [
    "ProcessContextRecord",
    "ProcessContextResolver",
    "ProcessReference",
    "process_context_message",
    "referenced_process_ids",
]
