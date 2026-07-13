from __future__ import annotations

from tests._support_coding_agent import *  # noqa: F403


def test_agent_session_halts_repeated_identical_successful_write_loop(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[str] = []
    repeated_args = {"path": "PROTOCOL_FIXTURE.md", "content": "line1 is"}

    def script(m, c):
        provider_calls["n"] += 1
        return tool_call_response_events(m, "write", repeated_args, call_id=f"call_{provider_calls['n']}")

    def write_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(args["content"])
        (tmp_path / args["path"]).write_text(args["content"], encoding="utf-8")
        return AgentToolResult(
            content=[TextContent(text=f"Successfully wrote {len(args['content'])} bytes to {args['path']}")],
            details={},
        )

    register_api_provider(create_faux_provider(script))
    write_definition = ToolDefinition(
        name="write",
        label="Write",
        description="write",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        execute=write_execute,
    )
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[write_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("write protocol fixture")

    assert executions == ["line1 is"] * 6
    assert provider_calls["n"] == 6
    assert session.messages[-1].role == "assistant"
    assert "mutating_no_progress_halt" in session.messages[-1].content[0].text
    assert (tmp_path / "PROTOCOL_FIXTURE.md").read_text(encoding="utf-8") == "line1 is"

def test_tool_failure_classifier_does_not_treat_read_source_error_tokens_as_failure() -> None:
    from travis.coding_agent.policies.tool_guardrails import classify_tool_failure

    source = 'import type { DraftAnalysis } from "../types";\nconst status = "error";\nconst failed = false;'

    failed, suffix = classify_tool_failure("read", source)

    assert failed is False
    assert suffix == ""

def test_tool_failure_classifier_keeps_explicit_tool_errors_as_failures() -> None:
    from travis.coding_agent.policies.tool_guardrails import classify_tool_failure

    failed, suffix = classify_tool_failure("read", "Error: File not found: src/app/EditorPanel.tsx")

    assert failed is True
    assert suffix == " [error]"

def test_agent_session_after_tool_call_does_not_mark_read_source_error_tokens_failed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from travis.agent.types import AgentToolResult
    from travis.ai.types import TextContent
    from travis.coding_agent.agent_session import AgentSession

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    source = 'import type { DraftAnalysis } from "../types";\nconst status = "error";\nconst failed = false;'
    context = SimpleNamespace(
        tool_call=SimpleNamespace(name="read", id="call-read-source"),
        args={"path": "src/lib/analyzer.ts"},
        result=AgentToolResult(content=[TextContent(text=source)], details=None),
        is_error=False,
    )

    try:
        after = session._after_tool_call(context)
    finally:
        session.shutdown()

    assert after is None or after.is_error is not True

def test_agent_session_missing_read_named_as_output_gets_write_recovery_guidance(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from travis.agent.types import AgentToolResult
    from travis.ai.types import TextContent
    from travis.coding_agent.agent_session import AgentSession

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    latest_user_prompt = (
        "After compact, read calc_notes.py and summarize whether divide is documented "
        "in NOTES_AFTER_COMPACT.md."
    )
    context = SimpleNamespace(
        tool_call=SimpleNamespace(name="read", id="call-read-missing-output"),
        args={"path": "NOTES_AFTER_COMPACT.md"},
        result=AgentToolResult(
            content=[TextContent(text="Error: File not found: NOTES_AFTER_COMPACT.md")],
            details=None,
        ),
        is_error=True,
        context=SimpleNamespace(
            messages=[
                SimpleNamespace(role="user", content=latest_user_prompt),
            ]
        ),
    )

    try:
        after = session._after_tool_call(context)
    finally:
        session.shutdown()

    assert after is not None
    assert after.content is not None
    text = _content_text(after.content)
    assert "output artifact" in text
    assert "use write to create it" in text
    assert after.terminate is not True

def test_tool_loop_guardrail_does_not_escalate_read_source_error_tokens_as_exact_failures() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(blocking_enabled=True))
    source = 'import type { DraftAnalysis } from "../types";\nconst status = "error";\nconst failed = false;'

    decisions = [
        controller.after_call("read", {"path": "src/lib/analyzer.ts"}, source, failed=None)
        for _ in range(4)
    ]

    assert [decision.action for decision in decisions] == ["allow", "warn", "halt", "halt"]
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert decisions[2].code == "idempotent_no_progress_block"
    assert decisions[3].code == "idempotent_no_progress_block"
    assert all("repeated_exact_failure" not in decision.code for decision in decisions)

def test_tool_loop_guardrail_treats_bash_file_preview_variants_as_no_progress() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(blocking_enabled=True))
    commands = [
        "head -400 src/agents/facebook_surfer.py | tail -100",
        "head -360 src/agents/facebook_surfer.py | tail -50",
        "awk 'NR>=330 && NR<=360' src/agents/facebook_surfer.py",
    ]

    decisions = [
        controller.after_call("bash", {"command": command}, "        # Configure model", failed=False)
        for command in commands
    ]

    assert decisions[0].action == "allow"
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert decisions[2].action == "halt"
    assert decisions[2].code == "idempotent_no_progress_block"
    assert "read with path/offset/limit" in decisions[2].message

def test_tool_loop_guardrail_keeps_bash_file_preview_memory_across_shell_mutations() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(blocking_enabled=True),
        cwd="/workspace",
    )

    first = controller.after_call(
        "bash",
        {"command": "cat docs/protocol_fixture.md"},
        "# Protocol Fixture",
        failed=False,
    )
    shell_write = controller.after_call(
        "bash",
        {"command": "printf '# Protocol Fixture\\n' > docs/protocol_fixture.md"},
        "(no output)",
        failed=False,
    )
    second = controller.after_call(
        "bash",
        {"command": "cat ./docs/protocol_fixture.md"},
        "# Protocol Fixture",
        failed=False,
    )
    shell_rewrite = controller.after_call(
        "bash",
        {"command": "printf '%s\\n' '# Protocol Fixture' '' '' '' > /workspace/docs/protocol_fixture.md"},
        "(no output)",
        failed=False,
    )
    third = controller.after_call(
        "bash",
        {"command": "cat /workspace/docs/protocol_fixture.md"},
        "# Protocol Fixture",
        failed=False,
    )

    assert first.action == "allow"
    assert shell_write.action == "allow"
    assert second.code == "idempotent_no_progress_warning"
    assert shell_rewrite.action == "allow"
    assert third.action == "halt"
    assert third.code == "idempotent_no_progress_block"

