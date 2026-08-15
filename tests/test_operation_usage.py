from __future__ import annotations

from pathlib import Path

from tests._provider_runtime import register_api_provider, reset_api_providers
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import AssistantMessage, Cost, TextContent, Usage, now_ms
from travis.coding_agent import AgentSession
from travis.coding_agent.operations import OperationRuntime, OperationStore


def setup_function() -> None:
    reset_api_providers()


def test_assistant_usage_is_numeric_idempotent_and_includes_zero_estimates(
    tmp_path: Path,
) -> None:
    calls = 0

    def script(model, _context):
        nonlocal calls
        calls += 1
        events = text_response_events(model, "done")
        message = events[-1].message
        message.usage = (
            Usage(
                input=10,
                output=4,
                cache_read=2,
                cache_write=1,
                cost=Cost(total=0.25),
            )
            if calls == 1
            else Usage(cost=Cost(total=0.0))
        )
        return events

    register_api_provider(create_faux_provider(script))
    store = OperationStore(tmp_path / "operations.sqlite3")
    runtime = OperationRuntime(
        store,
        runtime_id="a" * 32,
        pid=123,
        process_create_time=1.5,
        heartbeat_interval_seconds=None,
    )
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        operation_runtime=runtime,
        session_path=str(tmp_path / "session.jsonl"),
    )

    session.prompt("first")
    first = store.list_operations()[0]
    assert len(first.usage) == 1
    assert first.usage[0].input_tokens == 10
    assert first.usage[0].output_tokens == 4
    assert first.usage[0].cache_read_tokens == 2
    assert first.usage[0].cache_write_tokens == 1
    assert first.usage[0].cost == 0.25

    session.prompt("second")
    second = store.list_operations()[1]
    assert len(second.usage) == 1
    assert second.usage[0].input_tokens == 0
    assert second.usage[0].cost == 0

    session.dispose()
    runtime.close()


def test_duplicate_persistence_callback_reuses_usage_source_key(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    runtime = OperationRuntime(
        store,
        runtime_id="a" * 32,
        pid=123,
        process_create_time=1.5,
        heartbeat_interval_seconds=None,
    )
    session = AgentSession(
        cwd=str(tmp_path), model=faux_model(), operation_runtime=runtime
    )
    message = AssistantMessage(
        content=[TextContent(text="PRIVATE COMPLETION")],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=Usage(input=3, output=2, cost=Cost(total=0.1)),
        stop_reason="stop",
        timestamp=now_ms(),
    )

    session._operation_start_turn()
    session._operation_record_persisted_message(message)
    session._operation_record_persisted_message(message)
    session._operation_finish_turn()

    snapshot = store.list_operations()[0]
    assert len(snapshot.usage) == 1
    assert "PRIVATE COMPLETION" not in repr(snapshot)
    session.dispose()
    runtime.close()
