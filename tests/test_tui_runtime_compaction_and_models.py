from __future__ import annotations

from tests._support_tui import *  # noqa: F403
from travis.ai.providers._shared import blank_assistant_message
from travis.compaction import SUMMARY_PREFIX
from travis.compaction.compressor import estimate_tokens


def test_interactive_mode_serializes_bang_bash_after_streaming_turn(tmp_path) -> None:
    first_stream_started = threading.Event()
    first_stream_released = threading.Event()
    first_stream_finished = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        events = text_response_events(model, "turn")
        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        first_stream_started.set()

        def finish() -> None:
            first_stream_released.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)
            first_stream_finished.set()

        threading.Thread(target=finish, daemon=True).start()
        return stream

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=100)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["first", "! printf streamed", "/exit"])

    def input_fn(prompt: str) -> str:
        value = next(inputs)
        if value == "! printf streamed":
            assert first_stream_started.wait(timeout=2)
        if value == "/exit":
            first_stream_finished.wait(timeout=2)
        return value

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert first_stream_started.wait(timeout=2)
    try:
        assert app.session.has_pending_bash_messages is False
        assert app.session.get_steering_messages() == []
        assert _wait_until(lambda: mode.status._message == "Running bash")
        first_stream_released.set()
        assert first_stream_finished.wait(timeout=2)
    finally:
        first_stream_released.set()
        thread.join(timeout=2)
    assert not thread.is_alive()

    rendered = strip_ansi("\n".join(app.tui.render(100)))
    assert "$ printf streamed" in rendered
    assert "streamed" in rendered

def test_interactive_mode_keeps_agent_output_above_status_footer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "ordered reply")))
    terminal = FakeTerminal(columns=100)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["hi", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = app.tui.render(100)
    prompt_index = next(index for index, line in enumerate(rendered) if "hi" in strip_ansi(line))
    reply_index = next(index for index, line in enumerate(rendered) if line == "ordered reply")
    status_index = next(index for index, line in enumerate(rendered) if line.startswith("status:"))
    footer_index = next(index for index, line in enumerate(rendered) if "faux-model" in strip_ansi(line))

    assert prompt_index < reply_index < status_index < footer_index

def test_interactive_mode_labels_post_response_compaction_after_reply(tmp_path) -> None:
    compression_started = threading.Event()
    release_compression = threading.Event()

    def script(model, context):
        events = text_response_events(model, "reply before compaction")
        events[-1].message.usage.input = 200_000
        events[-1].message.usage.total_tokens = 200_000
        return events

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        enable_tui=True,
        context_length=100_000,
    )
    original_compact_post_response = app._compact_post_response

    def blocking_compact_post_response() -> None:
        compression_started.set()
        release_compression.wait(timeout=2)
        original_compact_post_response()

    app._compact_post_response = blocking_compact_post_response
    input_calls = {"n": 0}
    allow_exit_input = threading.Event()

    def input_fn(prompt: str) -> str:
        input_calls["n"] += 1
        if input_calls["n"] == 1:
            return "hi"
        allow_exit_input.wait(timeout=2)
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert compression_started.wait(timeout=2)
    assert _wait_until(lambda: "reply before compaction" in strip_ansi(terminal.output))
    assert _wait_until(lambda: mode.status._message == "Compressing")
    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert "reply before compaction" in rendered
    assert "status: Compressing" in rendered
    assert "status: Running" not in rendered

    release_compression.set()
    allow_exit_input.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

def test_interactive_mode_auto_compaction_notice_uses_actual_compaction_boundary_tokens(tmp_path) -> None:
    from travis.compaction.compressor import CompressionResult

    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=180), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    app.compaction.compressor.compression_count = 1
    app.compaction.last_compression_before_tokens = 50_000
    app.compaction.last_compression_after_tokens = 12_000
    app.compaction._last_compression_result = CompressionResult(
        messages=[],
        compressed=True,
        savings_pct=76.0,
        summary_model_requested="openrouter/openai/gpt-5.6-luna-pro",
        summary_model_used="faux/faux-model",
        summary_model_fallback=True,
        summary_model_error="temporary route failure",
    )

    mode.init()
    mode._render_auto_compaction_notice(before_compressions=0, before_tokens=8)

    rendered = strip_ansi("\n".join(app.tui.render(180)))
    assert "Context compacted: ~50,000 -> ~12,000 tokens" in rendered
    assert "Context compacted: ~8 ->" not in rendered
    assert "Compression model 'openrouter/openai/gpt-5.6-luna-pro' failed" in rendered
    assert "recovered with 'faux/faux-model'" in rendered


