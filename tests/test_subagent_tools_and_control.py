"""Search, extension-tool, and subagent control contracts."""


from __future__ import annotations


import shlex


import shutil


from tests._support_coding_agent import *  # noqa: F403


from travis.coding_agent.processes.service import ProcessSessionService


from travis.coding_agent.processes.types import ProcessOwner, ProcessState


from travis.coding_agent.resource_loader import DefaultResourceLoader


from travis.coding_agent.session_types import (
    _SUBAGENT_TOOL_NAMES,
    _prompt_rejects_subagent_tools,
    _prompt_requests_subagent_tools,
)


from travis.coding_agent.tools import all_tool_names


def eventually(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def test_grep_find_ls(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import os\nx = 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing\n", encoding="utf-8")
    grep = create_tool("grep", str(tmp_path))
    assert "a.py" in grep.execute("c1", {"pattern": "import os"}).content[0].text
    find = create_tool("find", str(tmp_path))
    assert "a.py" in find.execute("c2", {"pattern": "*.py"}).content[0].text
    ls = create_tool("ls", str(tmp_path))
    listing = ls.execute("c3", {}).content[0].text
    assert "a.py" in listing and "b.txt" in listing


def test_path_utils_normalizes_travis234_file_inputs(tmp_path: Path) -> None:
    assert resolve_to_cwd("@~draft.md", str(tmp_path)) == str(tmp_path / "~draft.md")
    assert resolve_to_cwd("file\u00a0name.txt", str(tmp_path)) == str(tmp_path / "file name.txt")


def test_find_tool_matches_path_globs_and_limit_notice(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "foo" / "bar"
    nested.mkdir(parents=True)
    (nested / "example.spec.ts").write_text("", encoding="utf-8")
    other = tmp_path / "some" / "parent" / "child"
    other.mkdir(parents=True)
    (other / "test.spec.ts").write_text("", encoding="utf-8")

    find = create_tool("find", str(tmp_path))

    result = find.execute("c1", {"pattern": "src/**/*.spec.ts"})
    assert result.content[0].text == "src/foo/bar/example.spec.ts"
    assert result.details is None

    limited = find.execute("c2", {"pattern": "*.spec.ts", "limit": 1})
    assert "[1 results limit reached. Use limit=2 for more, or refine pattern]" in limited.content[0].text
    assert limited.details == {"resultLimitReached": 1}


def test_find_and_grep_respect_scoped_gitignore_rules(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "a" / "ignored.txt").write_text("needle a ignored\n", encoding="utf-8")
    (tmp_path / "a" / "kept.txt").write_text("needle a kept\n", encoding="utf-8")
    (tmp_path / "b" / "ignored.txt").write_text("needle b ignored\n", encoding="utf-8")
    (tmp_path / "b" / "kept.txt").write_text("needle b kept\n", encoding="utf-8")
    (tmp_path / "root.txt").write_text("needle root\n", encoding="utf-8")

    find = create_tool("find", str(tmp_path))
    found = find.execute("c1", {"pattern": "**/*.txt"}).content[0].text.splitlines()
    assert found == ["a/kept.txt", "b/ignored.txt", "b/kept.txt", "root.txt"]

    grep = create_tool("grep", str(tmp_path))
    grep_text = grep.execute("c2", {"pattern": "needle"}).content[0].text
    assert "a/ignored.txt" not in grep_text
    assert "a/kept.txt:1: needle a kept" in grep_text
    assert "b/ignored.txt:1: needle b ignored" in grep_text


def test_grep_tool_supports_glob_literal_limit_and_no_match_text(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("TODO in text\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("TODO in python\nTODO second\n", encoding="utf-8")
    grep = create_tool("grep", str(tmp_path))

    result = grep.execute("c1", {"pattern": "TODO", "glob": "*.py", "literal": True, "limit": 1})
    text = result.content[0].text
    assert "b.py:1: TODO in python" in text
    assert "a.txt" not in text
    assert "[1 matches limit reached. Use limit=2 for more, or refine pattern]" in text
    assert result.details == {"matchLimitReached": 1}

    no_match = grep.execute("c2", {"pattern": "absent", "ignoreCase": True})
    assert no_match.content[0].text == "No matches found"
    assert no_match.details is None


def test_ls_tool_applies_travis234_limit_notice_and_sorting(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_text("", encoding="utf-8")
    (tmp_path / "A.txt").write_text("", encoding="utf-8")
    (tmp_path / "dir").mkdir()
    ls = create_tool("ls", str(tmp_path))

    result = ls.execute("c1", {"limit": 2})
    assert result.content[0].text == "A.txt\ndir/\n\n[2 entries limit reached. Use limit=4 for more]"
    assert result.details == {"entryLimitReached": 2}


def test_wrap_tool_definition_injects_ctx(tmp_path: Path) -> None:
    seen = {}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        seen["cwd"] = ctx.cwd if ctx else None
        return AgentToolResult(content=[], details={})

    from travis.coding_agent.tools.types import ToolDefinition

    defn = ToolDefinition(name="t", label="t", description="d", parameters={"type": "object"}, execute=execute)
    tool = wrap_tool_definition(defn, lambda: ToolContext(cwd=str(tmp_path)))
    tool.execute("c1", {})
    assert seen["cwd"] == str(tmp_path)


def test_travis234_extension_define_tool_and_registered_tool_wrappers(tmp_path: Path) -> None:
    from travis.agent.async_utils import run_sync
    from travis.coding_agent import (
        ExtensionRunner,
        RegisteredTool,
        define_tool,
        wrap_registered_tool,
        wrap_registered_tools,
    )
    from travis.coding_agent.tools.types import ToolDefinition

    seen: dict[str, object] = {}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        seen["tool_call_id"] = tool_call_id
        seen["args"] = args
        seen["cwd"] = ctx.cwd
        return AgentToolResult(content=[TextContent(text="ok")], details={"wrapped": True})

    definition = ToolDefinition(
        name="probe",
        label="probe",
        description="Probe extension tool",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        execute=execute,
    )
    defined = define_tool(definition)
    assert defined is definition
    assert define_tool(definition) is definition

    runner = ExtensionRunner(cwd=str(tmp_path))
    registered = RegisteredTool(definition=defined, source_info=create_synthetic_source_info("<test>", source="test"))
    tool = wrap_registered_tool(registered, runner)

    result = run_sync(tool.execute("call-1", {"value": "x"}))

    assert result.content[0].text == "ok"
    assert result.details == {"wrapped": True}
    assert seen == {"tool_call_id": "call-1", "args": {"value": "x"}, "cwd": str(tmp_path)}
    assert [wrapped.name for wrapped in wrap_registered_tools([registered], runner)] == ["probe"]


def test_live_extension_tool_registry_injects_the_canonical_extension_context(tmp_path: Path) -> None:
    import inspect

    from travis.agent.async_utils import run_sync
    from travis.agent.types import AgentToolResult
    from travis.coding_agent import AgentSession, ExtensionRunner
    from travis.coding_agent.tools.types import ToolDefinition

    seen: list[object] = []

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        seen.append(ctx)
        return AgentToolResult(content=[TextContent(text="ok")], details=None)

    runner = ExtensionRunner(cwd=str(tmp_path))
    runner.register_tool(
        ToolDefinition(
            name="extension-probe",
            label="extension-probe",
            description="Inspect the live extension context",
            parameters={"type": "object"},
            execute=execute,
        )
    )
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    session.bind_extensions({"uiContext": object(), "hasUI": True, "mode": "tui"})
    session.refresh_tools(include_all_extension_tools=True)
    tool = next(tool for tool in session.agent.state.tools if tool.name == "extension-probe")

    result = tool.execute("call-1", {})
    if inspect.isawaitable(result):
        result = run_sync(result)

    assert result.content[0].text == "ok"
    assert len(seen) == 1
    context = seen[0]
    assert context.cwd == str(tmp_path)
    assert context.mode == "tui"
    assert context.has_ui is True
    assert context.model_registry is session.model_registry
    assert context.get_context_usage() == session.get_context_usage()


def test_post_bind_extension_tool_registration_refreshes_the_live_registry(tmp_path: Path) -> None:
    from travis.agent.types import AgentToolResult
    from travis.coding_agent import AgentSession, ExtensionRunner
    from travis.coding_agent.tools.types import ToolDefinition

    runner = ExtensionRunner(cwd=str(tmp_path))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    definition = ToolDefinition(
        name="dynamic-probe",
        label="dynamic-probe",
        description="Dynamically registered extension tool",
        parameters={"type": "object"},
        execute=lambda *_args: AgentToolResult(content=[], details=None),
    )

    runner.register_tool(definition)
    assert "dynamic-probe" in {tool["name"] for tool in session.get_all_tools()}

    runner.unregister_tool("dynamic-probe")
    assert "dynamic-probe" not in {tool["name"] for tool in session.get_all_tools()}


def test_travis234_extension_tool_event_type_guards_are_public() -> None:
    from travis.coding_agent import (
        is_bash_tool_result,
        is_edit_tool_result,
        is_find_tool_result,
        is_grep_tool_result,
        is_ls_tool_result,
        is_read_tool_result,
        is_tool_call_event_type,
        is_write_tool_result,
    )

    bash_result = {"type": "tool_result", "toolName": "bash", "details": {"exitCode": 0}}
    read_result = {"type": "tool_result", "toolName": "read", "details": None}
    bash_call = {"type": "tool_call", "toolName": "bash", "input": {"command": "pwd"}}

    assert is_bash_tool_result(bash_result) is True
    assert is_read_tool_result(read_result) is True
    assert is_edit_tool_result(bash_result) is False
    assert is_write_tool_result(bash_result) is False
    assert is_grep_tool_result(bash_result) is False
    assert is_find_tool_result(bash_result) is False
    assert is_ls_tool_result(bash_result) is False
    assert is_tool_call_event_type("bash", bash_call) is True
    assert is_tool_call_event_type("read", bash_call) is False


def test_tool_factory_bundles(tmp_path: Path) -> None:
    assert {t.name for t in create_coding_tools(str(tmp_path))} == {
        "read",
        "bash",
        "tmux",
        "edit",
        "write",
    }
    assert {t.name for t in create_read_only_tools(str(tmp_path))} == {"read", "grep", "find", "ls"}
    assert len(create_all_tools(str(tmp_path))) == 8


def test_tmux_is_builtin_and_default(tmp_path: Path) -> None:
    assert "tmux" in all_tool_names
    assert create_tool_definition("tmux", str(tmp_path)).name == "tmux"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        assert session.get_active_tool_names() == [
            "read",
            "bash",
            "tmux",
            "edit",
            "write",
            "spawn_subagent",
            "wait_subagent",
        ]
        assert "Manage named long-lived tmux sessions" in session.system_prompt
    finally:
        session.shutdown()


def test_agent_session_exposes_only_core_subagent_workflow_by_default(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        active_names = set(session.get_active_tool_names())
        assert {"spawn_subagent", "wait_subagent"} <= active_names
        assert {
            "list_subagents",
            "get_subagent_result",
            "expand_subagent_result",
            "cancel_subagent",
        }.isdisjoint(active_names)
        assert set(_SUBAGENT_TOOL_NAMES) <= {tool["name"] for tool in session.get_all_tools()}
        assert "senior software engineer responsible for the complete outcome" in session.system_prompt
        assert "Use subagents when the user explicitly requests delegation" in session.system_prompt
        assert "only when project instructions do not restrict delegation" in session.system_prompt
        assert "Start independent children concurrently" in session.system_prompt
        assert "collect every child with `wait_subagent`" in session.system_prompt
        assert "Run finite commands with `bash`" in session.system_prompt
        assert "PTY plus `process`" not in session.system_prompt
        assert "Use `tmux` for servers, watchers, REPLs" in session.system_prompt
        assert "Treat child summaries as leads rather than proof" in session.system_prompt
        assert "Never invent files, tests, command results, or verification" in session.system_prompt
    finally:
        session.shutdown()


def test_default_subagent_policy_has_one_compact_authority(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        agent_dir=str(tmp_path / "agent"),
    )
    try:
        with_subagents = session.system_prompt
        session.set_active_tools_by_name(["read", "bash", "tmux", "edit", "write"])
        without_subagents = session.system_prompt
    finally:
        session.shutdown()

    delta = len(with_subagents) - len(without_subagents)
    assert delta <= 1_500
    assert with_subagents.count("two or more independent, bounded") == 1
    assert "project instructions do not restrict delegation" in with_subagents
    assert with_subagents.count("Honor an explicit user request not to use subagents") == 1


def test_parallel_delegation_language_activates_parent_subagent_tools() -> None:
    assert _prompt_requests_subagent_tools("Review this change with multiple agents.") is True
    assert _prompt_requests_subagent_tools("Split this into parallel workers.") is True
    assert _prompt_requests_subagent_tools("Use a multi-agent review.") is True
    assert _prompt_requests_subagent_tools("Do this without subagents.") is False
    assert _prompt_rejects_subagent_tools("Do this without subagents.") is True
    assert _prompt_rejects_subagent_tools("Review this change with multiple agents.") is False


def test_natural_coding_request_exposes_core_subagent_tools_to_the_model(tmp_path: Path) -> None:
    seen_tool_names: list[str] = []

    def script(model, context):
        seen_tool_names.extend(tool.name for tool in context.tools or [])
        return text_response_events(model, "analysis complete")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        session.prompt("Analyze the frontend and backend failures, then recommend fixes.")
        assert {"spawn_subagent", "wait_subagent"} <= set(seen_tool_names)
        assert "list_subagents" not in seen_tool_names
    finally:
        session.shutdown()


def test_explicit_subagent_opt_out_hides_every_subagent_tool_for_the_turn(tmp_path: Path) -> None:
    seen_tool_names: list[str] = []
    seen_system_prompts: list[str] = []

    def script(model, context):
        seen_tool_names.extend(tool.name for tool in context.tools or [])
        seen_system_prompts.append(context.system_prompt)
        return text_response_events(model, "worked alone")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        session.prompt("Analyze the frontend and backend without subagents.")
        assert set(_SUBAGENT_TOOL_NAMES).isdisjoint(seen_tool_names)
        assert "spawn_subagent" not in seen_system_prompts[0]
        assert "wait_subagent" not in seen_system_prompts[0]
        assert {"spawn_subagent", "wait_subagent"} <= set(session.get_active_tool_names())
    finally:
        session.shutdown()


def test_independent_travis_request_hides_every_subagent_tool_for_the_turn(
    tmp_path: Path,
) -> None:
    seen_tool_names: list[str] = []
    seen_system_prompts: list[str] = []

    def script(model, context):
        seen_tool_names.extend(tool.name for tool in context.tools or [])
        seen_system_prompts.append(context.system_prompt)
        return text_response_events(model, "durable Travis requested")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        session.prompt(
            "Could you ask another Travis to look at parser.py and tell me what "
            "parse_name gives back? Check the answer yourself too. Please don't "
            "change any of my files, close the other Travis when you're finished, "
            "and keep the answer simple."
        )

        assert set(_SUBAGENT_TOOL_NAMES).isdisjoint(seen_tool_names)
        assert "spawn_subagent" not in seen_system_prompts[0]
        assert "wait_subagent" not in seen_system_prompts[0]
        assert {"spawn_subagent", "wait_subagent"} <= set(
            session.get_active_tool_names()
        )
    finally:
        session.shutdown()


def test_parallel_delegation_temporarily_exposes_subagent_tools_to_the_model(tmp_path: Path) -> None:
    seen_tool_names: list[str] = []

    def script(model, context):
        seen_tool_names.extend(tool.name for tool in context.tools or [])
        return text_response_events(model, "review complete")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        session.prompt("Review this change with multiple agents.")
        assert set(_SUBAGENT_TOOL_NAMES) <= set(seen_tool_names)
        assert {"spawn_subagent", "wait_subagent"} <= set(session.get_active_tool_names())
        assert {
            "list_subagents",
            "get_subagent_result",
            "expand_subagent_result",
            "cancel_subagent",
        }.isdisjoint(set(session.get_active_tool_names()))
    finally:
        session.shutdown()


def test_internal_child_inherits_process_service_and_unique_owner(tmp_path: Path) -> None:
    child_stream = create_faux_provider(
        lambda model, _context: text_response_events(model, "child complete")
    ).stream_simple
    service = ProcessSessionService(directory=tmp_path / "processes")
    parent_owner = ProcessOwner("app-fixed", str(tmp_path), "agent")
    parent = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        stream_fn=child_stream,
        process_service=service,
        process_owner=parent_owner,
    )
    captured: dict[str, object] = {}

    def recording_factory(**kwargs):
        captured.update(kwargs)
        return AgentSession(**kwargs)

    parent._session_factory = recording_factory
    task = parent._build_subagent_task("shell-worker", "run an interactive command")
    try:
        result = parent._run_internal_subagent(task)
        assert result.status == "completed"
        assert result.summary == "child complete"
        child_owner = captured["process_owner"]
        assert captured["process_service"] is service
        assert captured["allowed_tool_names"] == [
            "read",
            "grep",
            "find",
            "ls",
            "bash",
            "process",
            "edit",
            "write",
            "tmux",
        ]
        assert child_owner != parent_owner
        assert child_owner.workspace_key == parent_owner.workspace_key
        assert child_owner.origin == "agent"
        assert child_owner.app_instance_id == f"app-fixed:subagent:{task.id}"
    finally:
        parent.shutdown()
        service.close()


def test_internal_child_reaps_active_managed_processes_on_completion(tmp_path: Path) -> None:
    calls = {"count": 0}
    model = faux_model()
    service = ProcessSessionService(directory=tmp_path / "processes")
    parent_owner = ProcessOwner("app-fixed", str(tmp_path), "agent")

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {
                    "command": f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(60)')}",
                    "yield_time_ms": 0,
                },
                call_id="child-background-process",
            )
        else:
            events = text_response_events(active_model, "child complete")
        return create_faux_provider(lambda _model, _context: events).stream_simple(
            active_model,
            context,
            options,
        )

    parent = AgentSession(
        cwd=str(tmp_path),
        model=model,
        stream_fn=stream_fn,
        process_service=service,
        process_owner=parent_owner,
    )
    task = parent._build_subagent_task("shell-worker", "start a managed command")
    child_owner = parent._subagent_process_owner(task)
    assert child_owner is not None
    try:
        result = parent._run_internal_subagent(task)

        assert result.status == "completed"
        eventually(
            lambda: all(snapshot.state.terminal for snapshot in service.list(child_owner)),
            timeout=2,
        )
        assert service.list(parent_owner) == ()
    finally:
        parent.shutdown()
        service.close()


def test_internal_child_cleanup_preserves_parent_processes(tmp_path: Path) -> None:
    calls = {"count": 0}
    service = ProcessSessionService(directory=tmp_path / "processes")
    parent_owner = ProcessOwner("app-fixed", str(tmp_path), "agent")

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {
                    "command": f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(60)')}",
                    "yield_time_ms": 0,
                },
                call_id="child-background-process",
            )
        else:
            events = text_response_events(active_model, "child complete")
        return create_faux_provider(lambda _model, _context: events).stream_simple(
            active_model, context, options
        )

    parent = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        stream_fn=stream_fn,
        process_service=service,
        process_owner=parent_owner,
    )
    bash = parent.get_tool_definition("bash")
    assert bash is not None
    parent_job = bash.execute(
        "parent-background-process",
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(60)')}",
            "yield_time_ms": 0,
        },
    )
    task = parent._build_subagent_task("shell-worker", "start a managed command")
    child_owner = parent._subagent_process_owner(task)
    assert child_owner is not None
    try:
        parent._run_internal_subagent(task)

        assert service.list(parent_owner)[0].state is ProcessState.RUNNING
        eventually(
            lambda: bool(service.list(child_owner))
            and all(snapshot.state.terminal for snapshot in service.list(child_owner))
        )
    finally:
        service.kill(parent_owner, parent_job.details["sessionId"])
        parent.shutdown()
        service.close()


def test_internal_child_reaps_managed_processes_when_provider_raises(tmp_path: Path) -> None:
    calls = {"count": 0}
    service = ProcessSessionService(directory=tmp_path / "processes")
    parent_owner = ProcessOwner("app-fixed", str(tmp_path), "agent")

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {
                    "command": f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(60)')}",
                    "yield_time_ms": 0,
                },
                call_id="child-background-process",
            )
            return create_faux_provider(lambda _model, _context: events).stream_simple(
                active_model, context, options
            )
        raise RuntimeError("child provider failed")

    parent = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        stream_fn=stream_fn,
        process_service=service,
        process_owner=parent_owner,
    )
    task = parent._build_subagent_task("shell-worker", "start a managed command")
    child_owner = parent._subagent_process_owner(task)
    assert child_owner is not None
    try:
        result = parent._run_internal_subagent(task)

        assert result.status == "completed"
        eventually(
            lambda: bool(service.list(child_owner))
            and all(snapshot.state.terminal for snapshot in service.list(child_owner))
        )
    finally:
        parent.shutdown()
        service.close()


