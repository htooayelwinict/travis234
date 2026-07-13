from __future__ import annotations

import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from travis.agent.types import AgentTool, AgentToolResult
from travis.app import CodingApp
from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.types import (
    AssistantMessage,
    ErrorEvent,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from travis.ai.stream import ApiProvider, register_api_provider, reset_api_providers
from travis.coding_agent import SettingsManager
from travis.coding_agent.provider_control_plane import ProviderControlPlane
from travis.coding_agent.processes.types import ProcessClosedError, ProcessNotFoundError, ProcessState
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore
from travis.tui.terminal import FakeTerminal


def setup_function() -> None:
    reset_api_providers()


def _python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def test_coding_app_owns_managed_process_service_and_closes_it_idempotently(tmp_path: Path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), enable_tui=False)
    service = app.process_service
    bash = app.session.get_tool_definition("bash")
    assert bash is not None
    started = bash.execute(
        "managed",
        {"command": _python_command("import time; time.sleep(30)"), "yield_time_ms": 0},
    )

    assert app.session.get_tool_definition("process") is not None
    assert started.details["status"] == "running"
    assert service.list(app.process_owner())[0].state is ProcessState.RUNNING

    app.close()
    app.close()

    with pytest.raises(ProcessClosedError, match="closed"):
        service.subscribe(lambda _event: None)


