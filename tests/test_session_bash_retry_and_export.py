"""Session bash, retry, typed persistence, and export contracts."""


from __future__ import annotations


from tests._support_coding_agent import *  # noqa: F403


def test_agent_session_execute_bash_records_message_and_session(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(tmp_path / "session.jsonl"))
    chunks: list[str] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        assert command == "printf hi"
        assert cwd == str(tmp_path)
        options.on_data(b"hi")
        return {"exit_code": 0}

    result = session.execute_bash(
        "printf hi",
        chunks.append,
        {"operations": BashOperations(exec=exec_command)},
    )

    assert result.output == "hi"
    assert chunks == ["hi"]
    assert session.messages[-1].role == "bashExecution"
    assert session.messages[-1].command == "printf hi"
    assert session.messages[-1].output == "hi"
    assert session.session_entries[-1]["message"]["role"] == "bashExecution"


def test_travis234_execute_bash_with_operations_is_public_and_sanitizes_streamed_output(tmp_path: Path) -> None:
    from travis.coding_agent import execute_bash_with_operations

    chunks: list[str] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        assert command == "printf hi"
        assert cwd == str(tmp_path)
        options.on_data(b"\x1b[31mhi\x1b[0m\x00\n")
        return {"exit_code": 0}

    result = execute_bash_with_operations(
        "printf hi",
        str(tmp_path),
        BashOperations(exec=exec_command),
        {"onChunk": chunks.append},
    )

    assert result.output == "hi\n"
    assert chunks == ["hi\n"]
    assert result.exit_code == 0
    assert result.cancelled is False
    assert result.truncated is False
    assert result.full_output_path is None


def test_travis234_experimental_feature_gate_uses_travis234_experimental_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent import are_experimental_features_enabled

    monkeypatch.delenv("TRAVIS234_EXPERIMENTAL", raising=False)
    assert are_experimental_features_enabled() is False

    monkeypatch.setenv("TRAVIS234_EXPERIMENTAL", "0")
    assert are_experimental_features_enabled() is False

    monkeypatch.setenv("TRAVIS234_EXPERIMENTAL", "1")
    assert are_experimental_features_enabled() is True


def test_travis234_create_synthetic_source_info_uses_canonical_keyword_arguments() -> None:
    from travis.coding_agent import SourceInfo, create_synthetic_source_info

    explicit = create_synthetic_source_info(
        "tools/example.ts",
        source="extension",
        scope="project",
        origin="package",
        base_dir="/repo/.travis234/extensions/example",
    )

    assert explicit == SourceInfo(
        path="tools/example.ts",
        source="extension",
        scope="project",
        origin="package",
        base_dir="/repo/.travis234/extensions/example",
    )
    assert explicit.base_dir == "/repo/.travis234/extensions/example"

    defaulted = create_synthetic_source_info("inline", source="sdk")
    assert defaulted.scope == "temporary"
    assert defaulted.origin == "top-level"
    assert defaulted.base_dir is None


def test_travis234_compaction_result_public_shape() -> None:
    from travis.coding_agent import CompactionResult

    result = CompactionResult(
        summary="summary",
        first_kept_entry_id="entry-2",
        tokens_before=1234,
        details={"kind": "artifact-index"},
    )

    assert result.summary == "summary"
    assert result.first_kept_entry_id == "entry-2"
    assert result.tokens_before == 1234
    assert result.details == {"kind": "artifact-index"}


