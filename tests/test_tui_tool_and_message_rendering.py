"""TUI tool, message, and command rendering contracts."""


from __future__ import annotations


from tests._support_tui import *  # noqa: F403


def test_assistant_markdown_thinking_error_and_narrow_wrapping() -> None:
    message = AssistantMessage(
        content=[
            ThinkingContent(thinking="checking **state**"),
            TextContent(text="# Result\n- wrapped text for a narrow terminal"),
        ],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    from travis.tui import AssistantMessageComponent

    assistant = AssistantMessageComponent(message)
    rendered = assistant.render(18)

    joined = "\n".join(rendered)
    assert "Thinking:" in joined
    assert "checking state" in joined
    assert "Result" in joined
    assert all(visible_width(line) <= 18 for line in rendered)

    error_message = AssistantMessage(
        content=[],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason="error",
        error_message="boom",
        timestamp=now_ms(),
    )
    assert "Error: boom" in "\n".join(AssistantMessageComponent(error_message).render(40))


def test_tool_execution_uses_render_hooks_collapsed_expanded_and_narrow_width(tmp_path) -> None:
    definition = create_read_tool_definition(str(tmp_path))
    component = ToolExecutionComponent(
        "read",
        "call-1",
        {"path": str(tmp_path / "attio" / "SKILL.md"), "offset": 12, "limit": 3},
        tool_definition=definition,
        cwd=str(tmp_path),
    )
    collapsed = "\n".join(component.render(30))

    assert "[skill] attio:12-14" in collapsed
    assert "to expand" in collapsed.replace("\n", " ")

    result = AgentToolResult(content=[TextContent(text="hidden skill body")], details=None)
    component.update_result(result, is_error=False)
    assert "hidden skill body" not in "\n".join(component.render(30))

    component.set_expanded(True)
    expanded = component.render(30)
    assert "hidden skill body" in "\n".join(expanded)
    assert all(visible_width(line) <= 30 for line in expanded)


def test_read_tool_render_tolerates_unvalidated_model_numeric_strings(tmp_path) -> None:
    definition = create_read_tool_definition(str(tmp_path))
    component = ToolExecutionComponent(
        "read",
        "call-1",
        {"path": "src/agents/facebook_surfer.py", "limit": "100.0"},
        tool_definition=definition,
        cwd=str(tmp_path),
    )

    rendered = "\n".join(component.render(80))

    assert "read src/agents/facebook_surfer.py" in rendered


def test_tool_execution_accepts_component_render_call_like_travis234() -> None:
    long_path = "/workspace/demo_okf_bundle/spec/final-important-suffix.md"

    definition = ToolDefinition(
        name="write",
        label="Write",
        description="Write file",
        parameters={},
        execute=lambda *args, **kwargs: AgentToolResult(content=[]),
        render_call=lambda args, ctx: Text(f"write {args['path']}"),
    )
    component = ToolExecutionComponent(
        "write",
        "call-1",
        {"path": long_path},
        tool_definition=definition,
        cwd="/workspace",
    )

    rendered = component.render(24)
    joined = "\n".join(rendered)

    assert "suffix.md" in joined
    assert all(visible_width(line) <= 24 for line in rendered)


def test_tool_execution_long_call_header_stays_single_stable_line() -> None:
    long_path = "/workspace/demo_okf_bundle/spec/very/deep/final-important-suffix.md"

    definition = ToolDefinition(
        name="write",
        label="Write",
        description="Write file",
        parameters={},
        execute=lambda *args, **kwargs: AgentToolResult(content=[]),
        render_call=lambda args, ctx: Text(f"write {args['path']}"),
    )
    component = ToolExecutionComponent(
        "write",
        "call-1",
        {"path": long_path},
        tool_definition=definition,
        cwd="/workspace",
    )

    rendered = component.render(32)

    assert len(rendered) == 1
    assert "suffix.md" in rendered[0]
    assert "very/deep" not in rendered[0]
    assert visible_width(rendered[0]) <= 32


def test_tool_execution_accepts_component_render_result_like_travis234() -> None:
    definition = ToolDefinition(
        name="read",
        label="Read",
        description="Read file",
        parameters={},
        execute=lambda *args, **kwargs: AgentToolResult(content=[]),
        render_call=lambda args, ctx: f"read {args['path']}",
        render_result=lambda result, metadata, context: Text(
            "[ok] first wrapped result line with final-important-suffix.md"
        ),
    )
    component = ToolExecutionComponent(
        "read",
        "call-1",
        {"path": "notes.md"},
        tool_definition=definition,
        cwd="/workspace",
    )
    component.update_result(AgentToolResult(content=[]), is_error=False)

    rendered = component.render(28)
    joined = "\n".join(rendered)

    assert "final-important-suffix.md" in joined
    assert all(visible_width(line) <= 28 for line in rendered)


def test_tool_execution_collapses_long_generic_results_until_expanded() -> None:
    component = ToolExecutionComponent("bash", {"command": "find ."})
    result = AgentToolResult(
        content=[TextContent(text="\n".join(f"line {index}" for index in range(12)))],
        details=None,
    )

    component.update_result(result, is_error=False)
    collapsed = "\n".join(component.render(80))

    assert "line 0" in collapsed
    assert "line 10" not in collapsed
    assert "... (2 more lines, to expand)" in collapsed

    component.set_expanded(True)
    assert "line 11" in "\n".join(component.render(80))


def test_tool_execution_collapses_huge_single_line_generic_result_before_rendering() -> None:
    component = ToolExecutionComponent("huge", {})
    result = AgentToolResult(content=[TextContent(text="x" * 80_000)], details=None)

    component.update_result(result, is_error=False)
    rendered = "\n".join(component.render(80))

    assert "more chars, to expand" in rendered
    assert len(rendered) < 8_000


def test_tool_execution_fallback_never_renders_process_stdin_payload() -> None:
    component = ToolExecutionComponent(
        "process",
        {
            "action": "write",
            "session_id": "proc_0123456789abcdef",
            "input": "TOP-SECRET-PAYLOAD",
        },
    )

    rendered = "\n".join(component.render(80))

    assert "process write proc_01234567" in rendered
    assert "TOP-SECRET-PAYLOAD" not in rendered


def test_tool_execution_uses_process_definition_renderer_for_wait_metadata() -> None:
    definition = SimpleNamespace(
        render_call=lambda args, ctx: (
            f"process {args['action']} {args['session_id'][:13]} "
            f"cursor={args['cursor']} wait={args['wait_time_ms']}ms"
        ),
        render_result=None,
    )
    component = ToolExecutionComponent(
        "process",
        {
            "action": "wait",
            "session_id": "proc_0123456789abcdef",
            "cursor": 8,
            "wait_time_ms": 60_000,
        },
        tool_definition=definition,
    )

    rendered = "\n".join(component.render(100))

    assert "process wait proc_01234567 cursor=8 wait=60000ms" in rendered


def test_tool_execution_renders_stable_running_process_marker() -> None:
    component = ToolExecutionComponent("bash", {"command": "sleep 30"})
    component.update_result(
        AgentToolResult(
            content=[TextContent(text="START\n")],
            details={
                "status": "running",
                "sessionId": "proc_0123456789abcdef0123456789abcdef",
            },
        ),
        is_error=False,
    )

    rendered = "\n".join(component.render(80))

    assert "running: proc_01234567" in rendered


def test_user_and_skill_invocation_components_render_like_travis234() -> None:
    from travis.tui import SkillInvocationMessageComponent, UserMessageComponent, parse_skill_block

    user = UserMessageComponent("hello **user**")
    rendered_user = user.render(80)
    assert rendered_user[0].startswith("\x1b]133;A\x07")
    assert "\x1b]133;B\x07\x1b]133;C\x07" in rendered_user[-1]
    assert "hello user" in strip_ansi("\n".join(rendered_user))
    assert "> hello" not in strip_ansi("\n".join(rendered_user))

    parsed = parse_skill_block(
        '<skill name="python" location="/skills/python/SKILL.md">\n'
        "Use pytest first.\n"
        "</skill>\n\n"
        "Apply it to the TUI."
    )
    assert parsed is not None
    assert parsed.name == "python"
    assert parsed.location.endswith("SKILL.md")
    assert parsed.user_message == "Apply it to the TUI."

    skill = SkillInvocationMessageComponent(parsed)
    collapsed = strip_ansi("\n".join(skill.render(80)))
    assert "[skill] python" in collapsed
    assert "Use pytest first." not in collapsed

    skill.set_expanded(True)
    expanded = strip_ansi("\n".join(skill.render(80)))
    assert "python" in expanded
    assert "Use pytest first." in expanded


def test_message_to_component_splits_skill_block_from_user_message() -> None:
    from travis.tui import message_to_component

    message = UserMessage(
        content=(
            '<skill name="tui" location="/skills/tui/SKILL.md">\n'
            "Render with boxes.\n"
            "</skill>\n\n"
            "Now update travis."
        ),
        timestamp=now_ms(),
    )

    component = message_to_component(message)
    assert component is not None
    rendered = strip_ansi("\n".join(component.render(100)))

    assert "[skill] tui" in rendered
    assert "Render with boxes." not in rendered
    assert "Now update travis." in rendered
    assert "> Now update" not in rendered


def test_bash_execution_component_renders_status_and_output() -> None:
    from travis.tui import BashExecutionComponent, message_to_component

    component = BashExecutionComponent("printf hi")
    initial = strip_ansi("\n".join(component.render(80)))
    assert "$ printf hi" in initial
    assert "Running" in initial

    component.append_output("line 1\n" + "\n".join(f"line {index}" for index in range(2, 25)))
    component.set_complete(exit_code=2, cancelled=False, truncated=True, full_output_path="/tmp/full.log")
    collapsed = strip_ansi("\n".join(component.render(80)))
    assert "$ printf hi" in collapsed
    assert "line 24" in collapsed
    assert "|line 1 " not in collapsed
    assert "... 4 more lines" in collapsed
    assert "(exit 2)" in collapsed
    assert "Full output: /tmp/full.log" in collapsed

    component.set_expanded(True)
    expanded = strip_ansi("\n".join(component.render(80)))
    assert "line 1" in expanded

    excluded = BashExecutionMessage(
        command="secret",
        output="hidden",
        exit_code=0,
        cancelled=False,
        truncated=False,
        full_output_path=None,
        timestamp=now_ms(),
        exclude_from_context=True,
    )
    mapped = message_to_component(excluded)
    assert mapped is not None
    mapped_rendered = strip_ansi("\n".join(mapped.render(80)))
    assert "$ secret" in mapped_rendered
    assert "[no context]" in mapped_rendered
    assert "hidden" in mapped_rendered


def test_special_message_components_render_collapsed_and_expanded() -> None:
    from travis.tui import (
        BranchSummaryMessageComponent,
        CompactionSummaryMessageComponent,
        CustomMessageComponent,
    )

    branch = BranchSummaryMessage(summary="Changed `src/app.py` and kept tests green.", from_id="root", timestamp=now_ms())
    branch_component = BranchSummaryMessageComponent(branch)
    branch_collapsed = "\n".join(branch_component.render(80))
    assert "[branch]" in branch_collapsed
    assert "Branch summary" in branch_collapsed
    assert "src/app.py" not in branch_collapsed
    branch_component.set_expanded(True)
    branch_expanded = "\n".join(branch_component.render(80))
    assert "Branch Summary" in branch_expanded
    assert "src/app.py" in branch_expanded

    compaction = type(
        "CompactionSummary",
        (),
        {"role": "compactionSummary", "summary": "Historical context was compacted.", "tokensBefore": 12345},
    )()
    compaction_component = CompactionSummaryMessageComponent(compaction)
    compaction_collapsed = "\n".join(compaction_component.render(80))
    assert "[compaction]" in compaction_collapsed
    assert "12,345" in compaction_collapsed
    assert "Historical context" not in compaction_collapsed
    compaction_component.set_expanded(True)
    assert "Historical context was compacted." in "\n".join(compaction_component.render(80))

    custom = CustomMessage(
        custom_type="note",
        content=[TextContent(text="Remember **this** detail.")],
        display=True,
        details={"source": "extension"},
        timestamp=now_ms(),
    )
    custom_component = CustomMessageComponent(custom)
    custom_rendered = "\n".join(custom_component.render(80))
    assert "[note]" in custom_rendered
    assert "Remember this detail." in custom_rendered


def test_interactive_mode_renders_existing_special_messages(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        BranchSummaryMessage(summary="Returned from old branch.", from_id="root", timestamp=now_ms()),
        type(
            "CompactionSummary",
            (),
            {"role": "compactionSummary", "summary": "Older history compacted.", "tokensBefore": 16000},
        )(),
        CustomMessage(
            custom_type="context",
            content="Extension-provided context",
            display=True,
            details=None,
            timestamp=now_ms(),
        ),
    ]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()

    rendered = "\n".join(app.tui.render(120))
    assert "[branch]" in rendered
    assert "Branch summary" in rendered
    assert "[compaction]" in rendered
    assert "16,000" in rendered
    assert "[context]" in rendered
    assert "Extension-provided context" in rendered


def test_interactive_mode_uses_extension_custom_message_renderer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_message_renderer(
        "context",
        lambda message, options=None, theme=None: Text(f"custom rendered: {message.content}"),
    )
    app.session.agent.state.messages = [
        CustomMessage(
            custom_type="context",
            content="Extension-provided context",
            display=True,
            details=None,
            timestamp=now_ms(),
        ),
    ]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()

    rendered = "\n".join(app.tui.render(120))
    assert "custom rendered: Extension-provided context" in rendered
    assert "[context]" not in rendered


def test_interactive_mode_renders_live_custom_message_with_extension_renderer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_message_renderer(
        "context",
        lambda message, options=None, theme=None: Text(f"live custom rendered: {message.content}"),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    app.session.send_custom_message({"customType": "context", "content": "Fresh extension context", "display": True})

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert "live custom rendered: Fresh extension context" in rendered
    assert "[context]" not in rendered


def test_interactive_mode_runs_agents_command_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/agents", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "No subagents have been spawned" in rendered


def test_interactive_mode_runs_help_command_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/help", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "TUI commands" in rendered
    assert "/model" in rendered
    assert "model should not run" not in rendered
