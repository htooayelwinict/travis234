from __future__ import annotations

import json
from pathlib import Path

import pytest

from travis.ai.models import reset_models
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.stream import register_api_provider, reset_api_providers
from travis.ai.types import AssistantMessage, ErrorEvent, TextContent, UserMessage, empty_usage, now_ms
from travis.app import CodingApp
from travis.coding_agent.eval_trace import ConversationLogWriter, EvalTraceWriter, SecretRedactor
from travis.tui.interactive_mode import InteractiveMode
from travis.tui.terminal import FakeTerminal


def setup_function() -> None:
    reset_models()
    reset_api_providers()


def test_eval_trace_records_lifecycle_without_sensitive_content(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = EvalTraceWriter(path, redactor=SecretRedactor(["sk-secret-value", "private prompt text"]))

    with pytest.raises(ValueError, match="unsafe trace field"):
        writer.write("tool_end", {"tool": "bash", "result": "private prompt text sk-secret-value"})
    with pytest.raises(ValueError, match="secret material"):
        writer.write("fatal", {"error_code": "sk-secret-value"})
    writer.write("tool_end", {"tool": "bash", "status": "ok", "duration_ms": 5})

    text = path.read_text(encoding="utf-8")
    assert "tool_end" in text
    assert "duration_ms" in text
    assert "sk-secret-value" not in text
    assert "private prompt text" not in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_eval_trace_accepts_capability_grant_synchronization_event(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = EvalTraceWriter(path)

    writer.write("capability_granted", {"status": "ok"})

    assert json.loads(path.read_text(encoding="utf-8"))["event"] == "capability_granted"


def test_eval_trace_accepts_sanitized_feature_audit_metadata(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = EvalTraceWriter(path)

    writer.write(
        "tui_ready",
        {"session_id": "session-1", "session_path": "/tmp/session-1.jsonl", "provider": "openrouter", "model": "m"},
    )
    writer.write("tool_end", {"tool": "process", "action": "write", "status": "ok"})
    writer.write("compaction_end", {"trigger": "threshold", "status": "ok", "compression_count": 1})
    writer.write(
        "turn_ready",
        {
            "status": "ok",
            "context_tokens": 64_000,
            "context_window": 256_000,
            "context_percent": 25.0,
            "context_estimated": False,
            "context_confidence": "provider_real",
            "compression_count": 1,
        },
    )
    writer.write("user_command_interrupt", {"interrupt_count": 2, "status": "ok"})

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["interrupt_count"] == 2
    assert events[-2]["event"] == "turn_ready"
    assert events[-2]["context_percent"] == 25.0


def test_interactive_trace_emits_ordered_safe_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = EvalTraceWriter(path, redactor=SecretRedactor(["private prompt text"]))
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "private response text"))
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
        event_trace=writer,
    )
    inputs = iter(["private prompt text", "/exit"])

    InteractiveMode(app, input_fn=lambda prompt: next(inputs)).run()

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    event_types = [event["event"] for event in events]
    assert event_types.index("tui_ready") < event_types.index("turn_start")
    assert (
        event_types.index("turn_start")
        < event_types.index("turn_end")
        < event_types.index("turn_ready")
        < event_types.index("shutdown")
    )
    turn_ready = next(event for event in events if event["event"] == "turn_ready")
    assert "context_window" in turn_ready
    assert "context_percent" in turn_ready
    assert turn_ready["compression_count"] == 0
    ready = next(event for event in events if event["event"] == "tui_ready")
    assert ready["session_id"] == app.session.session_id
    assert ready["session_path"] == app.session.session_path
    encoded = json.dumps(events)
    assert "private prompt text" not in encoded
    assert "private response text" not in encoded


def test_conversation_log_records_semantic_turn_and_redacts_secret_shapes(tmp_path: Path) -> None:
    path = tmp_path / "conversation.jsonl"
    writer = ConversationLogWriter(path)

    writer.write(
        turn_id="turn-1",
        prompt="Implement the parser",
        response="Done without exposing sk-secret123456",
        status="ok",
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == {
        "turn_id": "turn-1",
        "prompt": "Implement the parser",
        "response": "Done without exposing [REDACTED]",
        "status": "ok",
    }
    assert path.stat().st_mode & 0o777 == 0o600


def test_conversation_log_redacts_configured_provider_secret(tmp_path: Path) -> None:
    path = tmp_path / "conversation.jsonl"
    writer = ConversationLogWriter(path, redactor=SecretRedactor(["provider-secret-value"]))

    writer.write(
        turn_id="turn-1",
        prompt="Do not expose credentials",
        response="Accidental provider-secret-value output",
        status="ok",
    )

    text = path.read_text(encoding="utf-8")
    assert "provider-secret-value" not in text
    assert "[REDACTED]" in text


def test_coding_app_writes_final_assistant_text_to_conversation_log(tmp_path: Path) -> None:
    path = tmp_path / "conversation.jsonl"
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "Implemented and tested")))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=False,
        conversation_log=ConversationLogWriter(path),
    )

    app.run_turn("Repair the fixture")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["prompt"] == "Repair the fixture"
    assert record["response"] == "Implemented and tested"
    assert record["status"] == "ok"


def test_post_response_compaction_preserves_conversation_log_response(tmp_path: Path) -> None:
    path = tmp_path / "conversation.jsonl"

    def script(model, _context):
        events = text_response_events(model, "Implemented and tested before compaction")
        events[-1].message.usage.total_tokens = 200_000
        return events

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=False,
        context_length=100_000,
        summarizer=lambda _prompt: "## Goal\nkeep working\n## Remaining Work\ncontinue",
        conversation_log=ConversationLogWriter(path),
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    app.run_turn("Repair the fixture")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert app.compaction.compressor.compression_count == 1
    assert record["response"] == "Implemented and tested before compaction"
    assert record["status"] == "ok"


def test_coding_app_records_provider_error_as_terminal_output(tmp_path: Path) -> None:
    conversation_path = tmp_path / "conversation.jsonl"
    trace_path = tmp_path / "trace.jsonl"

    def provider_error(model, _context):
        message = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message="OpenRouter billing or quota failed (HTTP 402): Payment Required",
            timestamp=now_ms(),
        )
        return [ErrorEvent(reason="error", error=message)]

    register_api_provider(create_faux_provider(provider_error))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=False,
        conversation_log=ConversationLogWriter(conversation_path),
        event_trace=EvalTraceWriter(trace_path),
    )

    app.run_turn("Repair the fixture")

    record = json.loads(conversation_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    turn_end = next(event for event in events if event["event"] == "turn_end")
    assert record["status"] == "error"
    assert record["response"] == "Error: OpenRouter billing or quota failed (HTTP 402): Payment Required"
    assert turn_end["status"] == "error"
