from __future__ import annotations

from tests._support_coding_agent import *  # noqa: F403


def test_agent_session_blocks_repeated_missing_bash_tool_loop(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    args = {"command": "ls -la src/metrics"}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > 8:
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(m, "bash", args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[],
        max_iterations=8,
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("scan metrics")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 4
    assert len(tool_results) == 4
    assert tool_results[-1].is_error is True
    assert "repeated_exact_failure_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text

def test_agent_session_blocks_repeated_extension_blocked_bash_loop(tmp_path: Path) -> None:
    model = faux_model()
    runner = ExtensionRunner()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    args = {"command": "ls -la src/metrics"}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="should not execute")], details={})

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

    runner.on("tool_call", lambda event: {"block": True, "reason": "blocked by extension"})

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > 8:
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(m, "bash", args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        extension_runner=runner,
        max_iterations=8,
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("scan metrics")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 4
    assert executions == []
    assert len(tool_results) == 4
    assert tool_results[-1].is_error is True
    assert "repeated_exact_failure_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying bash" in session.messages[-1].content[0].text

def test_agent_session_does_not_claim_bash_token_scanning_is_containment(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    write_executions: list[dict] = []
    outside = tmp_path.parent / f"{tmp_path.name}-outside-ledgerlite.json"
    sequence = [
        ("bash", {"command": f"rm -f {outside} && python -m pytest"}),
        ("write", {"path": "ledgerlite_cli.py", "content": "code"}),
        ("bash", {"command": f"cd {tmp_path} && rm -f {outside} && python -m pytest"}),
        ("write", {"path": "tests/test_ledgerlite_cli.py", "content": "tests"}),
        ("bash", {"command": f"rm -f {outside} && true"}),
        ("bash", {"command": f"rm -f {outside} && python -m pytest"}),
    ]

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="should not execute")], details={})

    def execute_write(tool_call_id, args, signal=None, on_update=None, ctx=None):
        write_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="ok")], details={})

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
        if provider_calls["n"] > len(sequence):
            return text_response_events(m, "loop escaped")
        name, args = sequence[provider_calls["n"] - 1]
        return tool_call_response_events(m, name, args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition, write_definition],
        max_iterations=8,
    )

    session.prompt("add a cli and run tests")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 7
    assert len(bash_executions) == 4
    assert [args["path"] for args in write_executions] == ["ledgerlite_cli.py", "tests/test_ledgerlite_cli.py"]
    assert tool_results[-1].is_error is False
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "loop escaped"