def test_tool_loop_guardrail_treats_bash_inventory_variants_as_no_progress() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(blocking_enabled=True))
    commands = [
        "ls -la src/metrics",
        "find src/metrics -maxdepth 1 -type f",
        "rg --files src/metrics",
    ]

    decisions = [
        controller.after_call("bash", {"command": command}, "src/metrics/models.py", failed=False)
        for command in commands
    ]

    assert decisions[0].action == "allow"
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert decisions[2].action == "halt"
    assert decisions[2].code == "idempotent_no_progress_block"
    assert "read with path/offset/limit" in decisions[2].message

def test_tool_loop_guardrail_treats_broad_python_repo_scan_variants_as_no_progress() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(blocking_enabled=True))
    commands = [
        "find . -type f -name '*.py'",
        "rg --files -g '*.py' .",
        "find ./ -name '*.py' -type f",
    ]

    decisions = [
        controller.after_call("bash", {"command": command}, "src/app.py\nsrc/tools/read.py", failed=False)
        for command in commands
    ]

    assert decisions[0].action == "allow"
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert decisions[2].action == "halt"
    assert decisions[2].code == "idempotent_no_progress_block"
    assert "For codebase scans" in decisions[2].message
    assert "treat listings/search output as inventory" in decisions[2].message
    assert "read with path/offset/limit" in decisions[2].message

def test_tool_loop_guardrail_allows_useful_followup_reads_but_warns_on_repeated_same_read() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailController

    controller = ToolCallGuardrailController()
    first = controller.after_call(
        "read",
        {"path": "src/a.py", "offset": 1, "limit": 40},
        "same body",
        failed=False,
    )
    useful_followup = controller.after_call(
        "read",
        {"path": "src/b.py", "offset": 1, "limit": 40},
        "same body",
        failed=False,
    )
    repeated = controller.after_call(
        "read",
        {"path": "src/a.py", "offset": 1, "limit": 40},
        "same body",
        failed=False,
    )

    assert first.action == "allow"
    assert useful_followup.action == "allow"
    assert repeated.action == "warn"
    assert repeated.code == "idempotent_no_progress_warning"
    assert "Use a different query/path only if the existing result is insufficient" in repeated.message