def test_coding_app_reuses_process_service_across_same_workspace_resume(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    initial_path, initial_id = catalog.new_session_path(str(workspace), "initial-process")
    target_path, _target_id = catalog.new_session_path(str(workspace), "target-process")
    model = faux_model()
    _seed_restorable_app_session(Path(target_path), workspace, model)
    app = CodingApp(
        cwd=str(workspace),
        model=model,
        enable_tui=False,
        session_path=initial_path,
        session_id=initial_id,
        agent_dir=str(agent_dir),
    )
    service = app.process_service
    bash = app.session.get_tool_definition("bash")
    assert bash is not None
    started = bash.execute(
        "managed",
        {"command": _python_command("import time; time.sleep(30)"), "yield_time_ms": 0},
    )
    try:
        app.switch_session(target_path)

        assert app.process_service is service
        assert app.session.process_service is service
        resumed = service.poll(app.process_owner(), started.details["sessionId"], 0, wait_ms=0)
        assert resumed.state is ProcessState.RUNNING
    finally:
        app.close()


def test_coding_app_hides_old_workspace_process_after_cross_workspace_resume(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    initial_path, initial_id = catalog.new_session_path(str(first), "initial-process")
    target_path, _target_id = catalog.new_session_path(str(second), "target-process")
    model = faux_model()
    _seed_restorable_app_session(Path(target_path), second, model)
    app = CodingApp(
        cwd=str(first),
        model=model,
        enable_tui=False,
        session_path=initial_path,
        session_id=initial_id,
        agent_dir=str(agent_dir),
    )
    service = app.process_service
    first_owner = app.process_owner()
    bash = app.session.get_tool_definition("bash")
    assert bash is not None
    started = bash.execute(
        "managed",
        {"command": _python_command("import time; time.sleep(30)"), "yield_time_ms": 0},
    )
    try:
        app.switch_session(target_path)

        assert app.process_owner() != first_owner
        with pytest.raises(ProcessNotFoundError):
            service.poll(app.process_owner(), started.details["sessionId"], 0, wait_ms=0)
        assert service.poll(first_owner, started.details["sessionId"], 0, wait_ms=0).state is ProcessState.RUNNING
    finally:
        app.close()


def test_new_app_instance_recovers_terminal_process_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_dir = tmp_path / "agent"
    first = CodingApp(
        cwd=str(workspace),
        model=faux_model(),
        enable_tui=False,
        agent_dir=str(agent_dir),
    )
    bash = first.session.get_tool_definition("bash")
    assert bash is not None
    result = bash.execute(
        "managed",
        {"command": _python_command("print('done', end='')"), "yield_time_ms": 2_000},
    )
    process_id = result.details["sessionId"]
    first.close()

    second = CodingApp(
        cwd=str(workspace),
        model=faux_model(),
        enable_tui=False,
        agent_dir=str(agent_dir),
    )
    try:
        recovered = second.process_service.poll(second.process_owner(), process_id, 0, wait_ms=0)

        assert recovered.state is ProcessState.EXITED
        assert recovered.output == "done"
        assert recovered.durable_output is True
    finally:
        second.close()


def test_end_to_end_coding_app_read_tool_and_render(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("integration body", encoding="utf-8")
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": "notes.txt"})
        return text_response_events(m, "notes.txt contains integration body")

    register_api_provider(create_faux_provider(script))

    terminal = FakeTerminal(columns=80)
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal)
    app.run_turn("read notes.txt and summarize")

    roles = [getattr(m, "role", None) for m in app.messages]
    assert "user" in roles and "assistant" in roles and "toolResult" in roles
    rendered = "\n".join(app.tui.render(80))
    assert "read" in rendered
    assert "integration body" in rendered
    assert "integration body" in "\n".join(
        b.text for m in app.messages if getattr(m, "role", None) == "toolResult" for b in m.content
    )
    assert calls["n"] == 2


def _seed_restorable_app_session(
    path: Path,
    cwd: Path,
    model: Model,
    *,
    marker: str = "persisted marker",
) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    store = SessionStore(str(path), cwd=str(cwd.resolve()), session_id=path.stem)
    store.append_model_change(model.provider, model.id)
    store.append_thinking_level_change("medium")
    store.append_message(UserMessage(content=marker, timestamp=now_ms()))


def test_coding_app_initial_session_restores_registered_model_thinking_and_messages(tmp_path: Path) -> None:
    initial_model = faux_model()
    saved_model = replace(initial_model, id="saved-model", name="Saved Model")
    control_plane = ProviderControlPlane.in_memory()
    control_plane.ensure_model(initial_model)
    control_plane.ensure_model(saved_model)
    session_path = tmp_path / "saved.jsonl"
    _seed_restorable_app_session(session_path, tmp_path, saved_model)

    app = CodingApp(
        cwd=str(tmp_path),
        model=initial_model,
        enable_tui=False,
        session_path=str(session_path),
        provider_control_plane=control_plane,
    )

    assert app.session.model is saved_model
    assert app.session.thinking_level == "medium"
    assert [message.content for message in app.messages if isinstance(message, UserMessage)] == ["persisted marker"]
    assert app.session.provider_control_plane is control_plane


def test_coding_app_switch_session_rebinds_session_local_state_transactionally(tmp_path: Path) -> None:
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    initial_model = faux_model()
    saved_model = replace(initial_model, id="saved-model", name="Saved Model")
    control_plane = ProviderControlPlane.in_memory()
    control_plane.ensure_model(initial_model)
    control_plane.ensure_model(saved_model)
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    initial_path, initial_id = catalog.new_session_path(str(first_cwd), "initial")
    target_path, _target_id = catalog.new_session_path(str(second_cwd), "target")
    _seed_restorable_app_session(Path(target_path), second_cwd, saved_model)
    app = CodingApp(
        cwd=str(first_cwd),
        model=initial_model,
        terminal=FakeTerminal(),
        enable_tui=False,
        session_path=initial_path,
        session_id=initial_id,
        agent_dir=str(agent_dir),
        provider_control_plane=control_plane,
    )
    old_session = app.session
    old_compaction = app.compaction
    old_renderer = app.renderer
    rebound: list[object] = []
    app.subscribe_session_rebound(rebound.append)

    result = app.switch_session(target_path)

    assert result == {"cancelled": False}
    assert app.session_runtime.session is app.session
    assert app.session is not old_session
    assert app.compaction is not old_compaction
    assert app.renderer is not old_renderer
    assert app.cwd == str(second_cwd.resolve())
    assert app.session.cwd == str(second_cwd.resolve())
    assert app.session.model is saved_model
    assert app.session.thinking_level == "medium"
    assert [message.content for message in app.messages if isinstance(message, UserMessage)] == ["persisted marker"]
    assert app.session.provider_control_plane is control_plane
    assert app.session._compaction_manager is app.compaction
    assert rebound == [app.session]


def test_coding_app_failed_switch_keeps_original_session_and_subscriptions(tmp_path: Path, monkeypatch) -> None:
    model = faux_model()
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    session_path, session_id = catalog.new_session_path(str(tmp_path), "initial")
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        enable_tui=False,
        session_path=session_path,
        session_id=session_id,
        agent_dir=str(agent_dir),
    )
    old_session = app.session
    old_compaction = app.compaction
    old_unsubscribe = old_session._unsubscribe_agent
    rebound: list[object] = []
    app.subscribe_session_rebound(rebound.append)

    def fail_create(_options):
        raise RuntimeError("target initialization failed")

    monkeypatch.setattr(app.session_runtime, "_create_runtime", fail_create)

    with pytest.raises(RuntimeError, match="target initialization failed"):
        app.switch_session(str(tmp_path / "missing.jsonl"), cwd_override=str(tmp_path))

    assert app.session is old_session
    assert app.session_runtime.session is old_session
    assert app.compaction is old_compaction
    assert old_session._unsubscribe_agent is old_unsubscribe
    assert rebound == []


def test_coding_app_new_session_from_ephemeral_uses_app_owned_catalog(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        enable_tui=False,
        session_path=None,
        agent_dir=str(agent_dir),
    )
    assert app.session.session_path is None

    result = app.new_session()

    assert result == {"cancelled": False}
    assert app.session.session_path is not None
    assert Path(app.session.session_path).is_relative_to(agent_dir / "sessions")


def test_coding_app_wires_settings_retry_for_sse_idle_timeout(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=m.api,
                provider=m.provider,
                model=m.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message="SSE stream received no data events for 60 seconds",
                timestamp=now_ms(),
            )
            return [ErrorEvent(reason="error", error=error)]
        return text_response_events(m, "Recovered after retry")

    register_api_provider(create_faux_provider(script))
    settings = SettingsManager.in_memory({"retry": {"enabled": True, "maxRetries": 1, "baseDelayMs": 0}})
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        enable_tui=False,
        settings_manager=settings,
    )
    events: list[object] = []
    app.session.subscribe(events.append)

    app.run_turn("recover from transient stream timeout")

    assert calls["n"] == 2
    retry_events = [event for event in events if getattr(event, "type", "").startswith("auto_retry_")]
    assert [event.type for event in retry_events] == ["auto_retry_start", "auto_retry_end"]
    assert retry_events[0].error_message == "SSE stream received no data events for 60 seconds"
    assert retry_events[-1].success is True
    assert any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "stop"
        and any(isinstance(block, TextContent) and block.text == "Recovered after retry" for block in message.content)
        for message in app.messages
    )