def test_agent_session_blocks_repeated_invalid_read_schema_loop_when_enabled(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > 12:
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(m, "read", {}, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("explain each source file")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 4
    assert len(tool_results) == 4
    assert tool_results[-1].is_error is True
    assert "repeated_exact_failure_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying read" in session.messages[-1].content[0].text
    assert "repeated_exact_failure_block" in session.messages[-1].content[0].text

def test_agent_session_blocks_repeated_invalid_append_schema_loop_when_enabled(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    args = {"path": "docs/probe.md", "content": ""}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] > 8:
            return text_response_events(m, "loop escaped")
        return tool_call_response_events(m, "append", args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        max_iterations=8,
        tool_loop_guardrails={"blocking_enabled": True},
    )

    session.prompt("append empty chunks forever")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 5
    assert len(tool_results) == 5
    assert tool_results[-1].is_error is True
    assert "repeated_exact_failure_block" in tool_results[-1].content[0].text
    assert "STOP repeating" in tool_results[-1].content[0].text
    assert session.messages[-1].role == "assistant"
    assert "I stopped retrying append" in session.messages[-1].content[0].text

def test_agent_session_appends_recovery_guidance_before_consecutive_bash_block(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []
    seen_tool_results: list[list[str]] = []

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
    repeated_args = {"command": "find . -maxdepth 1 -type f -name 'jsonpatch.py'"}

    def script(m, c):
        provider_calls["n"] += 1
        tool_results = [
            _content_text(message.content)
            for message in c.messages
            if getattr(message, "role", None) == "toolResult"
        ]
        seen_tool_results.append(tool_results)
        if tool_results and "idempotent_no_progress_warning" in tool_results[-1]:
            return text_response_events(m, "I will use the existing result instead.")
        return tool_call_response_events(m, "bash", repeated_args, call_id=f"call_{provider_calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    session.prompt("find jsonpatch")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assistants = [m for m in session.messages if getattr(m, "role", None) == "assistant"]
    assert provider_calls["n"] == 3
    assert executions == [repeated_args, repeated_args]
    assert len(tool_results) == 2
    assert "idempotent_no_progress_warning" in tool_results[-1].content[0].text
    assert any("Tool loop warning" in results[-1] for results in seen_tool_results if results)
    assert assistants[-1].content[0].text == "I will use the existing result instead."

def test_agent_session_max_iterations_forces_toolless_summary(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    tool_calls = {"n": 0}
    saw_tools: list[bool] = []

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        return AgentToolResult(content=[TextContent(text=args["value"])], details={})

    probe_definition = ToolDefinition(
        name="probe",
        label="probe",
        description="Probe",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        saw_tools.append(bool(c.tools))
        if c.tools:
            tool_calls["n"] += 1
            return tool_call_response_events(
                m,
                "probe",
                {"value": f"run-{tool_calls['n']}"},
                call_id=f"call_{tool_calls['n']}",
            )
        return text_response_events(m, "summary")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[probe_definition],
        max_iterations=2,
    )

    session.prompt("loop")

    assert provider_calls["n"] == 3
    assert tool_calls["n"] == 2
    assert saw_tools == [True, True, False]
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "summary"

def test_agent_session_prepare_next_turn_refreshes_travis234_turn_state_after_tool_mutation(tmp_path: Path) -> None:
    model = faux_model()
    next_model = dataclasses.replace(model, id="next-model")
    provider_models: list[str] = []
    seen_tool_names: list[list[str]] = []
    session_holder: dict[str, AgentSession] = {}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        session_holder["session"].set_active_tools_by_name([])
        session_holder["session"].set_model(next_model)
        return AgentToolResult(content=[TextContent(text="state changed")], details={})

    mutate_session_definition = ToolDefinition(
        name="mutate_session",
        label="mutate_session",
        description="Mutate session state",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=execute,
    )

    def script(m, c):
        provider_models.append(m.id)
        seen_tool_names.append([tool.name for tool in c.tools or []])
        if len(provider_models) == 1:
            return tool_call_response_events(m, "mutate_session", {}, call_id="mutate_1")
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[mutate_session_definition],
    )
    session_holder["session"] = session

    session.prompt("mutate session")

    assert provider_models == [model.id, next_model.id]
    assert seen_tool_names == [["mutate_session"], []]
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "done"

def test_agent_session_accepts_travis_tool_loop_guardrail_config(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[dict] = []

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
        return tool_call_response_events(
            m,
            "bash",
            {"command": f"bad-{provider_calls['n']}"},
            call_id=f"call_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition],
        tool_loop_guardrails={
            "hard_stop_enabled": True,
            "hard_stop_after": {"same_tool_failure": 2},
        },
    )

    session.prompt("fail repeatedly")

    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert provider_calls["n"] == 2
    assert executions == [{"command": "bad-1"}, {"command": "bad-2"}]
    assert len(tool_results) == 2
    assert "same_tool_failure_halt" in tool_results[-1].content[0].text

def test_tool_loop_guardrail_resets_consecutive_idempotent_count_on_different_tool() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            blocking_enabled=True,
            consecutive_no_progress_block_after=4,
        )
    )
    repeated = {"command": "find . -name jsonpatch.py"}

    for _ in range(3):
        assert controller.before_call("bash", repeated).action == "allow"
        controller.after_call("bash", repeated, "jsonpatch.py", failed=False)

    controller.after_call("read", {"path": "README.md"}, "readme", failed=False)

    assert controller.before_call("bash", repeated).action == "allow"

def test_agent_session_exposes_default_coding_tools_for_greeting(tmp_path: Path) -> None:
    model = faux_model()
    seen = {}

    def script(m, c):
        seen["tools"] = [tool.name for tool in (c.tools or [])]
        seen["system_prompt"] = c.system_prompt
        return text_response_events(m, "hello")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    session.prompt("hi")
    assert set(seen["tools"]) == {
        "read",
        "bash",
        "edit",
        "write",
    }
    assert "No tools are active for this turn" not in seen["system_prompt"]

def test_agent_session_keeps_default_coding_tools_for_repo_inspection_prompt(tmp_path: Path) -> None:
    model = faux_model()
    seen = {}

    def script(m, c):
        seen["tools"] = [tool.name for tool in (c.tools or [])]
        return text_response_events(m, "ok")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    session.prompt("list files under src")
    assert set(seen["tools"]) == {
        "read",
        "bash",
        "edit",
        "write",
    }

def test_agent_session_default_prompt_matches_travis234_without_codebase_scan_drift(tmp_path: Path) -> None:
    model = faux_model()
    seen = {}

    def script(m, c):
        seen["system_prompt"] = c.system_prompt
        return text_response_events(m, "ok")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    session.prompt("analyze the codebase and give me insights")

    assert "For codebase scans" not in seen["system_prompt"]
    assert "use bash only for concise inventory/search commands" not in seen["system_prompt"]
    assert "then use read with path/offset/limit" not in seen["system_prompt"]
    assert "Do not repeat equivalent listings/searches/file previews" not in seen["system_prompt"]

def test_agent_session_registry_set_active_tools_and_allowlist(tmp_path: Path) -> None:
    model = faux_model()
    seen: list[list[str]] = []

    def script(m, c):
        seen.append([tool.name for tool in (c.tools or [])])
        return text_response_events(m, "ok")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, allowed_tool_names=["read", "grep", "find"])
    assert session.get_active_tool_names() == ["read", "grep", "find"]
    assert {tool["name"] for tool in session.get_all_tools()} == {"read", "grep", "find"}
    assert session.get_tool_definition("bash") is None
    assert session.get_tool_definition("grep").name == "grep"

    session.set_active_tools_by_name(["grep", "missing", "find"])
    session.prompt("hello")

    assert seen == [["grep", "find"]]
    assert session.get_active_tool_names() == ["grep", "find"]

def test_agent_session_get_all_tools_returns_travis234_tool_info_with_source_metadata(tmp_path: Path) -> None:
    from travis.coding_agent import SourceInfo, create_synthetic_source_info

    assert create_synthetic_source_info("<test>", source="test") == SourceInfo(path="<test>", source="test")

    model = faux_model()
    builtin_session = AgentSession(cwd=str(tmp_path), model=model, allowed_tool_names=["read"])

    builtin_tool = builtin_session.get_all_tools()[0]

    assert builtin_session.get_active_tool_names() == ["read"]
    assert builtin_tool["name"] == "read"
    assert builtin_tool["promptGuidelines"] == builtin_tool["prompt_guidelines"]
    assert builtin_tool["sourceInfo"] == builtin_tool["source_info"]
    assert builtin_tool["sourceInfo"] == {
        "path": "<builtin:read>",
        "source": "builtin",
        "scope": "temporary",
        "origin": "top-level",
    }

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        return AgentToolResult(content=[], details={})

    definition = ToolDefinition(
        name="custom",
        label="custom",
        description="Custom session tool",
        parameters={"type": "object", "properties": {}},
        execute=execute,
        prompt_guidelines=["Use this custom tool deliberately."],
    )
    custom_session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[definition])

    custom_tool = custom_session.get_all_tools()[0]

    assert custom_session.get_active_tool_names() == ["custom"]
    assert custom_tool["promptGuidelines"] == ["Use this custom tool deliberately."]
    assert custom_tool["sourceInfo"] == {
        "path": "<sdk:custom>",
        "source": "sdk",
        "scope": "temporary",
        "origin": "top-level",
    }

def test_agent_session_refreshes_extension_registered_tools_with_source_metadata(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner, create_synthetic_source_info

    model = faux_model()
    ran: dict[str, object] = {}
    calls = {"n": 0}
    extension_runner = ExtensionRunner()
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        extension_runner=extension_runner,
        allowed_tool_names=["read", "extension_tool"],
    )

    assert session.get_active_tool_names() == ["read"]
    assert {tool["name"] for tool in session.get_all_tools()} == {"read"}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        ran["args"] = args
        ran["cwd"] = ctx.cwd if ctx else None
        return AgentToolResult(content=[TextContent(text="extension ok")], details=None)

    extension_runner.register_tool(
        ToolDefinition(
            name="extension_tool",
            label="extension tool",
            description="Extension registered tool",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            execute=execute,
            prompt_guidelines=["Extension guideline"],
        ),
        source_info=create_synthetic_source_info(
            "/tmp/ext.py",
            source="extension",
            scope="project",
            origin="package",
            base_dir="/tmp",
        ),
    )
    session.refresh_tools(include_all_extension_tools=True)

    tool_info = next(tool for tool in session.get_all_tools() if tool["name"] == "extension_tool")
    assert tool_info["promptGuidelines"] == ["Extension guideline"]
    assert tool_info["sourceInfo"] == {
        "path": "/tmp/ext.py",
        "source": "extension",
        "scope": "project",
        "origin": "package",
        "baseDir": "/tmp",
        "base_dir": "/tmp",
    }
    assert session.get_active_tool_names() == ["read", "extension_tool"]

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "extension_tool", {"value": "from model"})
        return text_response_events(m, "done")

    session.provider_control_plane.api_providers.register(create_faux_provider(script), source_id="test")
    session.prompt("use extension")

    assert ran == {"args": {"value": "from model"}, "cwd": str(tmp_path)}