def test_tool_loop_guardrail_normalizes_bash_inventory_paths_against_cwd(tmp_path: Path) -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    project = tmp_path / "bot"
    project.mkdir()
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(blocking_enabled=True),
        cwd=str(project),
    )
    commands = [
        f"cd {project} && ls -la src/metrics/ 2>&1",
        f"find {project}/src/metrics -maxdepth 1 -type f",
        f"cd {project} && rg --files ./src/metrics",
    ]

    decisions = [
        controller.after_call("bash", {"command": command}, "src/metrics/models.py", failed=False)
        for command in commands
    ]

    assert decisions[0].action == "allow"
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert decisions[2].action == "halt"
    assert decisions[2].code == "idempotent_no_progress_block"
    assert "read with path/offset/limit" in decisions[2].message

def test_tool_loop_guardrail_ignores_unknown_bash_args_for_no_progress() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(blocking_enabled=True))
    calls = [
        {"command": "printf ok", "note": "first"},
        {"command": "printf ok", "note": "second"},
        {"command": "printf ok", "note": "third"},
    ]

    decisions = [controller.after_call("bash", args, "ok", failed=False) for args in calls]

    assert decisions[0].action == "allow"
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert decisions[2].action == "halt"
    assert decisions[2].code == "idempotent_no_progress_block"
    assert "same bash command" in decisions[2].message

def test_agent_session_appends_tool_loop_warning_to_repeated_bash_result(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="total 120")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] in (1, 2):
            return tool_call_response_events(m, "bash", {"command": "ls -la src/metrics"}, call_id=f"call_{provider_calls['n']}")
        return text_response_events(m, "stopped")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    session.prompt("inspect metrics")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    user_messages = [
        _content_text(message.content)
        for message in session.messages
        if getattr(message, "role", None) == "user"
    ]
    assert executions == [{"command": "ls -la src/metrics"}, {"command": "ls -la src/metrics"}]
    assert len(tool_results) == 2
    assert "total 120" in tool_results[1].content[0].text
    assert "idempotent_no_progress_warning" in tool_results[1].content[0].text
    assert "Use the result already provided" in tool_results[1].content[0].text
    assert tool_results[1].details["toolGuardrailWarnings"][0]["code"] == "idempotent_no_progress_warning"
    assert user_messages == ["inspect metrics"]


def test_agent_session_path_scoped_no_retry_does_not_stop_same_turn_recovery(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    write_executions: list[dict] = []

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[TextContent(text="preferred endpoint unavailable\nCommand exited with code 1")],
            details={},
        )

    def execute_write(tool_call_id, args, signal=None, on_update=None, ctx=None):
        write_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully wrote fallback.txt")], details={})

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
    write_definition = ToolDefinition(
        name="write",
        label="write",
        description="Write a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        execute=execute_write,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "bash",
                {"command": "curl https://preferred.invalid/data"},
                call_id="preferred_path",
            )
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "write",
                {"path": "fallback.txt", "content": "recovered"},
                call_id="fallback_path",
            )
        return text_response_events(m, "Recovered through the alternate path in the same turn.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition, write_definition],
    )

    session.prompt("Try the preferred endpoint; if it fails, do not retry that path. Use a fallback.")

    assert bash_executions == [{"command": "curl https://preferred.invalid/data"}]
    assert write_executions == [{"path": "fallback.txt", "content": "recovered"}]
    assert provider_calls["n"] == 3
    assert session.messages[-1].content[0].text == "Recovered through the alternate path in the same turn."