def test_coding_app_model_can_spawn_visible_subagent(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    child_tool_names: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "spawn_subagent",
                {
                    "role": "reviewer",
                    "goal": "inspect docs/report/travis_qa_scan_2026-06-26.md",
                    "wait": True,
                    "timeoutSeconds": 2,
                },
            )
        if provider_calls["n"] == 2:
            child_tool_names[:] = [tool.name for tool in (c.tools or [])]
            return text_response_events(m, "child reviewed the report")
        return text_response_events(m, "parent saw child status completed")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), enable_tui=False)
    events: list[object] = []
    app.session.subscribe(events.append)

    app.run_turn("spawn a reviewer subagent and show its status")

    tool_results = [
        message
        for message in app.messages
        if isinstance(message, ToolResultMessage) and message.tool_name == "spawn_subagent"
    ]
    assert tool_results
    assert tool_results[0].details["status"] == "completed"
    assert tool_results[0].details["role"] == "reviewer"
    assert tool_results[0].details["summary"] == "child reviewed the report"
    event_types = [event["type"] if isinstance(event, dict) else event.type for event in events]
    assert "subagent_start" in event_types
    assert "subagent_stop" in event_types
    assert set(child_tool_names) == {"read", "grep", "find", "ls"}
    assert "run" not in child_tool_names
    assert provider_calls["n"] == 3


def test_coding_app_internal_subagent_result_includes_child_tool_trace(tmp_path: Path) -> None:
    (tmp_path / "child.md").write_text("child trace body", encoding="utf-8")
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "spawn_subagent",
                {
                    "role": "reviewer",
                    "goal": "read child.md and report",
                    "wait": True,
                    "timeoutSeconds": 5,
                },
            )
        if provider_calls["n"] == 2:
            return tool_call_response_events(m, "read", {"path": "child.md"})
        if provider_calls["n"] == 3:
            return text_response_events(m, "child read child.md")
        return text_response_events(m, "parent saw child trace")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), enable_tui=False)
    events: list[object] = []
    app.session.subscribe(events.append)

    app.run_turn("spawn a reviewer subagent and show its status")

    result = app.session.subagents.list_results()[0]
    result_dict = result.as_dict()
    tool_trace = result_dict["toolTrace"]
    assert tool_trace
    assert tool_trace[0]["toolName"] == "read"
    assert tool_trace[0]["status"] == "ok"
    assert "child.md" in tool_trace[0]["argsPreview"]
    assert "child trace body" in tool_trace[0]["resultPreview"]
    formatted = app.session._format_subagent_result(result)
    assert "summary: child read child.md" in formatted
    assert "toolTrace:" not in formatted
    assert "read ok" not in formatted
    event_types = [event["type"] if isinstance(event, dict) else event.type for event in events]
    assert "subagent_tool_start" in event_types
    assert "subagent_tool_end" in event_types
    assert provider_calls["n"] == 4


def test_coding_app_internal_subagent_persists_expandable_result_pack(tmp_path: Path) -> None:
    (tmp_path / "child.md").write_text("child trace body", encoding="utf-8")
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "spawn_subagent",
                {
                    "role": "reviewer",
                    "goal": "read child.md and report",
                    "wait": True,
                    "timeoutSeconds": 5,
                },
            )
        if provider_calls["n"] == 2:
            return tool_call_response_events(m, "read", {"path": "child.md"})
        if provider_calls["n"] == 3:
            return text_response_events(m, "child final response with enough detail")
        return text_response_events(m, "parent saw child result")

    register_api_provider(create_faux_provider(script))
    session_path = tmp_path / "sessions" / "parent.jsonl"
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        enable_tui=False,
        session_path=str(session_path),
        session_id="session-fixed",
    )

    app.run_turn("spawn a reviewer subagent and show its status")

    result = app.session.subagents.list_results()[0]
    assert result.raw_log_path is not None
    raw_log_path = Path(result.raw_log_path)
    assert raw_log_path.parent == session_path.parent / "subagents" / "session-fixed"
    payload = json.loads(raw_log_path.read_text())
    assert payload["taskId"] == result.task_id
    assert payload["backend"] == "internal"
    assert payload["finalResponse"] == "child final response with enough detail"
    assert payload["toolTrace"][0]["toolName"] == "read"