def test_extension_runner_lifecycle_handlers_follow_travis234_emit_semantics() -> None:
    from travis.coding_agent import ExtensionRunner, emit_session_shutdown_event

    runner = ExtensionRunner()
    seen: list[tuple[str, str, str | None]] = []

    unsubscribe_start = runner.on(
        "session_start",
        lambda event: seen.append(("start", event["reason"], event.get("previousSessionFile"))),
    )
    runner.on(
        "session_shutdown",
        lambda event: seen.append(("shutdown", event["reason"], event.get("targetSessionFile"))),
    )
    runner.on("session_before_switch", lambda event: {"cancel": False, "source": "first"})
    runner.on(
        "session_before_switch",
        lambda event: {"cancel": event["reason"] == "resume", "source": "second"},
    )
    runner.on(
        "session_before_switch",
        lambda event: seen.append(("after-switch", event["reason"], event.get("targetSessionFile"))),
    )

    assert runner.has_handlers("session_start") is True
    assert runner.has_handlers("missing") is False

    assert runner.emit({"type": "session_start", "reason": "startup"}) is None
    assert runner.emit({"type": "session_before_switch", "reason": "new"}) == {
        "cancel": False,
        "source": "second",
    }
    assert runner.emit(
        {"type": "session_before_switch", "reason": "resume", "targetSessionFile": "next.jsonl"}
    ) == {"cancel": True, "source": "second"}
    assert emit_session_shutdown_event(
        runner,
        {"type": "session_shutdown", "reason": "resume", "targetSessionFile": "next.jsonl"},
    ) is True
    assert emit_session_shutdown_event(
        ExtensionRunner(),
        {"type": "session_shutdown", "reason": "quit"},
    ) is False

    unsubscribe_start()

    assert runner.has_handlers("session_start") is False
    assert seen == [
        ("start", "startup", None),
        ("after-switch", "new", None),
        ("shutdown", "resume", "next.jsonl"),
    ]

def test_extension_runner_flag_registration_defaults_and_values() -> None:
    runner = ExtensionRunner()

    runner.register_flag(
        "shared-flag",
        {"description": "first", "type": "boolean", "default": True},
    )
    runner.register_flag(
        "shared-flag",
        {"description": "second", "type": "boolean", "default": False},
    )
    runner.register_flag("name", {"description": "Name", "type": "string", "default": "base"})

    flags = runner.get_flags()

    assert flags["shared-flag"].description == "first"
    assert flags["shared-flag"].type == "boolean"
    assert runner.get_flag("shared-flag") is True
    assert runner.get_flag("name") == "base"

    runner.set_flag_value("shared-flag", False)

    assert runner.get_flag("shared-flag") is False
    assert runner.get_flag_values()["shared-flag"] is False
    assert runner.get_flag("missing") is None

def test_extension_runner_message_renderer_registration_and_lookup() -> None:
    runner = ExtensionRunner()

    def renderer(message, options=None, theme=None):
        return f"rendered:{getattr(message, 'customType', '')}:{bool((options or {}).get('expanded'))}"

    runner.register_message_renderer("my-type", renderer)

    assert runner.get_message_renderer("my-type") is renderer
    assert runner.get_message_renderer("not-exists") is None

def test_extension_runner_shortcut_registration_normalizes_and_overrides() -> None:
    runner = ExtensionRunner()
    calls: list[str] = []

    runner.register_shortcut("CTRL+Y", {"description": "first", "handler": lambda ctx=None: calls.append("first")})
    runner.register_shortcut("ctrl+y", {"description": "second", "handler": lambda ctx=None: calls.append("second")})

    shortcuts = runner.get_shortcuts({})

    assert list(shortcuts) == ["ctrl+y"]
    assert shortcuts["ctrl+y"].description == "second"
    shortcuts["ctrl+y"].handler(None)
    assert calls == ["second"]