def test_internal_child_cleanup_runs_after_parent_cancellation(tmp_path: Path) -> None:
    calls = {"count": 0}
    release = threading.Event()
    service = ProcessSessionService(directory=tmp_path / "processes")
    parent_owner = ProcessOwner("app-fixed", str(tmp_path), "agent")

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "bash",
                {
                    "command": f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(60)')}",
                    "yield_time_ms": 0,
                },
                call_id="child-background-process",
            )
        else:
            assert release.wait(2)
            events = text_response_events(active_model, "child released")
        return create_faux_provider(lambda _model, _context: events).stream_simple(
            active_model, context, options
        )

    parent = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        stream_fn=stream_fn,
        process_service=service,
        process_owner=parent_owner,
    )
    task = parent._build_subagent_task("shell-worker", "start a managed command")
    child_owner = parent._subagent_process_owner(task)
    assert child_owner is not None
    try:
        parent.subagents.spawn(task)
        eventually(lambda: any(not item.state.terminal for item in service.list(child_owner)))
        cancelled = parent.subagents.cancel(task.id, "parent cancelled")
        assert cancelled.status == "cancelled"
        release.set()
        eventually(lambda: all(item.state.terminal for item in service.list(child_owner)))
    finally:
        release.set()
        parent.shutdown()
        service.close()