def test_coding_app_tui_renderer_does_not_break_internal_subagent_tool_trace(tmp_path: Path) -> None:
    (tmp_path / "child.md").write_text("child trace body", encoding="utf-8")
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "spawn_subagent",
                {
                    "role": "reviewer",
                    "goal": "read child.md and report",
                    "wait": True,
                    "timeoutSeconds": 5,
                },
            )
        if provider_calls["n"] == 2:
            return tool_call_response_events(m, "read", {"path": "child.md"})
        if provider_calls["n"] == 3:
            return text_response_events(m, "child read child.md")
        return text_response_events(m, "parent saw child trace")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), enable_tui=True)

    app.run_turn("spawn a reviewer subagent and show its status")

    result = app.session.subagents.list_results()[0]
    tool_trace = result.as_dict()["toolTrace"]
    assert tool_trace[0]["toolName"] == "read"
    assert tool_trace[0]["status"] == "ok"
    assert "child trace body" in tool_trace[0]["resultPreview"]
    assert provider_calls["n"] == 4


def test_coding_app_internal_subagent_tool_trace_records_guardrail_halt(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "spawn_subagent",
                {
                    "role": "reviewer",
                    "goal": "try reading missing.md and report the blocker",
                    "wait": True,
                    "timeoutSeconds": 5,
                },
            )
        if provider_calls["n"] in {2, 3, 4, 5}:
            return tool_call_response_events(m, "read", {"path": "missing.md"})
        return text_response_events(m, "parent saw child guardrail")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        enable_tui=False,
        tool_loop_guardrails={"blocking_enabled": True},
    )

    app.run_turn("spawn a reviewer subagent and show its status")

    result = app.session.subagents.list_results()[0]
    result_dict = result.as_dict()
    tool_trace = result_dict["toolTrace"]
    assert tool_trace
    assert tool_trace[-1]["status"] == "guardrail_halt"
    assert tool_trace[-1]["guardrailCode"] == "repeated_exact_failure_block"
    assert result_dict["guardrail"]["code"] == "repeated_exact_failure_block"
    assert result.status == "failed"
    assert any("repeated_exact_failure_block" in error for error in result.errors)
    formatted = app.session._format_subagent_result(result)
    assert "guardrail: repeated_exact_failure_block" in formatted
    assert "error: Subagent stopped by tool guardrail" in formatted
    assert "read guardrail_halt" not in formatted
    assert "toolTrace:" not in formatted


def test_coding_app_wires_compaction_transform(tmp_path: Path) -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "ok")))
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), context_length=1000)
    # transform_context is the travis preflight phase
    assert app.session.agent._transform_context is not None
    app.run_turn("hello")
    assert any(getattr(m, "role", None) == "assistant" for m in app.messages)


@pytest.mark.parametrize("context_window", [32_000, 128_000, 256_000, 400_000])
def test_coding_app_default_compaction_threshold_is_half_model_context(
    tmp_path: Path,
    context_window: int,
) -> None:
    model = faux_model()
    model.context_window = context_window
    model.max_tokens = 8_192

    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal())

    assert app.compressor.context_length == context_window
    assert app.compressor.threshold_percent == 0.5
    assert app.compressor.threshold_tokens == context_window // 2


def test_coding_app_forwards_initial_thinking_level_to_session(tmp_path: Path) -> None:
    model = faux_model()
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), thinking_level="high")

    assert app.session.thinking_level == "high"


def test_coding_app_forwards_travis234_settings_manager_to_session(tmp_path: Path) -> None:
    settings = SettingsManager.in_memory({"shellCommandPrefix": "printf app-settings;"})

    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(),
        enable_tui=False,
        settings_manager=settings,
    )

    result = app.session.execute_bash("printf user")

    assert app.session.settings_manager is settings
    assert result.output == "app-settingsuser"


def test_coding_app_runs_travis_post_response_compaction(tmp_path: Path) -> None:
    model = faux_model()

    def script(m, c):
        events = text_response_events(m, "ok")
        events[-1].message.usage.total_tokens = 200_000
        return events

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=100_000,
        summarizer=lambda prompt: "## Goal\nkeep working\n## Remaining Work\ncontinue",
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    app.run_turn("continue")
    assert app.compaction.awaiting_real_usage_after_compression is True
    assert any(
        "[CONTEXT COMPACTION — REFERENCE ONLY]" in str(message.content)
        and "END OF CONTEXT SUMMARY" in str(message.content)
        for message in app.messages
    )


def test_coding_app_emits_travis234_auto_compaction_events_and_running_state(tmp_path: Path) -> None:
    model = faux_model()

    def script(m, c):
        events = text_response_events(m, "ok")
        events[-1].message.usage.total_tokens = 200_000
        return events

    register_api_provider(create_faux_provider(script))
    app_holder: dict[str, CodingApp] = {}

    def summarizer(prompt: str) -> str:
        assert app_holder["app"].session.is_compacting is True
        return "## Goal\nkeep working\n## Remaining Work\ncontinue"

    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=100_000,
        summarizer=summarizer,
    )
    app_holder["app"] = app
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    events: list[object] = []
    app.session.subscribe(events.append)

    app.run_turn("continue")

    compaction_events = [event for event in events if event.type in {"compaction_start", "compaction_end"}]
    assert compaction_events[0].type == "compaction_start"
    assert compaction_events[0].reason == "threshold"
    assert compaction_events[1].type == "compaction_end"
    assert compaction_events[1].reason == "threshold"
    assert compaction_events[1].will_retry is False
    assert compaction_events[1].error_message is None
    assert app.session.is_compacting is False