def test_agent_session_deduplicates_duplicate_bash_calls_in_same_turn(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[tuple[str, dict]] = []

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append((tool_call_id, dict(args)))
        return AgentToolResult(content=[TextContent(text=f"out:{args['command']}")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def multi_bash_events(model):
        calls = [
            ToolCall(id="call_1", name="bash", arguments={"command": "ls -la src/metrics"}),
            ToolCall(id="call_2", name="bash", arguments={"command": "ls -la src/metrics"}),
            ToolCall(id="call_3", name="bash", arguments={"command": "pwd"}),
        ]
        partial = AssistantMessage(
            content=list(calls),
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
        final = AssistantMessage(
            content=list(calls),
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
        events = [StartEvent(partial=partial)]
        for index, tool_call in enumerate(calls):
            events.append(ToolcallStartEvent(content_index=index, partial=partial))
            events.append(ToolcallEndEvent(content_index=index, tool_call=tool_call, partial=partial))
        events.append(DoneEvent(reason="toolUse", message=final))
        return events

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return multi_bash_events(m)
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    session.prompt("scan metrics")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assistant = next(m for m in session.messages if getattr(m, "role", None) == "assistant")
    assert executions == [
        ("call_1", {"command": "ls -la src/metrics"}),
        ("call_3", {"command": "pwd"}),
    ]
    assert [result.tool_call_id for result in tool_results] == ["call_1", "call_2", "call_3"]
    assert tool_results[1].is_error is True
    assert "Duplicate bash command in the same assistant turn" in tool_results[1].content[0].text
    assert [call.id for call in assistant.content if getattr(call, "type", None) == "toolCall"] == [
        "call_1",
        "call_2",
        "call_3",
    ]

def test_agent_session_appends_recovery_guidance_to_bash_no_progress_tool_result(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    seen_tool_results: list[list[str]] = []
    repeated_args = {"command": "ls -la src/metrics"}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        if args == repeated_args:
            return AgentToolResult(content=[TextContent(text="total 120")], details={})
        return AgentToolResult(content=[TextContent(text=f"diag:{args['command']}")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        tool_results = [
            _content_text(message.content)
            for message in c.messages
            if getattr(message, "role", None) == "toolResult"
        ]
        seen_tool_results.append(tool_results)
        if tool_results and "idempotent_no_progress_warning" in tool_results[-1]:
            return text_response_events(m, "I will use the first listing and read the relevant files.")
        if provider_calls["n"] % 2 == 0:
            return tool_call_response_events(m, "bash", repeated_args, call_id=f"call_{provider_calls['n']}")
        return tool_call_response_events(
            m,
            "bash",
            {"command": f"pwd && echo diag-{provider_calls['n']}"},
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    session.prompt("scan metrics and explain it")

    repeated_executions = [args for args in executions if args == repeated_args]
    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assistants = [m for m in session.messages if getattr(m, "role", None) == "assistant"]
    assert provider_calls["n"] == 5
    assert len(repeated_executions) == 2
    assert "idempotent_no_progress_warning" in tool_results[-1].content[0].text
    assert any("Tool loop warning" in results[-1] for results in seen_tool_results if results)
    assert assistants[-1].content[0].text == "I will use the first listing and read the relevant files."

def test_agent_session_failed_bash_respects_explicit_single_run_limit(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    process_limit_guidance: list[str] = []
    command = {"command": "python -m pytest tests/ -v"}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "FAILED tests/test_routecalc.py::test_return_to_start\n"
                        "Command exited with code 1"
                    )
                )
            ],
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
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        user_messages = [
            _content_text(message.content)
            for message in c.messages
            if getattr(message, "role", None) == "user"
        ]
        latest_user = user_messages[-1] if user_messages else ""
        if "user_process_limit" in latest_user:
            process_limit_guidance.append(latest_user)
            return text_response_events(m, "The single requested test run failed; I will report that result.")
        if provider_calls["n"] <= 2:
            return tool_call_response_events(m, "bash", command, call_id=f"call_{provider_calls['n']}")
        return text_response_events(m, "I retried without respecting the run limit.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    session.prompt("Run the test suite once only. Do not retry or run any other command. Just report the result.")

    assert executions == [command]
    assert provider_calls["n"] == 1
    assert process_limit_guidance == []
    assert session.messages[-1].role == "assistant"
    assert "single requested tool run failed" in session.messages[-1].content[0].text

def test_agent_session_failed_bash_plain_run_once_allows_followup_repair(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    edit_executions: list[dict] = []
    process_limit_guidance: list[str] = []
    command = {"command": "python -m pytest tests/"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "============================= test session starts ==============================\n"
                        "platform darwin -- Python 3.9.6\n"
                        + ("collection output\n" * 60)
                        + "TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType'\n"
                        "Command exited with code 2"
                    )
                )
            ],
            details={},
        )

    def execute_edit(tool_call_id, args, signal=None, on_update=None, ctx=None):
        edit_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully replaced 1 block(s).")], details={})

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
    edit_definition = ToolDefinition(
        name="edit",
        label="edit",
        description="Edit a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=execute_edit,
    )

    def script(m, c):
        provider_calls["n"] += 1
        user_messages = [
            _content_text(message.content)
            for message in c.messages
            if getattr(message, "role", None) == "user"
        ]
        latest_user = user_messages[-1] if user_messages else ""
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "bash", command, call_id="initial_pytest")
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "edit",
                {"path": "trailmix/core.py", "old": "list[float] | None", "new": "Optional[list[float]]"},
                call_id="edit_after_failed_pytest",
            )
        return text_response_events(m, "I inspected the failure and applied the targeted repair.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition, edit_definition])

    session.prompt(
        "Add optional segment_km to route_summary, update tests and README for this. "
        "Run the test suite once."
    )

    assert bash_executions == [command]
    assert edit_executions == [
        {"path": "trailmix/core.py", "old": "list[float] | None", "new": "Optional[list[float]]"}
    ]
    assert provider_calls["n"] == 3
    assert process_limit_guidance == []
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "I inspected the failure and applied the targeted repair."

def test_agent_session_run_once_validation_allows_explicit_failure_recovery(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    edit_executions: list[dict] = []
    command = {"command": "python -m pytest test_json_patch.py test_mini_ini.py test_config_dump.py -q"}
    edit_args = {"path": "mini_ini.py", "old": "value", "new": "strip_inline_comments(value)"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "FAILED test_mini_ini.py::TestComments::test_inline_comment_not_supported\n"
                        "1 failed, 106 passed\n"
                        "Command exited with code 1"
                    )
                )
            ],
            details={},
        )

    def execute_edit(tool_call_id, args, signal=None, on_update=None, ctx=None):
        edit_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully replaced 1 block(s).")], details={})

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
    edit_definition = ToolDefinition(
        name="edit",
        label="edit",
        description="Edit a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=execute_edit,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "bash", command, call_id="full_test_once")
        if provider_calls["n"] == 2:
            return tool_call_response_events(m, "edit", edit_args, call_id="fix_after_failed_once_run")
        return text_response_events(m, "I inspected the failing area and applied the targeted fix.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition, edit_definition])

    session.prompt(
        "Run the full local test set once: python -m pytest test_json_patch.py test_mini_ini.py "
        "test_config_dump.py -q. If it fails, inspect only the failing area and fix it. "
        "If it passes, write TEST_SUMMARY.md with the result."
    )

    assert bash_executions == [command]
    assert edit_executions == [edit_args]
    assert provider_calls == {"n": 3}
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "I inspected the failing area and applied the targeted fix."

def test_agent_session_plain_run_once_does_not_runtime_halt_repair_tool(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    edit_executions: list[dict] = []
    command = {"command": "python -m pytest tests/"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "FAILED tests/test_core.py::test_type_annotation\n"
                        "TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType'\n"
                        "Command exited with code 1"
                    )
                )
            ],
            details={},
        )

    def execute_edit(tool_call_id, args, signal=None, on_update=None, ctx=None):
        edit_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully replaced 1 block(s).")], details={})

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
    edit_definition = ToolDefinition(
        name="edit",
        label="edit",
        description="Edit a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=execute_edit,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "bash", command, call_id="initial_pytest")
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "edit",
                {"path": "trailmix/core.py", "old": "list[str] | None", "new": "Optional[list[str]]"},
                call_id="repair_after_failed_pytest",
            )
        return text_response_events(m, "Repair applied after the failed once-only validation run.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition, edit_definition])

    session.prompt(
        "Add an optional type annotation update, update tests and README. "
        "Run the test suite once."
    )

    assert provider_calls["n"] == 3
    assert bash_executions == [command]
    assert edit_executions == [
        {"path": "trailmix/core.py", "old": "list[str] | None", "new": "Optional[list[str]]"}
    ]
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "Repair applied after the failed once-only validation run."

def test_agent_session_plain_run_once_allows_same_batch_followup_mutation(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    edit_executions: list[dict] = []
    command = {"command": "python -m pytest test_okf_parse.py -v"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "FAILED test_okf_parse.py::TestMixedContent::test_full_document_structure\n"
                        "Command exited with code 1"
                    )
                )
            ],
            details={},
        )

    def execute_edit(tool_call_id, args, signal=None, on_update=None, ctx=None):
        edit_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully replaced 4 block(s).")], details={})

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
    edit_definition = ToolDefinition(
        name="edit",
        label="edit",
        description="Edit a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=execute_edit,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            stream = create_assistant_message_event_stream()
            stream.push(
                DoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(id="single_pytest", name="bash", arguments=command),
                            ToolCall(
                                id="same_batch_edit_after_failed_pytest",
                                name="edit",
                                arguments={
                                    "path": "test_okf_parse.py",
                                    "old": "assert len(elements) == 1",
                                    "new": "assert len(elements) >= 1",
                                },
                            ),
                        ],
                        api=m.api,
                        provider=m.provider,
                        model=m.id,
                        usage=empty_usage(),
                        stop_reason="toolUse",
                        timestamp=now_ms(),
                    ),
                )
            )
            return stream
        return text_response_events(m, "Same-batch follow-up mutation completed after the failed validation run.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition, edit_definition])

    session.prompt(
        "Add okf_parse.py and test_okf_parse.py. "
        "Run python -m pytest test_okf_parse.py -v exactly once."
    )

    assert provider_calls["n"] == 2
    assert bash_executions == [command]
    assert edit_executions == [
        {
            "path": "test_okf_parse.py",
            "old": "assert len(elements) == 1",
            "new": "assert len(elements) >= 1",
        }
    ]
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "Same-batch follow-up mutation completed after the failed validation run."

def test_agent_session_plain_run_once_survives_toolguard_steering_messages_without_halt(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    write_executions: list[dict] = []
    bash_executions: list[dict] = []
    edit_executions: list[dict] = []

    def execute_write(tool_call_id, args, signal=None, on_update=None, ctx=None):
        write_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text=f"Successfully wrote 10 bytes to {args['path']}")], details={})

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "FAILED test_csv_stats.py::TestParseCSV::test_csv_with_whitespace\n"
                        "Command exited with code 1"
                    )
                )
            ],
            details={},
        )

    def execute_edit(tool_call_id, args, signal=None, on_update=None, ctx=None):
        edit_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully replaced 1 block(s).")], details={})

    write_definition = ToolDefinition(
        name="write",
        label="write",
        description="Write a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        execute=execute_write,
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
    edit_definition = ToolDefinition(
        name="edit",
        label="edit",
        description="Edit a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"],
        },
        execute=execute_edit,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "write",
                {"path": "test_csv_stats.py", "content": "first\n"},
                call_id="first_write",
            )
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "write",
                {"path": "test_csv_stats.py", "content": "second\n"},
                call_id="second_write",
            )
        if provider_calls["n"] == 3:
            return tool_call_response_events(
                m,
                "bash",
                {"command": "python -m pytest test_csv_stats.py -q"},
                call_id="single_pytest",
            )
        if provider_calls["n"] == 4:
            return tool_call_response_events(
                m,
                "edit",
                {"path": "test_csv_stats.py", "old": "bad", "new": "good"},
                call_id="edit_after_failed_pytest",
            )
        return text_response_events(m, "Failure repaired after toolguard steering and validation failure.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[write_definition, bash_definition, edit_definition],
    )

    session.prompt(
        "Build csv_stats.py and tests. Run python -m pytest test_csv_stats.py -q once."
    )

    assert len(write_executions) == 2
    assert bash_executions == [{"command": "python -m pytest test_csv_stats.py -q"}]
    assert edit_executions == [{"path": "test_csv_stats.py", "old": "bad", "new": "good"}]
    assert provider_calls["n"] == 5
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "Failure repaired after toolguard steering and validation failure."

