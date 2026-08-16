from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tests._provider_runtime import register_api_provider, reset_api_providers
from travis.agent.types import AgentToolResult
from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from travis.ai.types import (
    AssistantMessage,
    Context,
    ErrorEvent,
    TextContent,
    SimpleStreamOptions,
    Tool,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from travis.ai.event_stream import create_assistant_message_event_stream
from travis.coding_agent import AgentSession, SettingsManager
from travis.coding_agent.operations import OperationRuntime, OperationStore
from travis.coding_agent.subagents import SubagentTask
from travis.coding_agent.tools.types import ToolDefinition


def setup_function() -> None:
    reset_api_providers()


def _runtime(tmp_path: Path) -> tuple[OperationRuntime, OperationStore]:
    store = OperationStore(tmp_path / "operations.sqlite3")
    runtime = OperationRuntime(
        store,
        runtime_id="a" * 32,
        pid=123,
        process_create_time=1.5,
        heartbeat_interval_seconds=None,
    )
    return runtime, store


def _session(
    tmp_path: Path,
    runtime: OperationRuntime,
    *,
    settings: SettingsManager | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
    stream_fn=None,
    operation_role: str | None = None,
    operation_task_id: str | None = None,
    retry_enabled: bool = False,
    max_retries: int = 0,
) -> AgentSession:
    return AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        settings_manager=settings,
        tool_definitions=tool_definitions,
        stream_fn=stream_fn,
        operation_runtime=runtime,
        operation_role=operation_role,
        operation_task_id=operation_task_id,
        retry_enabled=retry_enabled,
        max_retries=max_retries,
        session_path=str(tmp_path / "session.jsonl"),
    )


def _error_message(model, *, stop_reason: str = "error") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="PRIVATE PROVIDER ERROR")],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason=stop_reason,  # type: ignore[arg-type]
        error_message="PRIVATE PROVIDER ERROR",
        timestamp=now_ms(),
    )