def test_coding_app_persists_preflight_compaction_when_provider_errors(tmp_path: Path) -> None:
    model = faux_model()
    original_messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    def script(m, c):
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=m.api,
            provider=m.provider,
            model=m.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message="Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
        )
        return [ErrorEvent(reason="error", error=error)]

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\npreflight compacted",
    )
    app.session.agent.state.messages = list(original_messages)

    app.run_turn("continue after provider error")

    assert app.compaction.compressor.compression_count == 1
    assert len(app.messages) < len(original_messages)
    assert any(
        "[CONTEXT COMPACTION — REFERENCE ONLY]" in str(message.content)
        and "preflight compacted" in str(message.content)
        for message in app.messages
    )


def test_coding_app_auto_preflight_compaction_persists_travis234_session_boundary(tmp_path: Path) -> None:
    session_path = tmp_path / "auto-preflight-compaction.jsonl"
    model = faux_model()
    huge_tool_result = "auto persisted raw tool result\n" + ("x" * 80_000)
    seen_contexts = []

    def script(m, c):
        seen_contexts.append(c)
        return text_response_events(m, "ready after persisted compaction")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nauto persisted compacted",
        session_path=str(session_path),
    )
    old_messages = [
        UserMessage(content="old scan request", timestamp=now_ms()),
        AssistantMessage(
            content=[ToolCall(id="read-1", name="read", arguments={"path": "old.log"})],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="read-1",
            tool_name="read",
            content=[TextContent(text=huge_tool_result)],
            is_error=False,
            timestamp=now_ms(),
        ),
        *[
            UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
            for index in range(16)
        ],
    ]
    for message in old_messages:
        app.session._session_store.append_message(message)  # noqa: SLF001 - seed persisted branch.
    snapshot = app.session._session_store.build_context(default_thinking_level=app.session.thinking_level)  # noqa: SLF001
    app.session.agent.state.messages = snapshot.messages

    app.run_turn("continue after compact")

    reloaded = app.session._session_store.build_context(default_thinking_level=app.session.thinking_level)  # noqa: SLF001
    reloaded_text = "\n".join(
        f"{getattr(message, 'summary', '')}\n{getattr(message, 'content', '')}"
        for message in reloaded.messages
    )
    assert len(seen_contexts) == 1
    assert "auto persisted compacted" in reloaded_text
    assert huge_tool_result not in reloaded_text


def test_coding_app_spine_smoke_contract_no_network_with_fallback_compaction(tmp_path: Path) -> None:
    model = faux_model()
    huge_tool_result = "deterministic compaction payload\n" + ("x" * 60_000)
    seen_contexts = []

    def script(m, c):
        seen_contexts.append(c)
        return text_response_events(m, "spine final response")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: (_ for _ in ()).throw(RuntimeError("summary model unavailable")),
        enable_tui=False,
    )
    app.session.agent.state.messages = [
        UserMessage(content="initial spine setup", timestamp=now_ms()),
        AssistantMessage(
            content=[TextContent(text="setup acknowledged")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        ),
        UserMessage(content="old scan request", timestamp=now_ms()),
        AssistantMessage(
            content=[ToolCall(id="old-read", name="read", arguments={"path": "old.log"})],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="old-read",
            tool_name="read",
            content=[TextContent(text=huge_tool_result)],
            is_error=False,
            timestamp=now_ms(),
        ),
        *[
            UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
            for index in range(16)
        ],
    ]

    app.run_turn("finish the spine smoke request")

    assert len(seen_contexts) == 1
    assert app.compaction.compressor.compression_count == 1
    assert app.compaction.compressor._last_summary_fallback_used is True
    assert app.messages is app.session.agent.state.messages
    assert any(
        isinstance(message, AssistantMessage)
        and any(isinstance(block, TextContent) and block.text == "spine final response" for block in message.content)
        for message in app.messages
    )
    context_text = "\n".join(str(message.content) for message in seen_contexts[0].messages)
    assert "[CONTEXT COMPACTION" in context_text
    assert "Summary generation was unavailable" in context_text
    assert "finish the spine smoke request" in context_text
    assert huge_tool_result not in context_text


def test_coding_app_recovers_context_overflow_by_compacting_and_retrying(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=m.api,
                provider=m.provider,
                model=m.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message="prompt is too long for the model",
            )
            return [ErrorEvent(reason="error", error=error)]
        return text_response_events(m, "recovered")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "## Goal\nrecovered from overflow",
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    app.run_turn("continue after overflow")
    assert calls["n"] == 2
    assert not any(
        isinstance(message, AssistantMessage) and message.stop_reason == "error"
        for message in app.messages
    )
    assert any(
        isinstance(message, AssistantMessage)
        and any(isinstance(block, TextContent) and block.text == "recovered" for block in message.content)
        for message in app.messages
    )


def test_coding_app_provider_failure_resets_session_and_allows_next_turn(tmp_path: Path) -> None:
    model = faux_model()
    raw_provider_body = "provider guardrail details " + ("x" * 5000)
    bounded_error = (
        "OpenRouter authorization failed (HTTP 403) for model qwen/qwen3-coder-next. "
        "Check OPENROUTER_API_KEY, account credits, and model access. "
        "Provider message: provider guardrail details ... [truncated provider error body]"
    )
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=m.api,
                provider=m.provider,
                model=m.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message=bounded_error,
            )
            return [ErrorEvent(reason="error", error=error)]
        return text_response_events(m, "second turn ok")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(cwd=str(tmp_path), model=model, enable_tui=False)

    app.run_turn("first turn hits provider failure")

    provider_errors = [
        message
        for message in app.messages
        if isinstance(message, AssistantMessage) and message.stop_reason == "error"
    ]
    assert len(provider_errors) == 1
    assert provider_errors[0].error_message is not None
    assert "OpenRouter authorization failed" in provider_errors[0].error_message
    assert len(provider_errors[0].error_message) < 1200
    assert raw_provider_body not in provider_errors[0].error_message
    assert "x" * 500 not in provider_errors[0].error_message
    assert "ResponseNotRead" not in provider_errors[0].error_message
    assert app.session.is_streaming is False
    assert app.session.is_compacting is False
    assert app.session.retry_attempt == 0

    app.run_turn("second turn should still work")

    assert calls["n"] == 2
    assert app.session.is_streaming is False
    assert app.session.is_compacting is False
    assert app.session.retry_attempt == 0
    assert any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "stop"
        and any(isinstance(block, TextContent) and block.text == "second turn ok" for block in message.content)
        for message in app.messages
    )