def test_agent_session_exposes_extension_runner_and_emits_session_start(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner

    model = faux_model()
    runner = ExtensionRunner()
    seen: list[tuple[str, str | None]] = []
    runner.on("session_start", lambda event: seen.append((event["reason"], event.get("previousSessionFile"))))

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        extension_runner=runner,
        session_start_event={
            "type": "session_start",
            "reason": "resume",
            "previousSessionFile": "old.jsonl",
        },
    )

    assert session.extension_runner is runner
    assert session.has_extension_handlers("session_start") is True
    assert session.has_extension_handlers("missing") is False
    assert seen == [("resume", "old.jsonl")]

def test_agent_session_wraps_tool_definitions_into_runtime_tools(tmp_path: Path) -> None:
    model = faux_model()
    ran = {}
    calls = {"n": 0}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        ran["cwd"] = ctx.cwd if ctx else None
        ran["args"] = args
        return AgentToolResult(content=[], details={})

    definition = ToolDefinition(
        name="custom",
        label="custom",
        description="Custom session tool",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        execute=execute,
        prompt_snippet="Run custom behavior",
    )

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "custom", {"value": "ok"})
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[definition],
        active_tool_names=["custom"],
    )

    assert session.get_active_tool_names() == ["custom"]
    assert [tool["name"] for tool in session.get_all_tools()] == ["custom"]

    session.prompt("use custom")

    assert ran == {"cwd": str(tmp_path), "args": {"value": "ok"}}

def test_agent_session_emits_queue_update_events_before_delivered_user_message(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        return text_response_events(m, f"turn {calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    seen: list[tuple] = []

    def listener(event):
        if event.type == "queue_update":
            seen.append(("queue", list(event.steering), list(event.follow_up)))
        elif event.type == "message_start" and isinstance(event.message, UserMessage):
            seen.append(("user_start", _user_text(event.message)))

    unsubscribe = session.subscribe(listener)
    session.prompt("initial")
    seen.clear()

    session.steer("queued steering")
    session.follow_up("queued follow-up")
    assert session.pending_message_count == 2
    assert session.get_steering_messages() == ["queued steering"]
    assert session.get_follow_up_messages() == ["queued follow-up"]

    session.continue_()

    assert seen[:4] == [
        ("queue", ["queued steering"], []),
        ("queue", ["queued steering"], ["queued follow-up"]),
        ("queue", [], ["queued follow-up"]),
        ("user_start", "queued steering"),
    ]
    assert seen[4:6] == [
        ("queue", [], []),
        ("user_start", "queued follow-up"),
    ]
    assert session.pending_message_count == 0
    assert session.get_steering_messages() == []
    assert session.get_follow_up_messages() == []

    session.follow_up("clear me")
    cleared = session.clear_queue()
    assert cleared == {"steering": [], "follow_up": ["clear me"]}
    assert seen[-1] == ("queue", [], [])

    unsubscribe()
    event_count = len(seen)
    session.steer("after unsubscribe")
    assert len(seen) == event_count

def test_agent_session_queue_modes_batch_messages_in_all_mode(tmp_path: Path) -> None:
    model = faux_model()
    seen_user_batches: list[list[str]] = []
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        seen_user_batches.append(
            [
                _user_text(message)
                for message in c.messages
                if isinstance(message, UserMessage)
            ]
        )
        return text_response_events(m, f"turn {calls['n']}")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)

    assert session.steering_mode == "one-at-a-time"
    assert session.follow_up_mode == "one-at-a-time"
    session.set_steering_mode("all")
    session.set_follow_up_mode("all")
    assert session.steering_mode == "all"
    assert session.follow_up_mode == "all"

    session.prompt("initial")
    session.steer("steer 1")
    session.steer("steer 2")
    session.continue_()
    session.follow_up("follow 1")
    session.follow_up("follow 2")
    session.continue_()

    assert seen_user_batches[1] == ["initial", "steer 1", "steer 2"]
    assert seen_user_batches[2] == ["initial", "steer 1", "steer 2", "follow 1", "follow 2"]

def test_agent_session_prompt_queues_during_streaming_by_behavior(tmp_path: Path) -> None:
    model = faux_model()
    first_stream_started = threading.Event()
    release_first_stream = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        events = text_response_events(model, f"turn {stream_calls['n']}")
        if stream_calls["n"] > 1:
            return create_faux_provider(lambda m, c: events).stream_simple(model, context, options)

        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        first_stream_started.set()

        def finish() -> None:
            release_first_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    session = AgentSession(cwd=str(tmp_path), model=model)
    run_error: list[BaseException] = []

    def run_first_prompt() -> None:
        try:
            session.prompt("initial", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_first_prompt)
    thread.start()
    assert first_stream_started.wait(timeout=2)
    assert session.is_streaming is True

    preflight: list[bool] = []
    try:
        session.prompt("missing behavior", streaming_behavior=None, preflight_result=preflight.append)
        assert False, "expected missing streaming behavior to raise"
    except RuntimeError as error:
        assert "Specify streamingBehavior" in str(error)
    assert preflight == [False]
    assert session.pending_message_count == 0

    steer_preflight: list[bool] = []
    follow_preflight: list[bool] = []
    assert session.prompt("queued steer", streaming_behavior="steer", preflight_result=steer_preflight.append) == []
    assert session.prompt("queued follow", streaming_behavior="followUp", preflight_result=follow_preflight.append) == []

    assert steer_preflight == [True]
    assert follow_preflight == [True]
    assert session.get_steering_messages() == ["queued steer"]
    assert session.get_follow_up_messages() == ["queued follow"]

    release_first_stream.set()
    thread.join(timeout=2)
    assert run_error == []
    assert session.is_streaming is False

    session.continue_(stream_fn=stream_fn)

    user_contents = [_user_text(message) for message in session.messages if isinstance(message, UserMessage)]
    assert user_contents[-2:] == ["queued steer", "queued follow"]
    assert session.pending_message_count == 0

def test_concurrent_external_steering_is_delivered_once_with_distinct_ids(tmp_path: Path) -> None:
    model = faux_model()
    provider_entered = threading.Event()
    release_provider = threading.Event()
    calls = {"count": 0}

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        events = text_response_events(active_model, f"turn {calls['count']}")
        if calls["count"] > 1:
            return create_faux_provider(lambda _model, _context: events).stream_simple(
                active_model,
                context,
                options,
            )
        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        provider_entered.set()

        def finish() -> None:
            release_provider.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    session = AgentSession(cwd=str(tmp_path), model=model)
    errors = []

    def run_turn() -> None:
        try:
            session.prompt("initial", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001 - capture worker failure for assertion.
            errors.append(error)

    turn = threading.Thread(target=run_turn)
    turn.start()
    assert provider_entered.wait(timeout=1)

    first_id = session.steer("duplicate")
    second_id = session.steer("duplicate")
    release_provider.set()
    turn.join(timeout=2)

    user_text = [_user_text(message) for message in session.messages if isinstance(message, UserMessage)]
    assert not turn.is_alive()
    assert errors == []
    assert first_id != second_id
    assert user_text.count("duplicate") == 2
    assert session.pending_message_count == 0

def test_unacknowledged_external_message_restores_without_core_duplicate(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    message_id = session.steer("retry me")
    assert len(session.agent._steering.messages) == 1

    session._restore_unacknowledged_turn_messages()

    assert session.agent._steering.messages == []
    assert [item.id for item in session._turn_mailbox.snapshot("steering")] == [message_id]
    assert session.get_steering_messages() == ["retry me"]

def test_agent_session_input_extension_transforms_and_handles_prompt(tmp_path: Path) -> None:
    model = faux_model()
    provider_user_texts: list[str] = []

    def provider(model, context):
        users = [message for message in context.messages if isinstance(message, UserMessage)]
        latest = users[-1]
        provider_user_texts.append(
            "\n".join(
                part.text
                for part in latest.content
                if getattr(part, "type", None) == "text"
            )
            if isinstance(latest.content, list)
            else latest.content
        )
        return text_response_events(model, "done")

    register_api_provider(create_faux_provider(provider))
    runner = ExtensionRunner()
    seen_inputs: list[dict] = []

    def input_handler(event):
        seen_inputs.append(dict(event))
        if event["text"] == "ping":
            return {"action": "handled"}
        return {"action": "transform", "text": f"transformed:{event['text']}"}

    runner.on("input", input_handler)
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    session.prompt("hello")
    handled_result = session.prompt("ping")

    assert handled_result == []
    assert provider_user_texts == ["transformed:hello"]
    assert [_user_text(message) for message in session.messages if isinstance(message, UserMessage)] == [
        "transformed:hello"
    ]
    assert [event["text"] for event in seen_inputs] == ["hello", "ping"]
    assert [event["source"] for event in seen_inputs] == ["interactive", "interactive"]

def test_agent_session_input_extension_sees_streaming_behavior_before_queue(tmp_path: Path) -> None:
    model = faux_model()
    first_stream_started = threading.Event()
    release_first_stream = threading.Event()
    input_events: list[dict] = []

    def stream_fn(model, context, options):
        events = text_response_events(model, "turn")
        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        first_stream_started.set()

        def finish() -> None:
            release_first_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    runner = ExtensionRunner()
    runner.on("input", lambda event: input_events.append(dict(event)) or {"action": "continue"})
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)
    run_error: list[BaseException] = []

    def run_first_prompt() -> None:
        try:
            session.prompt("initial", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_first_prompt)
    thread.start()
    assert first_stream_started.wait(timeout=2)

    assert session.prompt("queued follow", streaming_behavior="followUp") == []
    assert session.get_follow_up_messages() == ["queued follow"]

    release_first_stream.set()
    thread.join(timeout=2)

    assert run_error == []
    assert [event.get("streamingBehavior") for event in input_events] == [None, "followUp"]

def test_agent_session_message_end_extension_replaces_assistant_message(tmp_path: Path) -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "hello")))
    runner = ExtensionRunner()
    public_events: list[AssistantMessage] = []

    def replace_assistant(event):
        message = event["message"]
        if not isinstance(message, AssistantMessage):
            return None
        replacement_usage = dataclasses.replace(
            message.usage,
            cost=dataclasses.replace(message.usage.cost, total=0.123),
        )
        return {"message": dataclasses.replace(message, usage=replacement_usage)}

    runner.on("message_end", replace_assistant)
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)
    session.subscribe(
        lambda event: public_events.append(event.message)
        if event.type == "message_end" and isinstance(event.message, AssistantMessage)
        else None
    )

    session.prompt("hi")

    assistant = next(message for message in session.messages if isinstance(message, AssistantMessage))
    assert assistant.usage.cost.total == 0.123
    assert public_events[-1].usage.cost.total == 0.123

