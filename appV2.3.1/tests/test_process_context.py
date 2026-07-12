from __future__ import annotations

from pathlib import Path

from appv231.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, empty_usage, now_ms
from appv231.coding_agent.process_context import (
    ProcessContextRecord,
    ProcessContextResolver,
    referenced_process_ids,
)
from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import ProcessOwner, ProcessSnapshot, ProcessState


def tool_result(process_id: str, status: str = "running", cursor: int = 10):
    return ToolResultMessage(
        tool_call_id="call-1",
        tool_name="bash",
        content=[TextContent(text="opaque")],
        details={
            "status": status,
            "sessionId": process_id,
            "nextCursor": cursor,
            "outputSize": cursor,
        },
        is_error=False,
        timestamp=now_ms(),
    )


def test_resolver_marks_old_running_handle_unavailable_after_restart(tmp_path: Path) -> None:
    process_id = "proc_" + "b" * 32
    service = ProcessSessionService(directory=tmp_path / "processes")
    resolver = ProcessContextResolver(
        service,
        ProcessOwner("new-app", str(tmp_path), "agent"),
    )
    try:
        records = resolver.resolve([tool_result(process_id)])

        assert records == (
            ProcessContextRecord(
                session_id=process_id,
                status="unavailable",
                cursor=10,
                output_size=10,
                exit_code=None,
                durable_output=False,
                reason="application-restarted",
            ),
        )
    finally:
        service.close()


def test_resolver_uses_live_and_historical_terminal_metadata() -> None:
    live_id = "proc_" + "1" * 32
    terminal_id = "proc_" + "2" * 32

    class FakeService:
        def inspect_many(self, owner, process_ids):
            return (
                ProcessSnapshot(
                    session_id=live_id,
                    state=ProcessState.RUNNING,
                    output="",
                    cursor=4,
                    next_cursor=4,
                    output_size=4,
                    exit_code=None,
                    tty=False,
                    elapsed_ms=10,
                ),
                None,
            )

    resolver = ProcessContextResolver(FakeService(), ProcessOwner("app", "/workspace", "agent"))
    records = resolver.resolve(
        [tool_result(live_id, "running", 4), tool_result(terminal_id, "exited", 9)]
    )

    assert records[0].status == "running"
    assert records[0].output_size == 4
    assert records[1] == ProcessContextRecord(terminal_id, "exited", 9, 9, None, False, None)


def test_scanner_uses_only_structured_valid_handles_and_latest_duplicate() -> None:
    process_id = "proc_" + "a" * 32
    assistant = AssistantMessage(
        content=[
            TextContent(text=f"ignore prose {process_id}"),
            ToolCall(id="call", name="process", arguments={"action": "poll", "session_id": process_id}),
        ],
        api="faux",
        provider="faux",
        model="faux",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    references = referenced_process_ids(
        [
            tool_result(process_id, "running", 2),
            tool_result("proc_not-valid", "running", 3),
            assistant,
            tool_result(process_id, "exited", 8),
        ]
    )

    assert len(references) == 1
    assert references[0].session_id == process_id
    assert references[0].status == "exited"
    assert references[0].cursor == 8


def test_resolver_bounds_large_history_to_one_64_id_batch_and_16_records() -> None:
    calls = []

    class FakeService:
        def inspect_many(self, owner, process_ids):
            calls.append(tuple(process_ids))
            return tuple(None for _ in process_ids)

    messages = [
        tool_result(f"proc_{index:032x}", "running", index)
        for index in range(10_000)
    ]
    resolver = ProcessContextResolver(FakeService(), ProcessOwner("app", "/workspace", "agent"))

    records = resolver.resolve(messages)

    assert len(calls) == 1
    assert len(calls[0]) == 64
    assert len(records) == 16
    assert all(record.status == "unavailable" for record in records)