def test_coding_app_provider_failure_and_followup_survive_jsonl_persistence(tmp_path: Path) -> None:
    model = faux_model()
    session_path = tmp_path / "provider-recovery.jsonl"
    export_path = tmp_path / "provider-recovery-export.jsonl"
    raw_provider_body = "provider guardrail details " + ("x" * 5000)
    bounded_error = (
        "OpenRouter authorization failed (HTTP 403) for model qwen/qwen3-coder-next. "
        "Check OPENROUTER_API_KEY, account credits, and model access. "
        "Provider message: provider guardrail details ... [truncated provider error body]"
    )
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=m.api,
                provider=m.provider,
                model=m.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message=bounded_error,
            )
            return [ErrorEvent(reason="error", error=error)]
        return text_response_events(m, "persisted follow-up ok")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(cwd=str(tmp_path), model=model, enable_tui=False, session_path=str(session_path))

    app.run_turn("first turn hits provider failure")
    app.run_turn("second turn should persist")

    persisted_text = session_path.read_text(encoding="utf-8")
    persisted_entries = [json.loads(line) for line in persisted_text.splitlines()]
    assistant_messages = [
        entry["message"]
        for entry in persisted_entries
        if entry.get("type") == "message" and entry.get("message", {}).get("role") == "assistant"
    ]
    assert any(message.get("errorMessage") == bounded_error for message in assistant_messages)
    assert any(
        block.get("type") == "text" and block.get("text") == "persisted follow-up ok"
        for message in assistant_messages
        for block in message.get("content", [])
    )
    assert len(bounded_error) < 1200
    assert raw_provider_body not in persisted_text
    assert "x" * 500 not in persisted_text
    assert "ResponseNotRead" not in persisted_text

    reloaded = CodingApp(cwd=str(tmp_path), model=model, enable_tui=False, session_path=str(session_path))
    reloaded_text = "\n".join(str(message.content) for message in reloaded.messages)
    reloaded_errors = [
        message.error_message
        for message in reloaded.messages
        if isinstance(message, AssistantMessage) and message.stop_reason == "error"
    ]
    assert reloaded_errors == [bounded_error]
    assert "persisted follow-up ok" in reloaded_text
    assert raw_provider_body not in reloaded_text
    assert "x" * 500 not in reloaded_text

    returned_path = app.session.export_to_jsonl(str(export_path))
    exported_text = Path(returned_path).read_text(encoding="utf-8")
    assert returned_path == str(export_path)
    assert bounded_error in exported_text
    assert "persisted follow-up ok" in exported_text
    assert raw_provider_body not in exported_text
    assert "x" * 500 not in exported_text
    assert "ResponseNotRead" not in exported_text


def test_coding_app_recovers_output_cap_error_by_lowering_max_tokens_without_compaction(tmp_path: Path) -> None:
    model = faux_model()
    model.max_tokens = 8192
    seen_max_tokens: list[int | None] = []

    def stream_fn(m, c, options):
        seen_max_tokens.append(options.max_tokens)
        return create_faux_provider(lambda _m, _c: text_response_events(_m, "recovered with lower output cap")).stream_simple(
            m,
            c,
            options,
        )

    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "should not compact",
    )
    app.session.agent.state.messages = [
        UserMessage(content=[TextContent(text="finish the scan")], timestamp=now_ms()),
        AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message=(
                "max_tokens: 8192 > context_window: 2000 - input_tokens: 1900 "
                "= available_tokens: 100"
            ),
            timestamp=now_ms(),
        ),
    ]

    app.run_turn("hi", stream_fn=stream_fn)

    assert seen_max_tokens == [100]
    assert model.max_tokens == 8192
    assert app.provider_control_plane.models.find(model.provider, model.id) is model
    assert app.session.model.max_tokens == 100
    assert app.compaction.compressor.compression_count == 0
    assert all(getattr(message, "stop_reason", None) != "error" for message in app.messages)
    assert any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, TextContent) and block.text == "recovered with lower output cap"
            for block in message.content
        )
        for message in app.messages
    )