def test_agent_session_message_end_extension_rejects_role_change(tmp_path: Path) -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "hello")))
    runner = ExtensionRunner()
    errors: list[dict] = []
    runner.on_error(errors.append)

    def replace_assistant_with_user(event):
        message = event["message"]
        if isinstance(message, AssistantMessage):
            return {"message": UserMessage(content="bad replacement")}
        return None

    runner.on("message_end", replace_assistant_with_user)
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    session.prompt("hi")

    assistant = next(message for message in session.messages if isinstance(message, AssistantMessage))
    assert assistant.content[0].text == "hello"
    assert errors[-1]["event"] == "message_end"
    assert "same role" in errors[-1]["error"]

def test_agent_session_tool_result_extension_modifies_result_before_context(tmp_path: Path) -> None:
    model = faux_model()
    provider_seen_tool_texts: list[str] = []

    def provider(model, context):
        if not any(getattr(message, "role", None) == "toolResult" for message in context.messages):
            return tool_call_response_events(model, "echo", {"text": "hello"})
        tool_result = next(message for message in context.messages if getattr(message, "role", None) == "toolResult")
        provider_seen_tool_texts.append("\n".join(part.text for part in tool_result.content))
        return text_response_events(model, "done")

    def execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=args["text"])], details={"text": args["text"]})

    runner = ExtensionRunner()
    runner.on(
        "tool_result",
        lambda event: {
            "content": [TextContent(text="patched result")],
            "details": {"patched": True},
        },
    )
    register_api_provider(create_faux_provider(provider))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tools=[
            AgentTool(
                name="echo",
                label="Echo",
                description="Echo text",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                execute=execute,
            )
        ],
        extension_runner=runner,
    )

    session.prompt("hi")

    tool_result = next(message for message in session.messages if getattr(message, "role", None) == "toolResult")
    assert provider_seen_tool_texts == ["patched result"]
    assert tool_result.content[0].text == "patched result"
    assert tool_result.details == {"patched": True}

