from __future__ import annotations

from pathlib import Path

from appv22.app import CodingApp
from appv22.ai.types import AssistantMessage, ErrorEvent, TextContent, UserMessage, empty_usage, now_ms
from appv22.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv22.ai.stream import register_api_provider, reset_api_providers
from appv22.tui.terminal import FakeTerminal


def setup_function() -> None:
    reset_api_providers()


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


def test_coding_app_wires_compaction_transform(tmp_path: Path) -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "ok")))
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), context_length=1000)
    # transform_context is the hermes preflight phase
    assert app.session.agent._transform_context is not None
    app.run_turn("hello")
    assert any(getattr(m, "role", None) == "assistant" for m in app.messages)


def test_coding_app_forwards_initial_thinking_level_to_session(tmp_path: Path) -> None:
    model = faux_model()
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), thinking_level="high")

    assert app.session.thinking_level == "high"


def test_coding_app_runs_hermes_post_response_compaction(tmp_path: Path) -> None:
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
        "[CONTEXT COMPACTION - REFERENCE ONLY]" in str(message.content)
        and "END OF CONTEXT SUMMARY" in str(message.content)
        for message in app.messages
    )


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