def test_agent_session_blocks_model_invented_absolute_read_outside_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside" / "leaked.py"
    project.mkdir()
    outside.parent.mkdir()
    outside.write_text("SECRET = True\n", encoding="utf-8")
    model = faux_model()
    provider_calls = {"n": 0}
    read_executions: list[dict] = []

    def execute_read(tool_call_id, args, signal=None, on_update=None, ctx=None):
        read_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text=outside.read_text(encoding="utf-8"))], details={})

    read_definition = ToolDefinition(
        name="read",
        label="read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=execute_read,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": str(outside)}, call_id="outside_read")
        return text_response_events(m, "I will stay inside the current working directory.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(project), model=model, tool_definitions=[read_definition])

    session.prompt("Create a small local parser in this empty project.")

    assert read_executions == []
    assert provider_calls["n"] == 2
    tool_results = [message for message in session.messages if isinstance(message, ToolResultMessage)]
    assert any("outside the current working directory" in message.content[0].text for message in tool_results)

def test_trusted_backend_does_not_claim_bash_path_containment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    command = {"command": f"grep -r OKF {outside} --include='*.py'"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="outside result\n")], details={})

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

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "bash", command, call_id="outside_bash")
        return text_response_events(m, "I will inspect only the current working directory.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(project), model=model, tool_definitions=[bash_definition])

    session.prompt("Inspect this empty project and create a focused TODO.")

    assert bash_executions == [command]
    assert provider_calls["n"] == 2

def test_prompt_text_does_not_authorize_absolute_path_outside_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside" / "allowed.txt"
    project.mkdir()
    outside.parent.mkdir()
    outside.write_text("allowed\n", encoding="utf-8")
    model = faux_model()
    provider_calls = {"n": 0}
    read_executions: list[dict] = []

    def execute_read(tool_call_id, args, signal=None, on_update=None, ctx=None):
        read_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="allowed\n")], details={})

    read_definition = ToolDefinition(
        name="read",
        label="read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=execute_read,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": str(outside)}, call_id="authorized_read")
        return text_response_events(m, "Read the authorized file.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(project), model=model, tool_definitions=[read_definition])

    session.prompt(f"Read this exact file: {outside}")

    assert read_executions == []
    assert provider_calls["n"] == 2
    tool_results = [message for message in session.messages if isinstance(message, ToolResultMessage)]
    assert any("outside the current working directory" in message.content[0].text for message in tool_results)

def test_agent_session_run_once_limit_does_not_halt_failed_non_command_tool(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    edit_executions: list[dict] = []
    write_executions: list[dict] = []
    bash_executions: list[dict] = []
    command = {"command": "python -m pytest tests/"}

    def execute_edit(tool_call_id, args, signal=None, on_update=None, ctx=None):
        edit_executions.append(dict(args))
        raise ValueError("No changes made to okf_bundle.py. The replacement produced identical content.")

    def execute_write(tool_call_id, args, signal=None, on_update=None, ctx=None):
        write_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Successfully wrote 1209 bytes to okf_bundle.py")], details={})

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="12 passed in 0.03s")], details={})

    edit_definition = ToolDefinition(
        name="edit",
        label="edit",
        description="Edit a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=execute_edit,
    )
    write_definition = ToolDefinition(
        name="write",
        label="write",
        description="Write a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        execute=execute_write,
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

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                m,
                "edit",
                {"path": "okf_bundle.py", "old": "missing", "new": "render_markdown"},
                call_id="bad_edit",
            )
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "write",
                {"path": "okf_bundle.py", "content": "complete replacement\n"},
                call_id="recovery_write",
            )
        if provider_calls["n"] == 3:
            return tool_call_response_events(m, "bash", command, call_id="single_pytest")
        return text_response_events(m, "Recovered from the edit failure and ran the tests once.")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[edit_definition, write_definition, bash_definition],
    )

    session.prompt(
        "Add render_markdown(document), add focused tests, then run the tests once."
    )

    assert edit_executions == [{"path": "okf_bundle.py", "old": "missing", "new": "render_markdown"}]
    assert write_executions == [{"path": "okf_bundle.py", "content": "complete replacement\n"}]
    assert bash_executions == [command]
    assert provider_calls["n"] == 4
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "Recovered from the edit failure and ran the tests once."