def test_internal_child_cleanup_is_a_noop_without_managed_process_service(tmp_path: Path) -> None:
    parent = AgentSession(cwd=str(tmp_path), model=faux_model())
    task = parent._build_subagent_task("reviewer", "inspect files")
    try:
        assert parent._subagent_process_owner(task) is None
        parent._kill_active_subagent_processes(None)
    finally:
        parent.shutdown()


def test_internal_child_cleanup_suppresses_process_service_failures(tmp_path: Path) -> None:
    class FailingProcessService:
        def list(self, _owner):
            raise RuntimeError("cleanup failed")

    parent = AgentSession(cwd=str(tmp_path), model=faux_model())
    parent.process_service = FailingProcessService()
    try:
        parent._kill_active_subagent_processes(
            ProcessOwner("child", str(tmp_path), "agent")
        )
    finally:
        parent.process_service = None
        parent.shutdown()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_internal_child_process_cleanup_does_not_stop_tmux(tmp_path: Path) -> None:
    parent = AgentSession(cwd=str(tmp_path), model=faux_model())
    tmux = parent.get_tool_definition("tmux")
    assert tmux is not None
    started = tmux.execute(
        "start-isolation-session",
        {"action": "start", "name": "cleanup-isolation", "command": "sleep 60"},
    )
    try:
        parent._kill_active_subagent_processes(None)
        listed = tmux.execute("list-isolation-session", {"action": "list"})
        assert started.details["sessionName"] in listed.details["sessions"]
    finally:
        tmux.execute(
            "stop-isolation-session",
            {"action": "stop", "name": "cleanup-isolation"},
        )
        parent.shutdown()