def test_agent_session_tool_call_extension_blocks_execution(tmp_path: Path) -> None:
    model = faux_model()
    provider_seen_tool_texts: list[str] = []
    executed = {"called": False}

    def provider(model, context):
        if not any(getattr(message, "role", None) == "toolResult" for message in context.messages):
            return tool_call_response_events(model, "echo", {"text": "hello"})
        tool_result = next(message for message in context.messages if getattr(message, "role", None) == "toolResult")
        provider_seen_tool_texts.append("\n".join(part.text for part in tool_result.content))
        return text_response_events(model, provider_seen_tool_texts[-1])

    def execute(tool_call_id, args, signal=None, on_update=None):
        executed["called"] = True
        return AgentToolResult(content=[TextContent(text="tool executed")], details={})

    runner = ExtensionRunner()
    runner.on("tool_call", lambda event: {"block": True, "reason": "Blocked by test"})
    register_api_provider(create_faux_provider(provider))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tools=[
            AgentTool(
                name="echo",
                label="Echo",
                description="Echo text",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                execute=execute,
            )
        ],
        extension_runner=runner,
    )

    session.prompt("hi")

    tool_result = next(message for message in session.messages if getattr(message, "role", None) == "toolResult")
    assert executed["called"] is False
    assert provider_seen_tool_texts == ["Blocked by test"]
    assert tool_result.is_error is True
    assert tool_result.content[0].text == "Blocked by test"