def test_coding_app_default_compaction_summarizer_uses_active_model(tmp_path: Path) -> None:
    model = faux_model()
    summary_prompts: list[str] = []

    def script(m, c):
        summary_prompts.append(c.system_prompt or "")
        return text_response_events(m, "## Historical Task Snapshot\nmodel-backed summary")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    status = app.compaction.compress_manual_with_status(app.messages)

    assert status.compressed is True
    assert status.warning is None
    assert summary_prompts == ["You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary following the exact format specified.\n\nDo NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."]
    assert any("model-backed summary" in str(message.content) for message in status.messages)


def test_coding_app_default_compaction_summarizer_uses_control_plane_auth(tmp_path: Path) -> None:
    calls: list[tuple[Model, object | None]] = []

    def stream(model, context, options=None):
        calls.append((model, options))
        result = create_assistant_message_event_stream()
        for event in text_response_events(model, "## Historical Task Snapshot\ncontrol-plane summary"):
            result.push(event)
        return result

    provider = ApiProvider(api="capturing", stream=stream, stream_simple=stream)
    control_plane = ProviderControlPlane.in_memory()
    control_plane.api_providers.register(provider)
    register_api_provider(provider)
    control_plane.auth.set_runtime_api_key("runtime-provider", "runtime-test-key")
    model = Model(id="initial-model", name="Initial", api="capturing", provider="runtime-provider", base_url="")
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        provider_control_plane=control_plane,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    status = app.compaction.compress_manual_with_status(app.messages)

    assert status.warning is None
    assert calls
    assert getattr(calls[-1][1], "api_key", None) == "runtime-test-key"


def test_coding_app_default_compaction_summarizer_tracks_model_switch(tmp_path: Path) -> None:
    calls: list[Model] = []

    def stream(model, context, options=None):
        del context, options
        calls.append(model)
        result = create_assistant_message_event_stream()
        for event in text_response_events(model, "## Historical Task Snapshot\nselected-model summary"):
            result.push(event)
        return result

    provider = ApiProvider(api="capturing", stream=stream, stream_simple=stream)
    control_plane = ProviderControlPlane.in_memory()
    control_plane.api_providers.register(provider)
    register_api_provider(provider)
    initial_model = Model(
        id="initial-model",
        name="Initial",
        api="capturing",
        provider="runtime-provider",
        base_url="",
    )
    selected_model = Model(
        id="selected-model",
        name="Selected",
        api="capturing",
        provider="runtime-provider",
        base_url="",
    )
    control_plane.ensure_model(selected_model)
    app = CodingApp(
        cwd=str(tmp_path),
        model=initial_model,
        terminal=FakeTerminal(),
        context_length=2000,
        provider_control_plane=control_plane,
    )
    app.session.set_model(selected_model)
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    status = app.compaction.compress_manual_with_status(app.messages)

    assert status.warning is None
    assert calls[-1] is selected_model


def test_coding_app_wires_compaction_manager_into_session_api(tmp_path: Path) -> None:
    model = faux_model()
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nsession compacted",
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    status = app.session.compact()

    assert status.compressed is True
    assert any("session compacted" in str(message.content) for message in app.messages)


def test_coding_app_manual_deep_compaction_reports_baseline_target(tmp_path: Path) -> None:
    model = faux_model()
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=128_000,
        summarizer=lambda prompt: "## Historical Task Snapshot\ndeep compacted",
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"deep old context {index} " * 500, timestamp=now_ms())
        for index in range(40)
    ]

    status = app.compaction.compress_manual_with_status(app.messages, deep=True)

    assert status.compressed is True
    assert status.deep is True
    assert status.target_tokens is not None
    assert status.target_tokens >= 4096
    assert status.compression_passes >= 1
    assert status.deep_stop_reason
    assert any("deep compacted" in str(message.content) for message in status.messages)


def test_coding_app_preflight_compacts_after_large_tool_result_before_next_provider_call(tmp_path: Path) -> None:
    huge_output = "important output\n" + ("x" * 80_000)
    model = faux_model()
    seen_contexts = []

    def script(m, c):
        seen_contexts.append(c)
        if len(seen_contexts) == 1:
            return tool_call_response_events(m, "huge", {})
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def huge_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=huge_output)], details={})

    huge_tool = AgentTool(
        name="huge",
        description="Return a large tool payload.",
        parameters={"type": "object", "properties": {}},
        label="Huge",
        execute=huge_execute,
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\ninner-loop compacted huge tool output",
    )
    app.session.agent.state.tools = [huge_tool]

    app.run_turn("run huge")

    assert len(seen_contexts) == 2
    assert app.compaction.compressor.compression_count == 1
    second_context_text = "\n".join(str(message.content) for message in seen_contexts[1].messages)
    assert "inner-loop compacted huge tool output" in second_context_text
    assert huge_output not in second_context_text