def test_real_internal_child_writes_edits_and_reports_changed_file(tmp_path: Path) -> None:
    calls = {"count": 0}

    def stream_fn(active_model, context, options):
        calls["count"] += 1
        if calls["count"] == 1:
            events = tool_call_response_events(
                active_model,
                "write",
                {"path": "evidence/child.txt", "content": "draft\n"},
                call_id="child-write",
            )
        elif calls["count"] == 2:
            events = tool_call_response_events(
                active_model,
                "edit",
                {
                    "path": "evidence/child.txt",
                    "edits": [{"oldText": "draft", "newText": "CHILD-EDIT-OK"}],
                },
                call_id="child-edit",
            )
        elif calls["count"] == 3:
            events = tool_call_response_events(
                active_model,
                "bash",
                {"command": 'test "$(cat evidence/child.txt)" = CHILD-EDIT-OK'},
                call_id="child-verify",
            )
        else:
            events = text_response_events(active_model, "Confirmed CHILD-EDIT-OK in evidence/child.txt.")
        return create_faux_provider(lambda _model, _context: events).stream_simple(active_model, context, options)

    parent = AgentSession(cwd=str(tmp_path), model=faux_model(), stream_fn=stream_fn)
    task = parent._build_subagent_task("evidence-writer", "write and verify the child evidence file")
    try:
        result = parent._run_internal_subagent(task)

        assert (tmp_path / "evidence/child.txt").read_text(encoding="utf-8") == "CHILD-EDIT-OK\n"
        assert result.status == "completed"
        assert result.files_changed == ["evidence/child.txt"]
        assert [entry["toolName"] for entry in result.tool_trace] == ["write", "edit", "bash"]
        assert all(name not in task.allowed_tools for name in _SUBAGENT_TOOL_NAMES)
    finally:
        parent.shutdown()