def test_agent_session_execute_bash_applies_travis234_command_prefix_but_records_original(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    seen: dict[str, str] = {}

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        seen["command"] = command
        seen["cwd"] = cwd
        options.on_data(b"ok")
        return {"exit_code": 0}

    result = session.execute_bash(
        "printf hi",
        options={
            "operations": BashOperations(exec=exec_command),
            "commandPrefix": "source ~/.profile",
        },
    )

    assert seen == {"command": "source ~/.profile\nprintf hi", "cwd": str(tmp_path)}
    assert result.output == "ok"
    assert session.messages[-1].command == "printf hi"


def test_agent_session_uses_travis234_settings_manager_for_built_in_and_user_bash(tmp_path: Path) -> None:
    class ShellSettings:
        def get_shell_command_prefix(self) -> str:
            return "printf settings-prefix;"

        def get_shell_path(self) -> None:
            return None

    session = AgentSession(cwd=str(tmp_path), model=faux_model(), settings_manager=ShellSettings())
    bash_definition = session.get_tool_definition("bash")
    assert bash_definition is not None

    tool_result = bash_definition.execute("c1", {"command": "printf tool"})
    user_result = session.execute_bash("printf user")

    assert tool_result.content[0].text == "settings-prefixtool"
    assert user_result.output == "settings-prefixuser"
    assert session.messages[-1].command == "printf user"


def test_agent_session_execute_bash_uses_travis234_shell_path_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_create_local_bash_operations(shell_path: str | None = None) -> BashOperations:
        captured["shell_path"] = shell_path
        return BashOperations(exec=lambda command, cwd, options: {"exit_code": 0})

    monkeypatch.setattr(
        "travis.coding_agent.session_bash.create_local_bash_operations",
        fake_create_local_bash_operations,
    )

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.execute_bash("true", options={"shellPath": "/bin/zsh"})

    assert captured == {"shell_path": "/bin/zsh"}
    assert session.messages[-1].command == "true"


def test_coding_agent_package_exports_bash_result() -> None:
    from travis.coding_agent import BashResult as ExportedBashResult

    assert ExportedBashResult is BashResult


def test_agent_session_defers_bash_result_while_streaming_then_flushes(tmp_path: Path) -> None:
    model = faux_model()
    session_path = tmp_path / "session.jsonl"
    stream_started = threading.Event()
    release_stream = threading.Event()

    def stream_fn(model, context, options):
        events = text_response_events(model, "streamed response")
        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        stream_started.set()

        def finish() -> None:
            release_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    run_error: list[BaseException] = []

    def run_prompt() -> None:
        try:
            session.prompt("start", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert stream_started.wait(timeout=2)
    assert session.is_streaming is True

    session.record_bash_result(
        "echo hi",
        BashResult(output="hi", exit_code=0, cancelled=False, truncated=False),
    )

    assert session.has_pending_bash_messages is True
    assert not any(message.role == "bashExecution" for message in session.messages)
    assert not any(entry.get("message", {}).get("role") == "bashExecution" for entry in session.session_entries)

    release_stream.set()
    thread.join(timeout=2)

    assert run_error == []
    assert session.has_pending_bash_messages is False
    assert any(message.role == "bashExecution" for message in session.messages)
    assert session.session_entries[-1]["message"]["role"] == "bashExecution"


def test_agent_session_abort_bash_cancels_running_command(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    command_started = threading.Event()
    result_holder: list[BashResult] = []
    errors: list[BaseException] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        command_started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if options.signal and options.signal.aborted:
                raise RuntimeError("aborted")
            time.sleep(0.005)
        return {"exit_code": 0}

    def run_bash() -> None:
        try:
            result_holder.append(
                session.execute_bash(
                    "sleep",
                    options={"operations": BashOperations(exec=exec_command)},
                )
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    thread = threading.Thread(target=run_bash)
    thread.start()
    assert command_started.wait(timeout=2)
    assert session.is_bash_running is True

    session.abort_bash()
    thread.join(timeout=2)

    assert errors == []
    assert len(result_holder) == 1
    assert result_holder[0].cancelled is True
    assert session.is_bash_running is False


def test_agent_session_abort_bash_cancels_every_concurrent_command(
    tmp_path: Path,
) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    started = {
        "first": threading.Event(),
        "second": threading.Event(),
    }
    results: dict[str, BashResult] = {}
    errors: list[BaseException] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        del cwd
        started[command].set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if options.signal and options.signal.aborted:
                raise RuntimeError("aborted")
            time.sleep(0.005)
        return {"exit_code": 0}

    def run(command: str) -> None:
        try:
            results[command] = session.execute_bash(
                command,
                options={"operations": BashOperations(exec=exec_command)},
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [
        threading.Thread(target=run, args=("first",)),
        threading.Thread(target=run, args=("second",)),
    ]
    for thread in threads:
        thread.start()

    assert started["first"].wait(timeout=2)
    assert started["second"].wait(timeout=2)
    assert session.is_bash_running is True

    session.abort_bash()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert set(results) == {"first", "second"}
    assert all(result.cancelled is True for result in results.values())
    assert session.is_bash_running is False


def test_agent_session_bash_completion_keeps_other_command_registered(
    tmp_path: Path,
) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    started = {
        "first": threading.Event(),
        "second": threading.Event(),
    }
    release = {
        "first": threading.Event(),
        "second": threading.Event(),
    }

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        del cwd, options
        started[command].set()
        assert release[command].wait(timeout=2)
        return {"exit_code": 0}

    threads = [
        threading.Thread(
            target=session.execute_bash,
            args=(command,),
            kwargs={"options": {"operations": BashOperations(exec=exec_command)}},
        )
        for command in ("first", "second")
    ]
    for thread in threads:
        thread.start()

    assert all(event.wait(timeout=2) for event in started.values())
    assert session.is_bash_running is True
    release["first"].set()
    threads[0].join(timeout=2)

    assert not threads[0].is_alive()
    assert threads[1].is_alive()
    assert session.is_bash_running is True

    release["second"].set()
    threads[1].join(timeout=2)
    assert not threads[1].is_alive()
    assert session.is_bash_running is False


def test_agent_session_bash_error_unregisters_its_signal(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        del command, cwd, options
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        session.execute_bash(
            "fail",
            options={"operations": BashOperations(exec=exec_command)},
        )

    assert session.is_bash_running is False


def test_agent_session_auto_retry_events_for_transient_provider_error(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        if calls["n"] == 1:
            stream = create_assistant_message_event_stream()
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message="Provider finish_reason: network_error",
            )
            stream.push(ErrorEvent(reason="error", error=error))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.prompt("Test", stream_fn=stream_fn)

    assert calls["n"] == 2
    retry_events = [event for event in events if event.type.startswith("auto_retry_")]
    assert retry_events[0].type == "auto_retry_start"
    assert retry_events[0].attempt == 1
    assert retry_events[0].max_attempts == 2
    assert retry_events[0].delay_ms == 0
    assert retry_events[0].error_message == "Provider finish_reason: network_error"
    assert retry_events[1].type == "auto_retry_end"
    assert retry_events[1].success is True
    assert retry_events[1].attempt == 1
    assert session.retry_attempt == 0


@pytest.mark.parametrize(
    "error_message",
    [
        "getaddrinfo failed for provider.example",
        "request failed with ENOTFOUND provider.example",
        "temporary DNS failure: EAI_AGAIN",
    ],
)
def test_agent_session_retries_dns_resolution_failures(
    tmp_path: Path,
    error_message: str,
) -> None:
    model = faux_model()
    calls = {"count": 0}

    def stream_fn(model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            stream = create_assistant_message_event_stream()
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message=error_message,
            )
            stream.push(ErrorEvent(reason="error", error=error))
            return stream
        return create_faux_provider(
            lambda active_model, active_context: text_response_events(
                active_model,
                "Recovered",
            )
        ).stream_simple(model, context, options)

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=1,
        retry_delay_ms=0,
    )

    session.prompt("Test DNS retry", stream_fn=stream_fn)

    assert calls["count"] == 2


def test_agent_session_auto_retry_adds_malformed_tool_args_correction_context(tmp_path: Path) -> None:
    model = faux_model()
    captured_contexts: list[Context] = []

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message=(
                    "Stream ended with malformed streamed tool-call arguments "
                    "for write; dropped tool call before dispatch."
                ),
            )
            stream.push(ErrorEvent(reason="error", error=error))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )

    session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert len(captured_contexts) == 2
    retry_user_messages = [
        message.content
        for message in captured_contexts[1].messages
        if getattr(message, "role", None) == "user" and isinstance(message.content, str)
    ]
    recovery = retry_user_messages[-1]
    assert "malformed streamed tool-call arguments" in recovery
    assert "write" in recovery
    assert "Do not retry the same malformed tool call" in recovery
    assert "protocol-looking literal" in recovery
    assert "Retry the write tool" in recovery
    assert "JSON unicode escapes" in recovery
    assert "change strategy with available tools" in recovery
    assert "base64" not in recovery
    assert "content_escaped" not in recovery


def test_agent_session_continues_partial_stream_dropped_tool_calls_with_chunk_guidance(tmp_path: Path) -> None:
    model = faux_model()
    captured_contexts: list[object] = []

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            partial = AssistantMessage(
                content=[TextContent(text="I will write the fixture.")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="length",
                response_id="partial-stream-stub",
                diagnostics=[
                    {
                        "code": "partial_stream_dropped_tool_calls",
                        "dropped_tool_names": ["bash"],
                        "finish_reason": "tool_calls",
                    }
                ],
            )
            stream.push(DoneEvent(reason="length", message=partial))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )

    messages = session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert len(captured_contexts) == 2
    assert any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "stop"
        and _content_text(message.content) == "Recovered"
        for message in messages
    )
    follow_up = captured_contexts[1].messages[-1]
    assert isinstance(follow_up, UserMessage)
    assert isinstance(follow_up.content, list)
    follow_up_text = _content_text(follow_up.content)
    assert "previous tool call (bash)" in follow_up_text
    assert "Do NOT retry the same tool call" in follow_up_text
    assert "Retry the write tool" in follow_up_text
    assert "JSON unicode escapes" in follow_up_text
    assert "change strategy with available tools" in follow_up_text
    assert "write smaller files" not in follow_up_text
    assert "base64" not in follow_up_text
    assert "content_escaped" not in follow_up_text


def test_agent_session_continues_malformed_streamed_mutating_tool_args_with_recovery_guidance(tmp_path: Path) -> None:
    model = faux_model()
    captured_contexts: list[object] = []

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            malformed = AssistantMessage(
                content=[TextContent(text="I will write the fixture.")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="length",
                response_id="partial-stream-stub",
                diagnostics=[
                    {
                        "code": "malformed_streamed_tool_call_arguments",
                        "dropped_tool_names": ["write"],
                        "finish_reason": "tool_calls",
                    }
                ],
            )
            stream.push(DoneEvent(reason="length", message=malformed))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )

    messages = session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert len(captured_contexts) == 2
    assert any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "stop"
        and _content_text(message.content) == "Recovered"
        for message in messages
    )
    follow_up = captured_contexts[1].messages[-1]
    assert isinstance(follow_up, UserMessage)
    follow_up_text = _content_text(follow_up.content)
    assert "malformed streamed tool-call arguments" in follow_up_text
    assert "This is a tool-argument formatting failure" in follow_up_text
    assert "Do not retry the same malformed tool call" in follow_up_text
    assert "Retry the write tool" in follow_up_text
    assert "JSON unicode escapes" in follow_up_text
    assert "change strategy with available tools" in follow_up_text
    assert "too large or malformed" not in follow_up_text
    assert "base64" not in follow_up_text
    assert "content_escaped" not in follow_up_text


def test_agent_session_internal_malformed_stream_recovery_does_not_trigger_user_process_limit(
    tmp_path: Path,
) -> None:
    model = faux_model()
    captured_contexts: list[object] = []
    bash_executions: list[dict] = []
    command = {"command": "echo '# Protocol Fixture"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[TextContent(text="zsh:1: unmatched '\nCommand exited with code 1")],
            details={},
        )

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute_bash,
    )

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            malformed = AssistantMessage(
                content=[TextContent(text="I will write the fixture.")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="length",
                response_id="partial-stream-stub",
                diagnostics=[
                    {
                        "code": "malformed_streamed_tool_call_arguments",
                        "dropped_tool_names": ["write"],
                        "finish_reason": "tool_calls",
                    }
                ],
            )
            stream.push(DoneEvent(reason="length", message=malformed))
            return stream
        if len(captured_contexts) == 2:
            return create_faux_provider(
                lambda m, c: tool_call_response_events(m, "bash", command, call_id="bad_bash")
            ).stream_simple(model, context, options)
        return create_faux_provider(
            lambda m, c: text_response_events(m, "I can continue after the failed bash fallback.")
        ).stream_simple(model, context, options)

    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    messages = session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert bash_executions == [command]
    assert len(captured_contexts) == 3
    tool_results = [message for message in session.messages if getattr(message, "role", None) == "toolResult"]
    assert "user_process_limit" not in _content_text(tool_results[-1].content)
    assert messages[-1].role == "assistant"
    assert _content_text(messages[-1].content) == "I can continue after the failed bash fallback."


def test_agent_session_does_not_retry_travis234_non_retryable_provider_limit_errors(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message="rate limit: insufficient_quota billing after EAI_AGAIN",
        )
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.prompt("Test", stream_fn=stream_fn)

    assert calls["n"] == 1
    assert [event.type for event in events if event.type.startswith("auto_retry_")] == []


def test_agent_session_auto_retry_exhaustion_emits_failure(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message=f"network_error attempt {calls['n']}",
        )
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.prompt("Test", stream_fn=stream_fn)

    assert calls["n"] == 3
    retry_events = [event for event in events if event.type.startswith("auto_retry_")]
    assert [event.type for event in retry_events] == [
        "auto_retry_start",
        "auto_retry_start",
        "auto_retry_end",
    ]
    assert [event.attempt for event in retry_events] == [1, 2, 2]
    assert retry_events[-1].success is False
    assert retry_events[-1].final_error == "network_error attempt 3"
    assert session.retry_attempt == 0


def test_agent_session_auto_retry_facade_toggles_retry_setting(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        retry_enabled=True,
        max_retries=1,
        retry_delay_ms=0,
    )

    assert session.auto_retry_enabled is True
    assert session.is_retrying is False

    session.set_auto_retry_enabled(False)
    assert session.auto_retry_enabled is False

    session.set_auto_retry_enabled(True)
    assert session.auto_retry_enabled is True


def test_agent_session_abort_retry_cancels_retry_delay(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}
    retry_started = threading.Event()
    prompt_finished = threading.Event()
    errors: list[BaseException] = []

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message="network_error before retry",
        )
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=1000,
    )
    events: list[object] = []

    def handle_event(event: object) -> None:
        events.append(event)
        if getattr(event, "type", None) == "auto_retry_start":
            retry_started.set()

    session.subscribe(handle_event)

    def run_prompt() -> None:
        try:
            session.prompt("Test", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        finally:
            prompt_finished.set()

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert retry_started.wait(timeout=2)
    assert session.is_retrying is True

    session.abort_retry()
    thread.join(timeout=2)

    assert prompt_finished.is_set()
    assert errors == []
    assert calls["n"] == 1
    retry_events = [event for event in events if getattr(event, "type", "").startswith("auto_retry_")]
    assert [event.type for event in retry_events] == ["auto_retry_start", "auto_retry_end"]
    assert retry_events[-1].success is False
    assert retry_events[-1].attempt == 1
    assert retry_events[-1].final_error == "Retry cancelled"
    assert session.retry_attempt == 0
    assert session.is_retrying is False


def test_agent_session_persists_and_reloads_typed_session_entries(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    model = faux_model()
    model.reasoning = True

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("hello")
    session.set_session_name("persisted name")
    session.set_thinking_level("high")
    replacement = faux_model()
    replacement.id = "replacement"
    replacement.provider = "faux"
    session.set_model(replacement)

    entries = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["type"] == "session"
    assert [entry["type"] for entry in entries[1:]] == [
        "message",
        "message",
        "session_info",
        "thinking_level_change",
        "model_change",
        "thinking_level_change",
    ]
    assert entries[-2]["provider"] == "faux"
    assert entries[-2]["modelId"] == "replacement"
    assert entries[-1]["thinkingLevel"] == "off"

    restored = AgentSession(cwd=str(tmp_path), model=replacement, session_path=str(session_path))

    assert restored.session_name == "persisted name"
    assert restored.thinking_level == "off"
    assert [getattr(message, "role", None) for message in restored.messages] == ["user", "assistant"]
    assert restored.messages[0].content == [TextContent(text="hello")]
    assert restored.messages[1].content[0].text == "reply"


def test_agent_session_branch_repoints_leaf_and_persists_new_child(tmp_path: Path) -> None:
    session_path = tmp_path / "branch-session.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply", "branch reply"])

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    branch_point = session.session_entries[-1]["id"]
    session.prompt("second")

    assert [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in session.messages
        if isinstance(message, UserMessage)
    ] == ["first", "second"]

    session.branch(branch_point)
    session.prompt("branch")

    assert [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in session.messages
        if isinstance(message, UserMessage)
    ] == ["first", "branch"]
    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    branch_user = next(
        entry
        for entry in persisted
        if entry["type"] == "message"
        and entry["message"]["content"] == [{"type": "text", "text": "branch", "textSignature": None}]
    )
    assert branch_user["parentId"] == branch_point

    restored = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    assert [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in restored.messages
        if isinstance(message, UserMessage)
    ] == ["first", "branch"]