def test_agent_session_tool_result_extension_chains_partial_patches(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    runner.on(
        "tool_result",
        lambda event: {
            "content": [TextContent(text="first patch")],
            "details": {"source": "first"},
        },
    )
    runner.on("tool_result", lambda event: {"isError": True})

    result = runner.emit_tool_result(
        {
            "type": "tool_result",
            "toolName": "echo",
            "toolCallId": "call-1",
            "input": {"text": "hello"},
            "content": [TextContent(text="base")],
            "details": {"base": True},
            "isError": False,
        }
    )

    assert result["content"][0].text == "first patch"
    assert result["details"] == {"source": "first"}
    assert result["isError"] is True

def test_agent_session_before_agent_start_injects_custom_message_and_system_prompt(tmp_path: Path) -> None:
    model = faux_model()
    provider_system_prompts: list[str] = []
    saw_injected_context: list[bool] = []

    def provider(model, context):
        provider_system_prompts.append(context.system_prompt or "")
        saw_injected_context.append(
            any(
                isinstance(message, UserMessage)
                and (
                    message.content == "injected context"
                    or (
                        isinstance(message.content, list)
                        and any(getattr(part, "text", None) == "injected context" for part in message.content)
                    )
                )
                for message in context.messages
            )
        )
        return text_response_events(model, "done")

    runner = ExtensionRunner()
    runner.on(
        "before_agent_start",
        lambda event: {
            "message": {
                "customType": "before-start",
                "content": "injected context",
                "display": True,
                "details": {"injected": True},
            },
            "systemPrompt": f"{event['systemPrompt']}\n\nextra instructions",
        },
    )
    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    session.prompt("hello")

    assert "extra instructions" in provider_system_prompts[-1]
    assert saw_injected_context == [True]
    custom_messages = [message for message in session.messages if getattr(message, "role", None) == "custom"]
    assert custom_messages[-1].custom_type == "before-start"
    assert custom_messages[-1].content == "injected context"
    assert custom_messages[-1].details == {"injected": True}

def test_extension_runner_before_agent_start_chains_system_prompt_updates() -> None:
    runner = ExtensionRunner()
    seen_prompts: list[str] = []

    def first(event):
        seen_prompts.append(event["systemPrompt"])
        return {"systemPrompt": f"{event['systemPrompt']}\nfirst"}

    def second(event):
        seen_prompts.append(event["systemPrompt"])
        return {"systemPrompt": f"{event['systemPrompt']}\nsecond"}

    runner.on("before_agent_start", first)
    runner.on("before_agent_start", second)

    result = runner.emit_before_agent_start("hello", None, "base", {"cwd": "/tmp/project"})

    assert seen_prompts == ["base", "base\nfirst"]
    assert result == {"systemPrompt": "base\nfirst\nsecond"}

def test_agent_session_context_extension_transforms_provider_messages_without_mutating_session(
    tmp_path: Path,
) -> None:
    model = faux_model()
    provider_user_texts: list[str] = []

    def provider(model, context):
        user = next(message for message in context.messages if isinstance(message, UserMessage))
        if isinstance(user.content, list):
            provider_user_texts.append(
                "\n".join(part.text for part in user.content if getattr(part, "type", None) == "text")
            )
        else:
            provider_user_texts.append(str(user.content))
        return text_response_events(model, "done")

    runner = ExtensionRunner()
    runner.on(
        "context",
        lambda event: {
            "messages": [
                dataclasses.replace(message, content=[TextContent(text="rewritten")])
                if isinstance(message, UserMessage)
                else message
                for message in event["messages"]
            ]
        },
    )
    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    session.prompt("original")

    assert provider_user_texts == ["rewritten"]
    stored_user = next(message for message in session.messages if isinstance(message, UserMessage))
    assert stored_user.content == [TextContent(text="original")]

def test_extension_runner_context_handlers_chain_messages() -> None:
    runner = ExtensionRunner()
    runner.on(
        "context",
        lambda event: {
            "messages": [*event["messages"], UserMessage(content=[TextContent(text="first")], timestamp=now_ms())]
        },
    )
    runner.on(
        "context",
        lambda event: {
            "messages": [*event["messages"], UserMessage(content=[TextContent(text="second")], timestamp=now_ms())]
        },
    )

    messages = runner.emit_context([UserMessage(content=[TextContent(text="base")], timestamp=now_ms())])

    assert [message.content[0].text for message in messages] == ["base", "first", "second"]

def test_agent_session_provider_extension_hooks_are_wired_into_stream_options(tmp_path: Path) -> None:
    model = faux_model()
    payloads: list[object] = []
    response_events: list[dict[str, object]] = []

    def stream_fn(model, context, options):
        payloads.append(options.on_payload({"body": "base"}) if options.on_payload else None)
        if options.on_response:
            options.on_response({"status": 202, "headers": {"x-test": "yes"}})
        return create_faux_provider(lambda m, c: text_response_events(m, "done")).stream_simple(model, context, options)

    runner = ExtensionRunner()
    runner.on(
        "before_provider_request",
        lambda event: {"body": f"{event['payload']['body']}:patched"},
    )
    runner.on("after_provider_response", lambda event: response_events.append(event))
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    session.prompt("hi", stream_fn=stream_fn)

    assert payloads == [{"body": "base:patched"}]
    assert response_events == [
        {"type": "after_provider_response", "status": 202, "headers": {"x-test": "yes"}}
    ]

def test_extension_runner_before_provider_request_chains_payloads() -> None:
    runner = ExtensionRunner()
    runner.on("before_provider_request", lambda event: {"body": f"{event['payload']['body']}:first"})
    runner.on("before_provider_request", lambda event: {"body": f"{event['payload']['body']}:second"})

    payload = runner.emit_before_provider_request({"body": "base"})

    assert payload == {"body": "base:first:second"}

def test_agent_session_dispatches_extension_command_without_provider_turn(tmp_path: Path) -> None:
    model = faux_model()
    command_runs: list[str] = []
    provider_calls = {"n": 0}

    def stream_fn(model, context, options):
        provider_calls["n"] += 1
        return create_faux_provider(lambda m, c: text_response_events(m, "should not run")).stream_simple(
            model, context, options
        )

    runner = ExtensionRunner()
    runner.register_command(
        "testcmd",
        {
            "description": "Test command",
            "handler": lambda args, ctx=None: command_runs.append(args),
        },
    )
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    result = session.prompt("/testcmd hello world", stream_fn=stream_fn)

    assert result == []
    assert command_runs == ["hello world"]
    assert provider_calls["n"] == 0
    assert session.messages == []

def test_agent_session_extension_command_context_exposes_system_prompt_options(tmp_path: Path) -> None:
    seen_options: list[BuildSystemPromptOptions] = []
    runner = ExtensionRunner()

    def handler(args, ctx):
        seen_options.append(ctx.get_system_prompt_options())
        seen_options[-1].selected_tools.append("mutated_tool")

    runner.register_command("inspect-options", {"description": "Inspect options", "handler": handler})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    session.prompt("/inspect-options")
    session.prompt("/inspect-options")

    assert [options.selected_tools for options in seen_options] == [
        [
            "read",
                "bash",
                "edit",
                "write",
            "mutated_tool",
        ],
        [
            "read",
                "bash",
                "edit",
                "write",
            "mutated_tool",
        ],
    ]

def test_agent_session_extension_command_context_can_append_custom_entries_and_messages(tmp_path: Path) -> None:
    session_path = tmp_path / "command-context-custom.jsonl"
    runner = ExtensionRunner()

    def handler(args, ctx):
        entry_id = ctx.append_entry("command-state", {"args": args})
        ctx.send_message(
            {"customType": "command-note", "content": "created by command", "display": True, "details": {"entry": entry_id}}
        )

    runner.register_command("write-context", {"description": "Write context", "handler": handler})
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        extension_runner=runner,
        session_path=str(session_path),
    )

    result = session.prompt("/write-context hello")

    assert result == []
    assert session.messages[-1].role == "custom"
    assert session.messages[-1].custom_type == "command-note"
    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    custom_entry = next(entry for entry in persisted if entry.get("type") == "custom")
    custom_message = next(entry for entry in persisted if entry.get("type") == "custom_message")
    assert custom_entry["customType"] == "command-state"
    assert custom_entry["data"] == {"args": "hello"}
    assert custom_message["parentId"] == custom_entry["id"]
    assert custom_message["customType"] == "command-note"
    assert custom_message["details"] == {"entry": custom_entry["id"]}

def test_agent_session_extension_command_context_can_send_user_message(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen_contexts: list[list[str]] = []

    def provider(message, context):
        seen_contexts.append(
            [_user_text(msg) for msg in context.messages if isinstance(msg, UserMessage)]
        )
        return text_response_events(message, "command user handled")

    register_api_provider(create_faux_provider(provider))

    def handler(args, ctx):
        ctx.send_user_message(f"from command: {args}")

    runner.register_command("ask", {"description": "Ask from command", "handler": handler})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    result = session.prompt("/ask follow through")

    assert result == []
    assert [_user_text(message) for message in session.messages if isinstance(message, UserMessage)] == [
        "from command: follow through"
    ]
    assert seen_contexts == [["from command: follow through"]]

def test_agent_session_create_replaced_session_context_rebinds_message_senders(tmp_path: Path) -> None:
    session_path = tmp_path / "replaced-context.jsonl"
    seen_contexts: list[list[str]] = []

    def provider(message, context):
        seen_contexts.append(
            [_user_text(msg) for msg in context.messages if isinstance(msg, UserMessage)]
        )
        return text_response_events(message, "replacement user handled")

    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))

    ctx = session.create_replaced_session_context()
    custom_messages = ctx.send_message(
        {"customType": "replacement-note", "content": "from replacement", "display": True}
    )
    user_messages = ctx.send_user_message("replacement prompt")

    assert ctx.cwd == str(tmp_path)
    assert ctx.get_session_name() is None
    assert [tool["name"] for tool in ctx.get_all_tools()][:4] == ["read", "bash", "edit", "write"]
    assert custom_messages[-1].role == "custom"
    assert custom_messages[-1].custom_type == "replacement-note"
    assert user_messages is not None
    assert [_user_text(message) for message in user_messages if isinstance(message, UserMessage)] == [
        "replacement prompt"
    ]
    assert seen_contexts == [["from replacement", "replacement prompt"]]
    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert any(entry.get("customType") == "replacement-note" for entry in persisted)