def test_spawn_subagent_tool_rejects_model_facing_safety_overrides(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    definition = session.get_tool_definition("spawn_subagent")
    assert definition is not None

    cases = (
        {"allowedTools": ["read", "bash", "spawn_subagent"], "wait": False},
        {"sandbox": "full_access", "wait": False},
        {"cwd": "/", "wait": False},
        {"timeoutSeconds": 0, "wait": False},
        {"timeoutSeconds": 301, "wait": False},
    )
    try:
        for overrides in cases:
            args = {"role": "reviewer", "goal": "inspect docs", **overrides}
            try:
                definition.execute("call-1", args)
            except ValueError:
                pass
            else:  # pragma: no cover - assertion path
                raise AssertionError(f"Expected spawn_subagent args to fail: {overrides!r}")
    finally:
        session.shutdown()


def test_spawn_subagent_tool_advertises_trusted_typed_roles(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    roles = agent_dir / "roles"
    roles.mkdir(parents=True)
    (roles / "security-reviewer.json").write_text(
        json.dumps(
            {
                "name": "security-reviewer",
                "description": "Review security-sensitive changes\nwithout modifying files.",
                "modelRole": "reviewer",
            }
        ),
        encoding="utf-8",
    )
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=False,
    )
    loader.reload()
    session = AgentSession(
        cwd=str(project),
        agent_dir=str(agent_dir),
        model=faux_model(),
        resource_loader=loader,
    )

    try:
        definition = session.get_tool_definition("spawn_subagent")
        metadata = "\n".join(
            [definition.prompt_snippet or "", *definition.prompt_guidelines]
        )

        assert "security-reviewer" in metadata
        assert "Review security-sensitive changes without modifying files." in metadata
        assert "tool and effect ceilings" in metadata
        assert str((roles / "security-reviewer.json").resolve()) not in metadata
    finally:
        session.shutdown()


def test_spawn_subagent_typed_role_guidance_is_bounded(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    roles = agent_dir / "roles"
    roles.mkdir(parents=True)
    for index in range(12):
        (roles / f"role-{index:02d}.json").write_text(
            json.dumps(
                {
                    "name": f"role-{index:02d}",
                    "description": f"Role {index:02d} " + ("x" * 400),
                }
            ),
            encoding="utf-8",
        )
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=False,
    )
    loader.reload()
    session = AgentSession(
        cwd=str(project),
        agent_dir=str(agent_dir),
        model=faux_model(),
        resource_loader=loader,
    )

    try:
        definition = session.get_tool_definition("spawn_subagent")
        metadata = "\n".join(
            [definition.prompt_snippet or "", *definition.prompt_guidelines]
        )

        assert len(metadata) <= 1_500
        assert "role-00" in metadata
        assert "role-11" not in metadata
        assert str(agent_dir.resolve()) not in metadata
    finally:
        session.shutdown()


def test_spawn_subagent_tool_does_not_parse_override_words_from_task_text(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    definition = session.get_tool_definition("spawn_subagent")
    assert definition is not None

    try:
        result = definition.execute(
            "call-1",
            {
                "role": "reviewer",
                "goal": "Document why the phrase full access mode appears in the fixture.",
                "contextPack": "The source text contains allowedTools=['read','bash','write'].",
                "wait": False,
            },
        )
        assert result.details["status"] == "queued"
        assert len(session.subagents.list_tasks()) == 1
    finally:
        session.shutdown()


def test_spawn_subagent_tool_blocks_duplicate_model_spawns_in_same_turn(tmp_path: Path, monkeypatch) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    definition = session.get_tool_definition("spawn_subagent")
    assert definition is not None
    spawned: list[tuple[str, str]] = []

    def fake_spawn(role: str, goal: str, options: dict | None = None):
        task = session._build_subagent_task(role, goal, options)
        spawned.append((role, goal))
        return task.id, task

    monkeypatch.setattr(session, "_spawn_subagent_task", fake_spawn)

    try:
        first = definition.execute("call-1", {"role": "shell-check", "goal": "run python -V", "wait": False})
        duplicate = definition.execute("call-2", {"role": "shell-check", "goal": "run   python -V", "wait": False})

        assert first.details["status"] == "queued"
        assert duplicate.details["status"] == "blocked"
        assert duplicate.details["reason"] == "duplicate_subagent_spawn_this_turn"
        assert len(spawned) == 1

        session._reset_model_subagent_turn_budget()
        after_reset = definition.execute("call-3", {"role": "shell-check", "goal": "run python -V", "wait": False})

        assert after_reset.details["status"] == "queued"
        assert len(spawned) == 2
    finally:
        session.shutdown()


def test_cancel_subagent_tool_blocks_cancel_after_terminal_result(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    definition = session.get_tool_definition("cancel_subagent")
    assert definition is not None

    def complete(task):
        return SubagentResult(
            task_id=task.id,
            backend=task.backend,
            role=task.role,
            status="completed",
            summary="Reviewer already completed.",
        )

    session.subagents.register_backend(CallableSubagentBackend("instant", complete))

    try:
        task_id, task = session._spawn_subagent_task("reviewer", "inspect package", {"backend": "instant"})
        result = session.subagents.wait(task_id, timeout=1)
        assert result.status == "completed"

        cancelled = definition.execute("call-1", {"taskId": task.id, "reason": "Task already completed"})

        assert cancelled.details["status"] == "blocked"
        assert cancelled.details["reason"] == "subagent_already_terminal"
        assert cancelled.details["terminalStatus"] == "completed"
        assert cancelled.details["taskId"] == task.id
        assert "Cancel skipped" in cancelled.content[0].text
        assert "do not retry cancel_subagent" in cancelled.content[0].text
    finally:
        session.shutdown()


def test_extension_subagent_task_builder_allows_catalog_subset_but_rejects_boundary_overrides(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        task = session._build_subagent_task(
            "reviewer",
            "inspect docs and write findings",
            {"allowedTools": ["read", "bash", "edit", "write"]},
        )
        assert task.sandbox == "workspace_write"
        assert task.allowed_tools == ("read", "bash", "edit", "write")

        cases = (
            {"cwd": str(tmp_path.parent)},
            {"sandbox": "full_access"},
            {"allowed_tools": ["read", "spawn_subagent"]},
            {"allowed_tools": []},
        )
        for options in cases:
            try:
                session._build_subagent_task("reviewer", "inspect docs", options)
            except ValueError as error:
                assert "Subagent safety overrides are not supported" in str(error)
            else:  # pragma: no cover - assertion path
                raise AssertionError(f"Expected extension subagent options to fail: {options!r}")
    finally:
        session.shutdown()


def test_agent_session_records_extension_subagent_observer_errors(tmp_path: Path, monkeypatch) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    def failing_emit(event):
        raise RuntimeError(f"broken observer for {event['type']}")

    monkeypatch.setattr(session._extension_runner, "emit", failing_emit)

    try:
        session._handle_subagent_event({"type": "subagent_start"})
        assert session.subagent_observer_errors() == [
            "extension observer failed for subagent_start: broken observer for subagent_start"
        ]
    finally:
        session.shutdown()


def test_subagent_result_format_uses_compact_public_summary(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        result = SubagentResult(
            task_id="subagent-fixed",
            backend="internal",
            role="reviewer",
            status="completed",
            summary="Reviewed report that mentions travis234/tests/test_tui.py.",
        )

        formatted = session._format_subagent_result(result)

        assert "Subagent subagent-fixed" in formatted
        assert "role: reviewer" in formatted
        assert "backend: internal" in formatted
        assert "status: completed" in formatted
        assert "summary: Reviewed report that mentions travis234/tests/test_tui.py." in formatted
        assert "filesChanged" not in formatted
        assert "errors" not in formatted
    finally:
        session.shutdown()
