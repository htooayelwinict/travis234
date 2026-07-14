from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from travis.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, empty_usage, now_ms
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.compaction_adapter import (
    compaction_summary_with_details,
    merge_process_compaction_details,
    merge_summary_model_compaction_details,
)
from travis.coding_agent.process_context import (
    ProcessContextRecord,
    ProcessContextResolver,
    referenced_process_ids,
)
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner, ProcessSnapshot, ProcessState
from travis.coding_agent.session_types import default_convert_to_llm


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


def test_compaction_details_merge_process_ledger_without_losing_file_details() -> None:
    records = [
        ProcessContextRecord(
            f"proc_{index:032x}",
            "running",
            index,
            index * 2,
            None,
            False,
        )
        for index in range(20)
    ]

    details = merge_process_compaction_details(
        {"readFiles": ["src/a.py"], "modifiedFiles": ["src/b.py"]},
        records,
    )

    assert details["readFiles"] == ["src/a.py"]
    assert details["modifiedFiles"] == ["src/b.py"]
    assert len(details["managedProcesses"]) == 16
    assert details["managedProcesses"][0] == {
        "sessionId": "proc_00000000000000000000000000000000",
        "status": "running",
        "cursor": 0,
        "outputSize": 0,
        "exitCode": None,
        "durableOutput": False,
    }


def test_dedicated_summary_model_provenance_is_extension_metadata_not_prompt_text() -> None:
    details = merge_summary_model_compaction_details(
        {"readFiles": ["src/a.py"]},
        SimpleNamespace(
            summary_model_dedicated=True,
            summary_model_requested="openrouter/openai/gpt-5.6-luna-pro",
            summary_model_used="openrouter/xiaomi/mimo-v2.5",
            summary_model_fallback=True,
            summary_model_error="temporary route failure",
        ),
    )

    assert details["summaryModel"] == {
        "requested": "openrouter/openai/gpt-5.6-luna-pro",
        "used": "openrouter/xiaomi/mimo-v2.5",
        "fallback": True,
        "error": "temporary route failure",
    }
    rendered = compaction_summary_with_details("summary", details)
    assert "gpt-5.6-luna-pro" not in rendered
    assert "temporary route failure" not in rendered


def test_compaction_summary_renders_valid_process_metadata_once() -> None:
    process_id = "proc_" + "e" * 32
    details = {
        "managedProcesses": [
            {
                "sessionId": process_id,
                "status": "exited",
                "cursor": 12,
                "outputSize": 20,
                "exitCode": 0,
                "durableOutput": True,
            },
            {"sessionId": "invalid", "status": "running"},
        ]
    }

    rendered = compaction_summary_with_details("summary", details)
    rendered_again = compaction_summary_with_details(rendered, details)

    assert rendered.count("<managed-processes>") == 1
    assert rendered_again.count("<managed-processes>") == 1
    assert process_id in rendered
    assert "status=exited" in rendered
    assert "cursor=12" in rendered
    assert "outputSize=20" in rendered
    assert "exitCode=0" in rendered
    assert "durableOutput=true" in rendered
    assert "invalid" not in rendered


def test_provider_context_is_not_displaced_by_managed_process_state(tmp_path: Path) -> None:
    process_id = "proc_" + "d" * 32
    session_path = tmp_path / "session.jsonl"
    service = ProcessSessionService(directory=tmp_path / "processes")
    owner = ProcessOwner("app", str(tmp_path), "agent")
    seen = []

    def stream_fn(model, context, options):
        seen.append(list(context.messages))
        events = text_response_events(model, "checked")
        return create_faux_provider(lambda _model, _context: events).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        process_service=service,
        process_owner=owner,
    )
    stale = tool_result(process_id)
    session.agent.state.messages = [stale]
    session._session_store.append_message(stale)
    try:
        session.prompt("what is the build status?", stream_fn=stream_fn)

        provider_messages = seen[-1]
        provider_text = "\n".join(
            block.text
            for message in provider_messages
            for block in getattr(message, "content", [])
            if isinstance(block, TextContent)
        )
        latest_text = "\n".join(
            block.text
            for block in getattr(provider_messages[-1], "content", [])
            if isinstance(block, TextContent)
        )
        assert "<managed-process-state>" not in provider_text
        assert latest_text == "what is the build status?"
        assert not any(
            getattr(message, "customType", None) == "managed_process_state"
            for message in session.messages
        )
        persisted_entries = [
            json.loads(line)
            for line in session_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert not any(
            entry.get("customType") == "managed_process_state"
            or entry.get("message", {}).get("customType") == "managed_process_state"
            for entry in persisted_entries
        )
    finally:
        session.shutdown()
        service.close()


def test_context_transform_keeps_real_tool_result_last(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=ProcessOwner("app", str(tmp_path), "agent"),
    )
    result = tool_result("proc_" + "f" * 32, status="exited")
    try:
        transformed = asyncio.run(session._transform_context([result]))
        provider_messages = default_convert_to_llm(transformed)

        assert transformed == [result]
        assert provider_messages == [result]
        assert provider_messages[-1].role == "toolResult"
    finally:
        session.shutdown()
        service.close()


def test_process_overlay_is_absent_without_structured_references(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=ProcessOwner("app", str(tmp_path), "agent"),
    )
    try:
        transformed = asyncio.run(session._transform_context([]))
        assert transformed == []
    finally:
        session.shutdown()
        service.close()