def test_agent_session_export_to_jsonl_writes_active_branch_with_linear_parent_ids(tmp_path: Path) -> None:
    session_path = tmp_path / "export-source.jsonl"
    export_path = tmp_path / "exports" / "active-branch.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply", "branch reply"])

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    branch_point = session.session_entries[-1]["id"]
    session.prompt("second")
    session.branch(branch_point)
    session.prompt("branch")

    returned_path = session.export_to_jsonl(str(export_path))

    assert returned_path == str(export_path)
    assert session.export_to_jsonl(str(tmp_path / "exports" / "active-branch-alias.jsonl")).endswith(
        "active-branch-alias.jsonl"
    )
    exported = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]
    assert exported[0]["type"] == "session"
    assert exported[0]["id"] == session.session_id
    assert exported[0]["cwd"] == str(tmp_path)
    assert [
        entry["message"]["content"]
        for entry in exported[1:]
        if entry["type"] == "message" and entry["message"]["role"] == "user"
    ] == [
        _serialized_text_content("first"),
        _serialized_text_content("branch"),
    ]
    assert "second" not in json.dumps(exported)

    previous_id = None
    for entry in exported[1:]:
        assert entry.get("parentId") == previous_id
        previous_id = entry["id"]


def test_agent_session_export_to_html_writes_standalone_session_view(tmp_path: Path) -> None:
    session_path = tmp_path / "html-source.jsonl"
    export_path = tmp_path / "exports" / "session.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply <ok>")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("hello <world>")

    returned_path = session.export_to_html(str(export_path))

    assert returned_path == str(export_path)
    assert session.export_to_html(str(tmp_path / "exports" / "session-alias.html")).endswith("session-alias.html")
    html = export_path.read_text(encoding="utf-8")
    assert "<title>Session Export</title>" in html
    assert 'id="session-data"' in html
    assert 'id="messages"' in html
    assert "hello &lt;world&gt;" not in html
    assert "reply &lt;ok&gt;" not in html
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert session_data["header"]["id"] == session.session_id
    assert session_data["header"]["cwd"] == str(tmp_path)
    assert session_data["leafId"] == session.session_entries[-1]["id"]
    assert [entry["type"] for entry in session_data["entries"]] == ["message", "message"]
    assert session_data["entries"][0]["message"]["content"] == _serialized_text_content("hello <world>")
    assert session_data["entries"][1]["message"]["content"][0]["text"] == "reply <ok>"
    assert "Available tools:" in session_data["systemPrompt"]
    assert any(tool["name"] == "read" for tool in session_data["tools"])