def test_successful_provider_turn_records_phases_without_content(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    register_api_provider(
        create_faux_provider(
            lambda model, _context: text_response_events(
                model, "PRIVATE COMPLETION CONTENT"
            )
        )
    )
    session = _session(
        tmp_path,
        runtime,
        operation_role="reviewer",
        operation_task_id="PRIVATE_CHILD_TASK_ID",
    )

    session.prompt("PRIVATE PROMPT CONTENT")

    snapshots = store.list_operations()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.operation.state == "settled"
    assert snapshot.operation.program_counter >= 5
    assert next(item for item in snapshot.registers if item.key == "phase").as_dict()[
        "value"
    ] == "turn_settled"
    assert [(effect.kind, effect.state) for effect in snapshot.effects] == [
        ("provider", "settled")
    ]
    assert next(item for item in snapshot.registers if item.key == "role").as_dict()[
        "value"
    ] == "reviewer"
    assert "PRIVATE_CHILD_TASK_ID" not in repr(snapshot.registers)

    connection = sqlite3.connect(tmp_path / "operations.sqlite3")
    dump = "\n".join(connection.iterdump())
    connection.close()
    assert "PRIVATE PROMPT CONTENT" not in dump
    assert "PRIVATE COMPLETION CONTENT" not in dump
    assert "PRIVATE_CHILD_TASK_ID" not in dump
    session.dispose()
    runtime.close()


@pytest.mark.parametrize(
    ("reason", "effect_state", "operation_state"),
    [("error", "failed", "failed"), ("aborted", "cancelled", "cancelled")],
)
def test_provider_error_and_cancellation_settle_without_raw_error(
    tmp_path: Path, reason: str, effect_state: str, operation_state: str
) -> None:
    runtime, store = _runtime(tmp_path)

    def script(model, _context):
        error = _error_message(model, stop_reason=reason)
        return [ErrorEvent(reason=reason, error=error)]  # type: ignore[arg-type]

    register_api_provider(create_faux_provider(script))
    session = _session(tmp_path, runtime)

    session.prompt("trigger")

    snapshot = store.list_operations()[0]
    assert snapshot.operation.state == operation_state
    assert snapshot.effects[0].state == effect_state
    assert "PRIVATE PROVIDER ERROR" not in repr(snapshot)
    session.dispose()
    runtime.close()


def test_retry_and_tool_continuation_are_distinct_provider_effects(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    calls = 0

    def script(model, _context):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = _error_message(model)
            error.error_message = "SSE stream received no data events for 60 seconds"
            return [ErrorEvent(reason="error", error=error)]
        if calls == 2:
            return tool_call_response_events(model, "probe", {}, call_id="call-probe")
        return text_response_events(model, "done")

    def execute(*_args, **_kwargs):
        return AgentToolResult(content=[TextContent(text="tool ok")], details={})

    tool = ToolDefinition(
        name="probe",
        label="probe",
        description="",
        parameters={"type": "object", "properties": {}},
        execute=execute,
        effects=frozenset({"read"}),
    )
    settings = SettingsManager.in_memory(
        {
            "retry": {"enabled": True, "maxRetries": 1, "baseDelayMs": 0},
            "toolPolicy": {"mode": "audit", "autoAllowEffects": ["read"]},
        }
    )
    register_api_provider(create_faux_provider(script))
    session = _session(
        tmp_path,
        runtime,
        settings=settings,
        tool_definitions=[tool],
        retry_enabled=True,
        max_retries=1,
    )

    session.prompt("run")

    snapshot = store.list_operations()[0]
    assert calls == 3
    assert [effect.kind for effect in snapshot.effects] == [
        "provider",
        "provider",
        "tool",
        "provider",
    ]
    assert [effect.state for effect in snapshot.effects] == [
        "failed",
        "settled",
        "settled",
        "settled",
    ]
    assert [
        message.tool_call_id
        for message in session.messages
        if isinstance(message, ToolResultMessage)
    ] == ["call-probe"]
    session.dispose()
    runtime.close()


def test_jsonl_failure_after_provider_settlement_fails_only_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, store = _runtime(tmp_path)
    register_api_provider(
        create_faux_provider(lambda model, _context: text_response_events(model, "done"))
    )
    session = _session(tmp_path, runtime)
    original_append = session._session_store.append_message

    def fail_assistant(message) -> None:
        if isinstance(message, AssistantMessage):
            raise OSError("PRIVATE JSONL PATH")
        original_append(message)

    monkeypatch.setattr(session._session_store, "append_message", fail_assistant)

    with pytest.raises(OSError, match="PRIVATE JSONL PATH"):
        session.prompt("run")

    snapshot = store.list_operations()[0]
    assert snapshot.effects[0].state == "settled"
    assert snapshot.operation.state == "failed"
    assert "PRIVATE JSONL PATH" not in repr(snapshot)
    session.dispose()
    runtime.close()


def test_provider_effect_remains_intent_until_delayed_stream_settles(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    source = create_assistant_message_event_stream()
    session = _session(tmp_path, runtime, stream_fn=lambda *_args: source)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            session.prompt("wait")
        except BaseException as error:  # noqa: BLE001 - asserted below.
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 2
    snapshot = None
    while time.monotonic() < deadline:
        operations = store.list_operations()
        if operations and operations[0].effects:
            snapshot = operations[0]
            break
        time.sleep(0.01)

    assert snapshot is not None
    assert snapshot.effects[0].state == "intent"
    for event in text_response_events(faux_model(), "done"):
        source.push(event)
    thread.join(timeout=2)

    assert errors == []
    assert thread.is_alive() is False
    assert store.list_operations()[0].effects[0].state == "settled"
    session.dispose()
    runtime.close()


def test_direct_continue_creates_a_separate_operation(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    register_api_provider(
        create_faux_provider(lambda model, _context: text_response_events(model, "done"))
    )
    session = _session(tmp_path, runtime)

    session.prompt("first")
    session.follow_up("second")
    session.continue_()

    snapshots = store.list_operations()
    assert len(snapshots) == 2
    assert [snapshot.operation.state for snapshot in snapshots] == [
        "settled",
        "settled",
    ]
    assert [len(snapshot.effects) for snapshot in snapshots] == [1, 1]
    session.dispose()
    runtime.close()


def test_internal_typed_child_turn_is_observed_without_storing_goal(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    register_api_provider(
        create_faux_provider(
            lambda model, _context: text_response_events(model, "child done")
        )
    )
    parent = _session(tmp_path, runtime)
    task = SubagentTask(
        role="reviewer",
        goal="PRIVATE CHILD GOAL",
        cwd=str(tmp_path),
        allowed_tools=(),
    )

    result = parent._run_internal_subagent(task)

    assert result.status == "completed"
    snapshot = store.list_operations()[0]
    role = next(item for item in snapshot.registers if item.key == "role")
    assert role.as_dict()["value"] == "reviewer"
    assert "PRIVATE CHILD GOAL" not in repr(snapshot)
    assert task.id not in repr(snapshot)
    parent.dispose()
    runtime.close()


def test_provider_request_fingerprint_excludes_credentials_and_message_bodies(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    session = _session(tmp_path, runtime)
    source = create_assistant_message_event_stream()
    for event in text_response_events(faux_model(), "PRIVATE RESPONSE BODY"):
        source.push(event)
    context = Context(
        messages=[UserMessage(content="PRIVATE REQUEST BODY")],
        tools=[Tool(name="probe", description="", parameters={})],
    )
    options = SimpleStreamOptions(
        api_key="PRIVATE API KEY",
        headers={"Authorization": "PRIVATE HEADER"},
        env={"PRIVATE_ENV": "PRIVATE ENV VALUE"},
        metadata={"private": "PRIVATE METADATA"},
    )

    session._operation_start_turn()
    observed = session._operation_invoke_provider(
        lambda *_args: source,
        faux_model(),
        context,
        options,
    )
    list(observed)
    observed.result_sync()
    session._operation_finish_turn()

    connection = sqlite3.connect(tmp_path / "operations.sqlite3")
    dump = "\n".join(connection.iterdump())
    connection.close()
    for secret in (
        "PRIVATE REQUEST BODY",
        "PRIVATE RESPONSE BODY",
        "PRIVATE API KEY",
        "PRIVATE HEADER",
        "PRIVATE ENV VALUE",
        "PRIVATE METADATA",
    ):
        assert secret not in dump
    assert store.list_operations()[0].effects[0].state == "settled"
    session.dispose()
    runtime.close()