def test_interactive_mode_surfaces_auto_compaction_abort_without_repeating(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=180), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    app.compaction.compressor._last_compress_aborted = True
    app.compaction.compressor._last_summary_model_requested = "openrouter/openai/gpt-5.6-luna-pro"
    app.compaction.compressor._last_summary_error = "provider timed out"

    mode.init()
    mode._render_auto_compaction_notice(before_compressions=0, before_tokens=50_000)
    mode._render_auto_compaction_notice(before_compressions=0, before_tokens=50_000)

    rendered = strip_ansi("\n".join(app.tui.render(180)))
    assert rendered.count("Context compaction aborted") == 1
    assert "conversation preserved unchanged" in rendered


def test_interactive_mode_auto_compacts_21_plus_turns_and_resumes_after_reload(tmp_path) -> None:
    anchor = "ANCHOR-TUI-CONTINUITY-7429"
    main_model = Model(
        id="main-model",
        name="Main",
        api="stress-main",
        provider="main-provider",
        base_url="",
        context_window=4_000,
        max_tokens=0,
    )
    summary_model = Model(
        id="summary-model",
        name="Summary",
        api="stress-summary",
        provider="summary-provider",
        base_url="",
        context_window=4_000,
        max_tokens=2_000,
    )
    main_contexts: list[dict[str, object]] = []
    summary_prompts: list[str] = []

    def message_text(message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        return "\n".join(getattr(block, "text", "") for block in content)

    def main_script(model, context):
        transcript = "\n".join(message_text(message) for message in context.messages)
        prompt_tokens = estimate_tokens(context.messages)
        main_contexts.append(
            {
                "has_summary": SUMMARY_PREFIX in transcript,
                "has_anchor": anchor in transcript,
                "prompt_tokens": prompt_tokens,
            }
        )
        text = f"continuity={anchor if anchor in transcript else 'MISSING'}\n" + ("work-product " * 48)
        partial = blank_assistant_message(model)
        partial.content = [TextContent(text=text)]
        final = blank_assistant_message(model)
        final.content = [TextContent(text=text)]
        final.usage.input = prompt_tokens
        final.usage.output = max(1, len(text) // 4)
        final.usage.total_tokens = final.usage.input + final.usage.output
        return [
            StartEvent(partial=partial),
            TextStartEvent(content_index=0, partial=partial),
            TextDeltaEvent(content_index=0, delta=text, partial=partial),
            TextEndEvent(content_index=0, content=text, partial=partial),
            DoneEvent(reason="stop", message=final),
        ]

    def summary_script(model, context):
        prompt = "\n".join(message_text(message) for message in context.messages)
        summary_prompts.append(prompt)
        return text_response_events(
            model,
            f"## Goal\nContinue the stress run.\n## Critical Context\nContinuity token: {anchor}.",
        )

    register_api_provider(create_faux_provider(main_script, api="stress-main"))
    register_api_provider(create_faux_provider(summary_script, api="stress-summary"))
    session_path = tmp_path / "stress-session.jsonl"
    terminal = FakeTerminal(columns=140, rows=50)
    app = CodingApp(
        cwd=str(tmp_path),
        session_path=str(session_path),
        model=main_model,
        compression_model=summary_model,
        terminal=terminal,
        enable_tui=True,
    )
    prompts = [
        f"Task {index:02d}/24: continue independently and preserve {anchor}."
        for index in range(1, 25)
    ] + ["/exit"]
    prompt_index = [0]
    holder: dict[str, object] = {}

    def input_fn(_prompt: str) -> str:
        index = prompt_index[0]
        if index:
            mode = holder["mode"]
            assert _wait_until(lambda: not mode._is_turn_active() and mode.status._message == "Idle", timeout=5)
        value = prompts[index]
        prompt_index[0] += 1
        return value

    mode = InteractiveMode(app, input_fn=input_fn)
    holder["mode"] = mode

    assert mode.run() == 0
    assert len(main_contexts) == 24
    assert summary_prompts
    assert all(anchor in prompt for prompt in summary_prompts)
    assert app.compressor.compression_count == len(summary_prompts)
    assert "Context compacted:" in terminal.output
    assert any(context["has_summary"] and context["has_anchor"] for context in main_contexts)
    app.close()

    reloaded = CodingApp(
        cwd=str(tmp_path),
        session_path=str(session_path),
        model=main_model,
        compression_model=summary_model,
        terminal=FakeTerminal(),
        enable_tui=False,
    )
    reloaded.run_turn(f"Task 25: resume after reload and preserve {anchor}.")
    reloaded.close()

    assert main_contexts[-1]["has_summary"] is True
    assert main_contexts[-1]["has_anchor"] is True

def test_interactive_mode_footer_marks_context_rough_while_awaiting_real_usage(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=120), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    app.compaction.awaiting_real_usage_after_compression = True

    mode._refresh_footer()

    assert mode.footer.context_percent is None
    assert mode.footer.context_percent_unknown is True
    rendered = strip_ansi("\n".join(mode.footer.render(120)))
    assert "~0.0%/" in rendered
    assert "?/" not in rendered

def test_interactive_mode_renders_auto_retry_status_instead_of_stale_running(tmp_path) -> None:
    retry_started = threading.Event()
    allow_exit = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        if stream_calls["n"] == 1:
            stream = create_assistant_message_event_stream()
            error_message = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message="Provider finish_reason: network_error",
                timestamp=now_ms(),
            )
            stream.push(ErrorEvent(reason="error", error=error_message))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "retry recovered")).stream_simple(
            model, context, options
        )

    register_api_provider(ApiProvider(api="faux", stream=stream_fn, stream_simple=stream_fn))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.set_auto_retry_enabled(True)
    app.session._max_retries = 1
    app.session._retry_delay_ms = 5000
    app.session.subscribe(lambda event: retry_started.set() if event.type == "auto_retry_start" else None)
    input_calls = {"n": 0}

    def input_fn(prompt: str) -> str:
        input_calls["n"] += 1
        if input_calls["n"] == 1:
            return "hi"
        allow_exit.wait(timeout=2)
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert retry_started.wait(timeout=2)
    try:
        assert _wait_until(lambda: mode.status._message.startswith("Retrying (1/1) in 5s"))
        assert mode.status._message != "Running"
    finally:
        app.session.abort_retry()
        allow_exit.set()
        thread.join(timeout=2)
    assert not thread.is_alive()