def test_agent_session_broad_scan_recovery_guidance_prefers_inventory_over_repeating_bash(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    recovery_messages: list[str] = []
    scan_commands = [
        {"command": "find . -type f -name '*.py'"},
        {"command": "rg --files -g '*.py' ."},
    ]

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="src/app.py\nsrc/tools/read.py")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        tool_results = [
            _content_text(message.content)
            for message in c.messages
            if getattr(message, "role", None) == "toolResult"
        ]
        if tool_results and "idempotent_no_progress_warning" in tool_results[-1]:
            recovery_messages.append(tool_results[-1])
            return text_response_events(m, "I will treat the listing as inventory and inspect only relevant files.")
        return tool_call_response_events(
            m,
            "bash",
            scan_commands[min(provider_calls["n"] - 1, len(scan_commands) - 1)],
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    session.prompt("analyze the codebase bot, and read all the codes in python files")

    assert executions == scan_commands
    assert provider_calls["n"] == 3
    assert len(recovery_messages) == 1
    recovery = recovery_messages[0]
    assert "idempotent_no_progress_warning" in recovery
    assert "Do not call the same bash command" in recovery
    assert "For codebase scans, treat listings/search output as inventory" in recovery
    assert "read with path/offset/limit" in recovery
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == (
        "I will treat the listing as inventory and inspect only relevant files."
    )

def test_agent_session_reissues_tool_result_guidance_for_escalating_tool_loop_warnings(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    recovery_lengths: list[int] = []
    repeated_args = {"command": "ls -la src/metrics"}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="Command exited with code 1")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        tool_results = [
            _content_text(message.content)
            for message in c.messages
            if getattr(message, "role", None) == "toolResult"
        ]
        recoveries = [text for text in tool_results if "Tool loop warning" in text]
        recovery_lengths.append(len(recoveries))
        if len(recoveries) >= 2:
            return text_response_events(m, "I will stop retrying bash and use the existing failure.")
        return tool_call_response_events(m, "bash", repeated_args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("scan metrics")

    assistants = [m for m in session.messages if getattr(m, "role", None) == "assistant"]
    assert provider_calls["n"] == 4
    assert executions == [repeated_args, repeated_args, repeated_args]
    assert max(recovery_lengths) >= 2
    assert assistants[-1].content[0].text == "I will stop retrying bash and use the existing failure."

def test_agent_session_blocks_consecutive_repeated_bash_loop_and_stops(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="jsonpatch.py")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    repeated_args = {
        "command": "find . -maxdepth 1 -type f -name 'jsonpatch.py'"
    }

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > 5:
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(m, "bash", repeated_args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("find jsonpatch")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 3
    assert executions == [repeated_args, repeated_args, repeated_args]
    assert len(tool_results) == 3
    assert tool_results[-1].is_error is True
    assert "idempotent_no_progress_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text

def test_agent_session_blocks_interleaved_repeated_bash_no_progress_when_enabled(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    repeated_args = {"command": "ls -la src/metrics"}
    executions: list[dict] = []

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        if args == repeated_args:
            return AgentToolResult(content=[TextContent(text="total 120")], details={})
        return AgentToolResult(content=[TextContent(text=f"diag:{args['command']}")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > 12:
            return text_response_events(m, "loop escaped")
        if provider_calls["n"] % 2 == 0:
            return tool_call_response_events(m, "bash", repeated_args, call_id=f"call_{provider_calls['n']}")
        return tool_call_response_events(
            m,
            "bash",
            {"command": f"pwd && echo diag-{provider_calls['n']}"},
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("inspect metrics without looping")

    repeated_executions = [args for args in executions if args == repeated_args]
    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 6
    assert len(repeated_executions) == 3
    assert tool_results[-1].is_error is True
    assert "idempotent_no_progress_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text
    assert "idempotent_no_progress_block" in session.messages[-1].content[0].text

def test_agent_session_blocks_semantic_bash_file_preview_loop_when_enabled(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    commands = [
        "head -400 src/agents/facebook_surfer.py | tail -100",
        "head -360 src/agents/facebook_surfer.py | tail -50",
        "head -350 src/agents/facebook_surfer.py | tail -30",
        "head -340 src/agents/facebook_surfer.py | tail -20",
        "head -338 src/agents/facebook_surfer.py | tail -10",
        "head -337 src/agents/facebook_surfer.py | tail -5",
        "head -336 src/agents/facebook_surfer.py | tail -10",
    ]

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="        # Configure model")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > len(commands):
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(
            m,
            "bash",
            {"command": commands[provider_calls["n"] - 1]},
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("read every important part of facebook_surfer.py")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 3
    assert executions == [{"command": command} for command in commands[:3]]
    assert tool_results[-1].is_error is True
    assert "idempotent_no_progress_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text

def test_agent_session_blocks_semantic_bash_inventory_loop_when_enabled(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    commands = [
        "ls -la src/metrics",
        "find src/metrics -maxdepth 1 -type f",
        "rg --files src/metrics",
        "find ./src/metrics -type f | sort",
        "ls src/metrics",
        "find src/metrics -type f | sort",
    ]

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="src/metrics/models.py")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > len(commands):
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(
            m,
            "bash",
            {"command": commands[provider_calls["n"] - 1]},
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("scan src/metrics and explain the files")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 3
    assert executions == [{"command": command} for command in commands[:3]]
    assert tool_results[-1].is_error is True
    assert "idempotent_no_progress_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text

def test_agent_session_blocks_cwd_normalized_bash_inventory_loop_when_enabled(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    project = tmp_path / "bot"
    project.mkdir()
    commands = [
        f"cd {project} && ls -la src/metrics/ 2>&1",
        f"find {project}/src/metrics -maxdepth 1 -type f",
        f"cd {project} && rg --files ./src/metrics",
        f"ls -la {project}/src/metrics",
        "find ./src/metrics -type f | sort",
        "ls src/metrics",
    ]

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="src/metrics/models.py")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > len(commands):
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(
            m,
            "bash",
            {"command": commands[provider_calls["n"] - 1]},
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(project),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("scan src/metrics and explain the files")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 3
    assert executions == [{"command": command} for command in commands[:3]]
    assert tool_results[-1].is_error is True
    assert "idempotent_no_progress_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text

def test_agent_session_blocks_bash_loop_when_only_unknown_args_change(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    calls = [
        {"command": "printf ok", "note": "first"},
        {"command": "printf ok", "note": "second"},
        {"command": "printf ok", "note": "third"},
        {"command": "printf ok", "note": "fourth"},
        {"command": "printf ok", "note": "fifth"},
    ]

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="ok")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > len(calls):
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(
            m,
            "bash",
            calls[provider_calls["n"] - 1],
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("run the diagnostic")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 3
    assert executions == calls[:3]
    assert tool_results[-1].is_error is True
    assert "idempotent_no_progress_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text
