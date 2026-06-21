from __future__ import annotations

from pathlib import Path

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.app import CodingApp
from appv22.ai.types import (
    AssistantMessage,
    ErrorEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from appv22.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv22.ai.stream import register_api_provider, reset_api_providers
from appv22.coding_agent import SettingsManager
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


def test_coding_app_default_compaction_threshold_uses_model_context_with_static_prompt_reserve(tmp_path: Path) -> None:
    model = faux_model()
    model.context_window = 128_000
    model.max_tokens = 8_192

    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal())

    assert app.compressor.context_length == 128_000
    assert app.compressor.threshold_tokens < 128_000 - 16_384
    assert app.compressor.threshold_tokens > 100_000


def test_coding_app_forwards_initial_thinking_level_to_session(tmp_path: Path) -> None:
    model = faux_model()
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), thinking_level="high")

    assert app.session.thinking_level == "high"


def test_coding_app_forwards_pi_settings_manager_to_session(tmp_path: Path) -> None:
    settings = SettingsManager.inMemory({"shellCommandPrefix": "printf app-settings;"})

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
        "[CONTEXT COMPACTION — REFERENCE ONLY]" in str(message.content)
        and "END OF CONTEXT SUMMARY" in str(message.content)
        for message in app.messages
    )


def test_coding_app_emits_pi_auto_compaction_events_and_running_state(tmp_path: Path) -> None:
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
    assert model.max_tokens == 100
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
        "read pi/packages/ai/src/providers/amazon-bedrock.ts\n"
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
                    arguments={"path": "pi/packages/ai/src/providers/amazon-bedrock.ts"},
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