def test_interactive_mode_bang_runs_bash_without_model_and_records_context(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["! printf hi", "!! printf secret", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["n"] == 0
    assert "$ printf hi" in rendered
    assert "$ printf secret [no context]" in rendered
    assert "hi" in rendered
    assert "secret" in rendered
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert [message.command for message in bash_messages] == ["printf hi", "printf secret"]
    assert bash_messages[0].exclude_from_context in (None, False)
    assert bash_messages[1].exclude_from_context is True
    converted = app.session._convert_to_llm(app.messages)
    converted_text = "\n".join(
        block.text for message in converted for block in getattr(message, "content", []) if getattr(block, "type", None) == "text"
    )
    assert "printf hi" in converted_text
    assert "printf secret" not in converted_text

def test_bang_runs_while_agent_executor_is_occupied(tmp_path) -> None:
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    turn_started = threading.Event()
    release_turn = threading.Event()
    future = mode._command_executor().submit(
        "turn",
        lambda: (turn_started.set(), release_turn.wait(timeout=2)),
    )
    assert turn_started.wait(timeout=1)
    try:
        started = time.monotonic()
        mode._run_bash_command("printf user", exclude_from_context=False)

        assert time.monotonic() - started < 0.25
        assert release_turn.is_set() is False
        assert _wait_until(
            lambda: (mode.tui.drain_dispatcher() or True)
            and "user" in strip_ansi("\n".join(mode.history.render(120)))
        )
    finally:
        release_turn.set()
        future.result(timeout=1)
        mode._user_commands.close()
        mode.tui.drain_dispatcher()
        mode._command_executor().close()
        mode.footer_data_provider.dispose()
        app.close()


def test_user_command_completion_backfills_terminal_trace_when_listener_misses_event(tmp_path) -> None:
    import json
    import shlex
    import sys

    from travis.coding_agent.eval_trace import EvalTraceWriter

    trace_path = tmp_path / "trace.jsonl"
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
        event_trace=EvalTraceWriter(trace_path),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    command = (
        f"{shlex.quote(sys.executable)} -c "
        f"{shlex.quote('import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)')}"
    )
    try:
        mode._run_bash_command(command, exclude_from_context=False)
        assert _wait_until(
            lambda: mode._user_commands is not None
            and bool(mode._user_commands.list())
            and mode._user_commands.list()[0].process_id is not None
        )
        assert mode._unsubscribe_process_events is None

        assert mode._user_commands is not None
        assert mode._user_commands.interrupt_focused() is True
        assert mode._user_commands.interrupt_focused() is True
        assert _wait_until(
            lambda: (mode.tui.drain_dispatcher() or True)
            and mode._user_commands is not None
            and not mode._user_commands.list()
        )
        mode.tui.drain_dispatcher()

        events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        terminal = [event for event in events if event["event"] == "process_event"]
        assert len(terminal) == 1
        assert terminal[0]["origin"] == "user"
        assert terminal[0]["process_state"] in {"terminated", "exited"}
    finally:
        if mode._user_commands is not None:
            mode._user_commands.close()
        mode.tui.drain_dispatcher()
        if mode._session_commands is not None:
            mode._session_commands.close()
        mode.footer_data_provider.dispose()
        app.close()

def test_slow_user_bash_extension_resolution_does_not_block_input(tmp_path) -> None:
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    entered = threading.Event()
    release = threading.Event()

    def slow_handler(event):
        entered.set()
        release.wait(timeout=2)
        return {
            "result": BashResult("extension done", 0, False, False),
        }

    app.session.extension_runner.on("user_bash", slow_handler)
    try:
        started = time.monotonic()
        mode._run_bash_command("printf extension", exclude_from_context=False)

        assert time.monotonic() - started < 0.25
        assert entered.wait(timeout=1)
    finally:
        release.set()
        mode._user_commands.close()
        mode.tui.drain_dispatcher()
        if mode._session_commands is not None:
            mode._session_commands.close()
        mode.footer_data_provider.dispose()
        app.close()

def test_bang_completion_records_against_launch_session_after_resume(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    first_path, first_id = catalog.new_session_path(str(workspace), "first-bang")
    second_path, second_id = catalog.new_session_path(str(workspace), "second-bang")
    second_store = SessionStore(second_path, cwd=str(workspace), session_id=second_id)
    second_store.append_model_change("faux", "faux-model")
    second_store.append_thinking_level_change("off")
    second_store.append_message(UserMessage(content="second session", timestamp=now_ms()))
    app = CodingApp(
        cwd=str(workspace),
        model=faux_model(),
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
        session_path=first_path,
        session_id=first_id,
        agent_dir=str(agent_dir),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    entered = threading.Event()
    release = threading.Event()

    def execute(command, cwd, options):
        entered.set()
        release.wait(timeout=2)
        options.on_data(b"launch-session-output")
        return {"exit_code": 0}

    app.session.extension_runner.on(
        "user_bash",
        lambda event: {"operations": BashOperations(exec=execute)},
    )
    try:
        mode._run_bash_command("delayed", exclude_from_context=False)
        assert entered.wait(timeout=1)

        app.switch_session(second_path)
        release.set()
        assert _wait_until(
            lambda: (mode.tui.drain_dispatcher() or True)
            and mode._user_commands is not None
            and not mode._user_commands.list()
        )
        mode.tui.drain_dispatcher()
        if mode._session_commands is not None:
            mode._session_commands.close()

        first_messages = SessionStore(first_path, cwd=str(workspace)).build_context().messages
        second_messages = SessionStore(second_path, cwd=str(workspace)).build_context().messages
        assert any(
            getattr(message, "role", None) == "bashExecution"
            and "launch-session-output" in message.output
            for message in first_messages
        )
        assert not any(
            getattr(message, "role", None) == "bashExecution"
            and "launch-session-output" in message.output
            for message in second_messages
        )
    finally:
        release.set()
        if mode._user_commands is not None:
            mode._user_commands.close()
        mode.tui.drain_dispatcher()
        if mode._session_commands is not None:
            mode._session_commands.close()
        mode.footer_data_provider.dispose()
        app.close()

def test_interactive_mode_bang_uses_user_bash_extension_result(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    seen_events: list[dict] = []

    def handle_user_bash(event):
        seen_events.append(event)
        return {
            "result": BashResult(
                output="from extension\n",
                exit_code=0,
                cancelled=False,
                truncated=False,
                full_output_path=None,
            )
        }

    app.session.extension_runner.on("user_bash", handle_user_bash)
    inputs = iter(["! printf from-shell", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert calls["n"] == 0
    assert seen_events == [
        {
            "type": "user_bash",
            "command": "printf from-shell",
            "excludeFromContext": False,
            "cwd": str(tmp_path),
        }
    ]
    assert "from extension" in rendered
    assert bash_messages[-1].command == "printf from-shell"
    assert bash_messages[-1].output == "from extension\n"

def test_interactive_mode_bang_uses_user_bash_extension_operations(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    exec_calls: list[tuple[str, str]] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        exec_calls.append((command, cwd))
        options.on_data(b"from custom operations\n")
        return {"exit_code": 0}

    def handle_user_bash(event):
        return {
            "operations": BashOperations(exec=exec_command),
            "commandPrefix": "source ~/.profile",
        }

    app.session.extension_runner.on("user_bash", handle_user_bash)
    inputs = iter(["! printf from-shell", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    bash_messages = [message for message in app.messages if getattr(message, "role", None) == "bashExecution"]
    assert exec_calls == [("source ~/.profile\nprintf from-shell", str(tmp_path))]
    assert "from custom operations" in rendered
    assert bash_messages[-1].command == "printf from-shell"
    assert bash_messages[-1].output == "from custom operations\n"

def test_interactive_mode_manual_compress_renders_feedback_and_updates_footer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nmanual compacted",
        enable_tui=True,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    inputs = iter(["/compress old context", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = "\n".join(app.tui.render(140))
    assert "compact: Compressed:" in rendered
    assert "Approx request size:" in rendered
    assert "%/" in rendered
    assert "faux-model" in rendered

def test_interactive_mode_manual_compress_failure_resets_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def fail_manual_compress(*args, **kwargs):
        raise RuntimeError("summary provider stuck")

    app.session.compact = fail_manual_compress
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode._run_manual_compress("/compress")

    rendered = "\n".join(app.tui.render(140))
    assert mode.status._message == "Idle"
    assert "compact: Compression failed: summary provider stuck" in rendered
    assert "status: Compressing" not in rendered

def test_interactive_mode_manual_compress_routes_deep_mode_through_session(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    calls: list[tuple[str | None, bool]] = []

    def fake_compact(focus=None, summarizer=None, deep=False):
        calls.append((focus, deep))
        return ManualCompressionStatus(
            messages=app.messages,
            compressed=False,
            noop=True,
            headline="No changes from compression: 0 messages",
            token_line="Approx request size: ~0 tokens (unchanged)",
            focus=focus,
            deep=deep,
        )

    app.session.compact = fake_compact
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode._run_manual_compress("/compress deep code scan")
    mode._run_manual_compress("/compress aggressive database schema")

    rendered = "\n".join(app.tui.render(140))
    assert calls == [("code scan", True), ("aggressive database schema", False)]
    assert mode.status._message == "Idle"
    assert "compact: No changes from compression: 0 messages" in rendered
    assert "status: Compressing" not in rendered

def test_status_line_uses_signal_glass_theme_for_known_kinds(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    line = StatusLine("Compression complete", kind="compact")

    rendered = "\n".join(line.render(80))

    assert "\x1b[38;2;86;240;182m" in rendered
    assert strip_ansi(rendered) == "compact: Compression complete"

def test_status_line_respects_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    line = StatusLine("Compression complete", kind="compact")

    rendered = "\n".join(line.render(80))

    assert "\x1b[" not in rendered
    assert rendered == "compact: Compression complete"

def test_footer_uses_signal_glass_theme_without_changing_text(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    footer = FooterComponent(
        cwd=str(tmp_path),
        model="faux-model",
        provider="faux",
        context_window=128_000,
        context_percent=3.5,
    )

    rendered = "\n".join(footer.render(120))

    assert "\x1b[38;2;120;255;208m" in rendered
    plain = strip_ansi(rendered)
    assert "faux-model" in plain
    assert "3.5%/128k" in plain

def test_interactive_mode_compact_alias_is_local_and_does_not_call_model(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        context_length=2000,
        summarizer=lambda prompt: "## Historical Task Snapshot\nmanual compacted",
        enable_tui=True,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]
    inputs = iter(["/compact old context", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = "\n".join(app.tui.render(140))
    assert calls["n"] == 0
    assert "compact: Compressed:" in rendered
    assert "model should not run" not in rendered
    assert "%/" in rendered

def test_interactive_mode_login_logout_oauth_are_local_tui_commands(tmp_path) -> None:
    calls: list[object] = []

    def script(model, context):
        calls.append(("model", context))
        return text_response_events(model, "model should not run")

    def login(callbacks):
        calls.append(("login", sorted(callbacks.keys())))
        return {"access": "login-token", "refresh": "refresh-token", "expires": 4_102_444_800_000}

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_provider(
        "sso",
        {
            "baseUrl": "https://sso.example.test",
            "api": "faux",
            "oauth": {
                "name": "Corporate SSO",
                "login": login,
                "refreshToken": lambda credentials: credentials,
                "getApiKey": lambda credentials: credentials["access"],
            },
            "models": [
                {
                    "id": "sso-model",
                    "name": "SSO Model",
                    "reasoning": False,
                    "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "contextWindow": 32000,
                    "maxTokens": 4096,
                }
            ],
        },
    )
    inputs = iter(["/login", "1", "Corporate SSO", "/logout", "Corporate SSO", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert [call[0] for call in calls] == ["login"]
    assert "onAuth" in calls[0][1]
    assert "onDeviceCode" in calls[0][1]
    assert "onPrompt" in calls[0][1]
    assert "Logged in to Corporate SSO" in rendered
    assert "Logged out of Corporate SSO" in rendered
    assert app.session.model_registry.get_provider_auth_status("sso") == {"configured": False}
    assert app.session.model_registry.get_api_key_for_provider("sso") is None
    assert "model should not run" not in rendered

def test_interactive_mode_login_api_key_is_local_tui_command(tmp_path, monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(tmp_path / "agent"))

    def script(model, context):
        calls.append(("model", context))
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_provider(
        "proxy",
        {
            "name": "Proxy AI",
            "baseUrl": "https://proxy.example.test",
            "api": "faux",
            "apiKey": "$PROXY_API_KEY",
            "models": [
                {
                    "id": "proxy-model",
                    "name": "Proxy Model",
                    "reasoning": False,
                    "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "contextWindow": 32000,
                    "maxTokens": 4096,
                }
            ],
        },
    )
    inputs = iter(["/login", "2", "Proxy AI", "typed-secret", "/logout", "Proxy AI", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls == []
    assert "Saved API key for Proxy AI" in rendered
    assert "Removed stored API key for Proxy AI" in rendered
    assert "typed-secret" not in rendered
    assert app.session.model_registry.get_provider_auth_status("proxy") == {"configured": False}
    assert app.session.model_registry.get_api_key_for_provider("proxy") is None
    assert "model should not run" not in rendered

def test_interactive_mode_login_api_key_offers_active_provider_without_registered_model(monkeypatch, tmp_path) -> None:
    agent_dir = tmp_path / "agent-home" / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    model = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    inputs = iter(["/login", "2", "OpenRouter", "typed-secret", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Saved API key for OpenRouter" in rendered
    assert "typed-secret" not in rendered
    assert app.session.model_registry.get_provider_auth_status("openrouter") == {
        "configured": True,
        "source": "stored",
    }
    assert app.session.model_registry.get_api_key_for_provider("openrouter") == "typed-secret"
    stored = json.loads((agent_dir / "auth.json").read_text(encoding="utf-8"))
    assert stored == {"openrouter": {"type": "api_key", "key": "typed-secret"}}
    assert (agent_dir / "auth.json").stat().st_mode & 0o777 == 0o600
    assert "model should not run" not in rendered

def test_interactive_mode_footer_counts_active_provider_without_registered_model(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=140)
    model = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    mode.init()

    try:
        assert mode.footer_data_provider.get_available_provider_count() == 1
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_model_command_switches_openrouter_without_model_turn(tmp_path) -> None:
    calls: list[object] = []

    def script(model, context):
        calls.append((model, context))
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140)
    model = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    inputs = iter(["/model openrouter/moonshotai/kimi-k2.6", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls == []
    assert app.session.model.provider == "openrouter"
    assert app.session.model.id == "moonshotai/kimi-k2.6"
    assert "Switched model to openrouter/moonshotai/kimi-k2.6" in rendered

def test_interactive_mode_model_command_selects_registered_alternate_without_model_turn(tmp_path, monkeypatch) -> None:
    from tests._provider_runtime import current_registry

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="qwen/qwen3.6-flash",
        name="qwen/qwen3.6-flash",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    alternate = Model(
        id="moonshotai/kimi-k2.6",
        name="moonshotai/kimi-k2.6",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )
    registry = current_registry()
    registry.replace_all([alternate])
    app = CodingApp(
        cwd=str(tmp_path),
        model=active,
        terminal=terminal,
        enable_tui=True,
        model_registry=registry,
    )
    inputs = iter(["/model", "openrouter/moonshotai/kimi-k2.6", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert app.session.model is alternate
    assert "Select model:" in rendered
    assert "Switched model to openrouter/moonshotai/kimi-k2.6" in rendered
    assert "model should not run" not in rendered

def test_interactive_mode_extension_select_uses_tui_input_when_interactive(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app)
    mode.init()

    def fail_raw_input(prompt: str) -> str:
        raise AssertionError(f"raw input called: {prompt}")

    mode.input_fn = fail_raw_input
    result: dict[str, str | None] = {}
    error: dict[str, BaseException] = {}

    def prompt_for_selection() -> None:
        try:
            result["value"] = mode.prompt_extension_select("Pick auth method:", ("Use subscription", "Use API key"))
        except BaseException as exc:  # noqa: BLE001 - test thread captures assertion failures.
            error["value"] = exc

    thread = threading.Thread(target=prompt_for_selection)
    thread.start()
    deadline = time.monotonic() + 2
    while thread.is_alive() and mode.active_editor is None and time.monotonic() < deadline:
        time.sleep(0.01)
    app.tui._handle_terminal_input("2\r")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert error == {}
    assert result == {"value": "Use API key"}

def test_interactive_mode_extension_select_reports_invalid_numeric_choice(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "99")
    mode.init()

    result = mode.prompt_extension_select("Select model:", ("alpha", "beta"), kind="model")

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert result is None
    assert "Invalid selection: 99. Enter a number from 1 to 2." in rendered

def test_interactive_mode_extension_select_reports_blank_cancel(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "")
    mode.init()

    result = mode.prompt_extension_select("Select model:", ("alpha", "beta"), kind="model")

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert result is None
    assert "Selection cancelled." in rendered

def test_interactive_mode_coerces_read_numeric_string_like_travis234_validation(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "file.py").write_text("print('ok')\n", encoding="utf-8")
    calls = {"count": 0}
    seen_tool_result = {"text": ""}

    def script(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(model, "read", {"path": "src/file.py", "limit": "100.0"})
        seen_tool_result["text"] = "\n".join(
            block.text
            for message in context.messages
            if getattr(message, "role", None) == "toolResult"
            for block in getattr(message, "content", [])
            if hasattr(block, "text")
        )
        return text_response_events(model, "handled read")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    app.run_turn("read src/file.py with limit 100")

    rendered = "\n".join(app.tui.render(120))
    assert "read src/file.py" in rendered
    assert "read.limit: expected number" not in rendered
    assert "Traceback" not in rendered
    assert "print('ok')" in seen_tool_result["text"]
    assert calls["count"] == 2

def test_travis234_standalone_editor_helpers_are_exported_and_match_core_behavior() -> None:
    from travis.tui import KillRing, UndoStack, find_word_backward, find_word_forward, is_native_modifier_pressed

    ring = KillRing()
    ring.push("foo", {"prepend": False})
    ring.push("bar", {"prepend": False, "accumulate": True})
    ring.push("pre-", {"prepend": True, "accumulate": True})
    assert ring.peek() == "pre-foobar"
    ring.push("older", {"prepend": False})
    assert ring.length == 2
    ring.rotate()
    assert ring.peek() == "pre-foobar"

    stack = UndoStack()
    state = {"items": [1]}
    stack.push(state)
    state["items"].append(2)
    assert stack.length == 1
    assert stack.pop() == {"items": [1]}
    assert stack.pop() is None
    stack.push({"value": "x"})
    stack.clear()
    assert stack.length == 0

    assert find_word_backward("foo bar", 7) == 4
    assert find_word_forward("foo bar", 0) == 3
    assert find_word_backward("foo.bar", 7) == 4
    assert find_word_forward("foo.bar", 0) == 3
    assert find_word_forward("  word", 0) == 6
    assert find_word_backward("word  ", 6) == 0

    assert is_native_modifier_pressed("shift") is False
    assert is_native_modifier_pressed("command") is False

def test_strip_ansi_helper() -> None:
    assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

def test_markdown_render_reuses_cached_lines_until_text_changes() -> None:
    markdown = Markdown("**bold** `code`\n- *item*")

    first = markdown.render(80)
    second = markdown.render(80)

    assert second is first
    assert first == ["bold code", "- item"]

    markdown.set_text("**changed**")
    changed = markdown.render(80)

    assert changed is not first
    assert changed == ["changed"]

def test_input_render_avoids_full_width_scan_for_long_ascii_tail(monkeypatch) -> None:
    import travis.tui.components.editor as component_module

    real_visible_width = component_module.visible_width
    checked_lengths: list[int] = []

    def guarded_visible_width(text: str) -> int:
        checked_lengths.append(len(text))
        assert len(text) < 500
        return real_visible_width(text)

    monkeypatch.setattr(component_module, "visible_width", guarded_visible_width)
    editor = Input("x" * 20_000, prompt="travis> ")

    rendered = editor.render(100)

    assert len(rendered) == 1
    assert "x" in strip_ansi(rendered[0])
    assert max(checked_lengths) < 500

def test_interactive_mode_parses_params_command() -> None:
    from travis.tui.interactive_mode import _parse_params_command

    assert _parse_params_command("/params") == ""

def test_interactive_mode_parses_params_filter() -> None:
    from travis.tui.interactive_mode import _parse_params_command

    assert _parse_params_command("/params temperature") == "temperature"

def test_interactive_mode_params_command_displays_constructor_params(monkeypatch) -> None:
    class FakeSession:
        model = Model(id="step-3.7-flash", name="Step", api="openai-completions", provider="stepfun", base_url="")
        thinking_level = "off"
        session_name = "test"

        def subscribe(self, callback):
            return lambda: None

    class FakeApp:
        cwd = "."
        tui = TUI(FakeTerminal())
        session = FakeSession()
        messages = []

    mode = InteractiveMode(
        FakeApp(),
        generation_params=GenerationParams(
            temperature=0.2,
            max_tokens=4096,
            sources={"temperature": "cli", "max_tokens": "cli"},
        ),
    )
    shown: dict[str, str] = {}
    monkeypatch.setattr(mode, "_show_status", lambda message, kind="info": shown.update(message=message, kind=kind))

    mode._run_params_command("")

    assert shown["kind"] == "model"
    assert shown["message"] == "stepfun/step-3.7-flash: temperature=0.2 (cli), max_tokens=4096 (cli)"

def test_interactive_mode_params_command_displays_generation_param_warnings(monkeypatch) -> None:
    class FakeSession:
        model = Model(id="step-3.7-flash", name="Step", api="openai-completions", provider="stepfun", base_url="")
        thinking_level = "off"
        session_name = "test"

        def subscribe(self, callback):
            return lambda: None

    class FakeApp:
        cwd = "."
        tui = TUI(FakeTerminal())
        session = FakeSession()
        messages = []

    mode = InteractiveMode(
        FakeApp(),
        generation_params=GenerationParams(provider_sort="latency"),
        generation_param_warnings=[
            ProviderParamWarning(
                param="provider_sort",
                action="dropped",
                reason="stepfun does not support provider routing sort preferences.",
            )
        ],
    )
    shown: dict[str, str] = {}
    monkeypatch.setattr(mode, "_show_status", lambda message, kind="info": shown.update(message=message, kind=kind))

    mode._run_params_command("")

    assert shown["kind"] == "model"
    assert shown["message"] == (
        "stepfun/step-3.7-flash: provider_sort=latency; "
        "warnings: provider_sort dropped"
    )