def test_agent_session_extension_command_context_exposes_session_and_tool_metadata(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen: dict[str, object] = {}

    def handler(args, ctx):
        ctx.set_session_name("Command Session")
        seen["name"] = ctx.get_session_name()
        seen["active_before"] = ctx.get_active_tools()
        seen["all_tool_names"] = [tool["name"] for tool in ctx.get_all_tools()]
        ctx.set_active_tools(["read", "bash"])
        seen["active_after"] = ctx.get_active_tools()
        seen["commands"] = [command["name"] for command in ctx.get_commands()]

    runner.register_command("metadata", {"description": "Metadata", "handler": handler})
    runner.register_command("other", {"description": "Other", "handler": lambda args, ctx=None: None})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    result = session.prompt("/metadata")

    assert result == []
    assert session.session_name == "Command Session"
    assert seen["name"] == "Command Session"
    assert seen["active_before"] == [
        "read",
        "bash",
        "edit",
        "write",
    ]
    assert seen["active_after"] == ["read", "bash"]
    assert {"read", "bash", "edit", "write"}.issubset(set(seen["all_tool_names"]))
    assert "append" not in set(seen["all_tool_names"])
    assert seen["commands"][:2] == ["metadata", "other"]
    assert {"agents", "delegate", "cancel-agent"}.issubset(set(seen["commands"]))

def test_agent_session_extension_command_context_exposes_thinking_level(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen: dict[str, object] = {}
    events: list[str] = []

    def handler(args, ctx):
        seen["before"] = ctx.get_thinking_level()
        ctx.set_thinking_level("high")
        seen["after"] = ctx.get_thinking_level()

    runner.register_command("think", {"description": "Think", "handler": handler})
    model = faux_model()
    model.reasoning = True
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)
    session.subscribe(lambda event: events.append(event.level) if event.type == "thinking_level_changed" else None)

    result = session.prompt("/think")

    assert result == []
    assert seen == {"before": "off", "after": "high"}
    assert session.thinking_level == "high"
    assert events == ["high"]

def test_agent_session_extension_command_context_sets_entry_label(tmp_path: Path) -> None:
    session_path = tmp_path / "command-label.jsonl"
    runner = ExtensionRunner()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "first reply")))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        extension_runner=runner,
        session_path=str(session_path),
    )
    session.prompt("first")
    user_entry = next(
        entry
        for entry in session.session_entries
        if entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == _serialized_text_content("first")
    )

    runner.register_command(
        "label-entry",
        {
            "description": "Label entry",
            "handler": lambda args, ctx: ctx.set_label(user_entry["id"], "important"),
        },
    )

    result = session.prompt("/label-entry")

    assert result == []
    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    label_entry = next(entry for entry in persisted if entry.get("type") == "label")
    assert label_entry["targetId"] == user_entry["id"]
    assert label_entry["label"] == "important"

def test_agent_session_extension_command_context_exec_runs_without_session_message(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen: dict[str, object] = {}

    def handler(args, ctx):
        seen["result"] = ctx.exec(
            "python",
            ["-c", "import sys; print('out'); print('err', file=sys.stderr)"],
            {"cwd": str(tmp_path)},
        )

    runner.register_command("exec-it", {"description": "Exec it", "handler": handler})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    result = session.prompt("/exec-it")

    assert result == []
    assert seen["result"] == {"stdout": "out\n", "stderr": "err\n", "code": 0, "killed": False}
    assert not any(getattr(message, "role", None) == "bashExecution" for message in session.messages)

def test_agent_session_extension_command_context_can_wait_and_compact(tmp_path: Path) -> None:
    from travis.compaction import CompactionManager, ContextCompressor

    runner = ExtensionRunner()
    seen: dict[str, object] = {}

    def handler(args, ctx):
        seen["idle"] = ctx.wait_for_idle()
        ctx.compact(
            {
                "customInstructions": args,
                "onComplete": lambda result: seen.update(
                    {
                        "summary": result.summary,
                        "first_kept_entry_id": result.first_kept_entry_id,
                        "tokens_before": result.tokens_before,
                    }
                ),
                "onError": lambda error: seen.update({"error": str(error)}),
            }
        )

    runner.register_command("compact-now", {"description": "Compact now", "handler": handler})
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_last_n=1, protect_first_n=1),
            summarizer=lambda prompt: f"summary includes focus: {'keep auth flow' in prompt}",
        ),
    )
    session.agent.state.messages = [
        UserMessage(content=f"message {index} " + ("x" * 80), timestamp=now_ms() + index)
        for index in range(6)
    ]

    result = session.prompt("/compact-now keep auth flow")

    assert result == []
    assert seen["idle"] is None
    assert seen["summary"] == "summary includes focus: True"
    assert seen["first_kept_entry_id"] == ""
    assert seen["tokens_before"] > 0
    assert "error" not in seen

def test_agent_session_extension_command_context_can_set_model(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    first = faux_model()
    second = faux_model()
    second.id = "second-model"
    second.name = "Second Model"
    seen: dict[str, object] = {}

    def handler(args, ctx):
        seen["changed"] = ctx.set_model(second)

    runner.register_command("set-model", {"description": "Set model", "handler": handler})
    session = AgentSession(cwd=str(tmp_path), model=first, extension_runner=runner)

    result = session.prompt("/set-model")

    assert result == []
    assert seen["changed"] is True
    assert session.model is second