def test_agent_session_export_to_html_uses_travis234_browser_shell_contract(tmp_path: Path) -> None:
    session_path = tmp_path / "html-shell.jsonl"
    export_path = tmp_path / "exports" / "shell.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("hello")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert 'id="hamburger"' in html
    assert 'id="sidebar-overlay"' in html
    assert 'id="tree-search"' in html
    assert 'data-filter="no-tools"' in html
    assert 'id="tree-container"' in html
    assert 'id="tree-status"' in html
    assert 'id="sidebar-resizer"' in html
    assert 'id="image-modal"' in html
    assert "const base64 = document.getElementById('session-data').textContent;" in html
    assert "new TextDecoder('utf-8').decode(bytes)" in html
    assert "const { header, entries, leafId: defaultLeafId, systemPrompt, tools, renderedTools } = data;" in html
    assert "new URLSearchParams" in html
    assert "function buildTree()" in html
    assert "function getPath(targetId)" in html


def test_agent_session_export_to_html_uses_travis234_theme_and_layout_tokens(tmp_path: Path) -> None:
    session_path = tmp_path / "html-theme.jsonl"
    export_path = tmp_path / "exports" / "theme.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("theme")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "--line-height: 18px;" in html
    assert "--dim:" in html
    assert "--selectedBg:" in html
    assert "--borderAccent:" in html
    assert "--customMessageBg:" in html
    assert "--userMessageBg:" in html
    assert "--toolPendingBg:" in html
    assert "font-size: 12px;" in html
    assert "line-height: var(--line-height);" in html
    assert "border-right: 1px solid var(--dim);" in html
    assert "background: var(--selectedBg);" in html
    assert "padding: var(--line-height) calc(var(--line-height) * 2);" in html
    assert "align-items: center;" in html
    assert "#content > *" in html
    assert "max-width: 800px;" in html