def test_coding_app_compacts_failed_large_turn_before_followup_provider_call(tmp_path: Path) -> None:
    huge_output = "read packages/ai/src/index.ts\n" + ("x" * 80_000)
    model = faux_model()
    seen_contexts = []

    def script(m, c):
        seen_contexts.append(c)
        return text_response_events(m, "ready")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nfailed scan compacted",
    )
    app.session.agent.state.messages = [
        UserMessage(content=[TextContent(text="analyze the codebase and read all files")], timestamp=now_ms()),
        AssistantMessage(
            content=[ToolCall(id="read-1", name="read", arguments={"path": "packages/ai/src/index.ts"})],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="read-1",
            tool_name="read",
            content=[TextContent(text=huge_output)],
            is_error=False,
            timestamp=now_ms(),
        ),
        AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message=(
                "OpenRouter authorization failed (HTTP 403) for model qwen/qwen3-coder-next. "
                "Provider message: Forbidden"
            ),
            timestamp=now_ms(),
        ),
    ]
    app.compaction.awaiting_real_usage_after_compression = True

    app.run_turn("hi")

    assert len(seen_contexts) == 1
    context_text = "\n".join(str(message.content) for message in seen_contexts[0].messages)
    assert "failed scan compacted" in context_text
    assert huge_output not in context_text


def test_coding_app_compacts_prompt_guardrail_failed_turn_before_followup_provider_call(tmp_path: Path) -> None:
    blocked_output = (
        "read src/providers/amazon-bedrock.ts\n"
        + ("system prefix spoofing source fixture\n" * 400)
    )
    model = faux_model()
    seen_contexts = []

    def script(m, c):
        seen_contexts.append(c)
        return text_response_events(m, "ready")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=128_000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nguardrail scan compacted",
    )
    app.session.agent.state.messages = [
        UserMessage(content=[TextContent(text="analyze the codebase and read all python files")], timestamp=now_ms()),
        AssistantMessage(
            content=[
                ToolCall(
                    id="read-1",
                    name="read",
                    arguments={"path": "src/providers/amazon-bedrock.ts"},
                )
            ],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="read-1",
            tool_name="read",
            content=[TextContent(text=blocked_output)],
            is_error=False,
            timestamp=now_ms(),
        ),
        AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message=(
                "OpenRouter prompt-injection guardrail blocked the request (HTTP 403) "
                "for model qwen/qwen3-coder-next. Provider message: "
                "Request blocked: prompt injection patterns detected. "
                "Patterns: system_prefix_spoofing"
            ),
            timestamp=now_ms(),
        ),
    ]
    app.compaction.awaiting_real_usage_after_compression = True

    app.run_turn("hi")

    assert len(seen_contexts) == 1
    context_text = "\n".join(str(message.content) for message in seen_contexts[0].messages)
    assert "guardrail scan compacted" in context_text
    assert blocked_output not in context_text
    assert "system_prefix_spoofing" not in context_text


def test_coding_app_prompt_guardrail_compaction_persists_clean_travis234_branch(tmp_path: Path) -> None:
    session_path = tmp_path / "guardrail-compaction.jsonl"
    blocked_output = (
        "read src/providers/amazon-bedrock.ts\n"
        + ("system_prefix_spoofing source fixture\n" * 400)
    )
    model = faux_model()
    seen_contexts = []

    def script(m, c):
        seen_contexts.append(c)
        return text_response_events(m, "ready")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        context_length=128_000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nguardrail persisted compacted",
        session_path=str(session_path),
    )
    persisted_messages = [
        UserMessage(content=[TextContent(text="analyze the codebase and read all python files")], timestamp=now_ms()),
        AssistantMessage(
            content=[
                ToolCall(
                    id="read-1",
                    name="read",
                    arguments={"path": "src/providers/amazon-bedrock.ts"},
                )
            ],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="read-1",
            tool_name="read",
            content=[TextContent(text=blocked_output)],
            is_error=False,
            timestamp=now_ms(),
        ),
        AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message=(
                "OpenRouter prompt-injection guardrail blocked the request (HTTP 403) "
                "for model qwen/qwen3-coder-next. Provider message: "
                "Request blocked: prompt injection patterns detected. "
                "Patterns: system_prefix_spoofing"
            ),
            timestamp=now_ms(),
        ),
    ]
    for message in persisted_messages:
        app.session._session_store.append_message(message)  # noqa: SLF001 - seed persisted branch.
    snapshot = app.session._session_store.build_context(default_thinking_level=app.session.thinking_level)  # noqa: SLF001
    app.session.agent.state.messages = snapshot.messages
    app.compaction.awaiting_real_usage_after_compression = True

    app.run_turn("hi")

    reloaded = app.session._session_store.build_context(default_thinking_level=app.session.thinking_level)  # noqa: SLF001
    reloaded_text = "\n".join(
        f"{getattr(message, 'summary', '')}\n{getattr(message, 'content', '')}"
        for message in reloaded.messages
    )
    assert len(seen_contexts) == 1
    assert "guardrail persisted compacted" in reloaded_text
    assert blocked_output not in reloaded_text
    assert "system_prefix_spoofing" not in reloaded_text
    assert all(
        not (isinstance(message, AssistantMessage) and message.stop_reason == "error")
        for message in reloaded.messages
    )
