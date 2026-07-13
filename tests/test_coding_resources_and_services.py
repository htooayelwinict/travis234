from __future__ import annotations

from tests._support_coding_agent import *  # noqa: F403


def test_settings_manager_in_memory_ports_travis234_defaults_setters_and_migration() -> None:
    settings = SettingsManager.in_memory(
        {
            "queueMode": "all",
            "retry": {"maxRetries": 5, "maxDelayMs": 12_345},
            "terminal": {"imageWidthCells": 0},
            "images": {"autoResize": False},
            "skills": {"enableSkillCommands": False, "customDirectories": ["skills/custom"]},
        }
    )

    assert settings.get_steering_mode() == "all"
    assert settings.get_retry_settings() == {"enabled": True, "maxRetries": 5, "baseDelayMs": 2000}
    assert settings.get_provider_retry_settings() == {"timeoutMs": None, "maxRetries": None, "maxRetryDelayMs": 12_345}
    assert settings.get_image_width_cells() == 1
    assert settings.get_image_auto_resize() is False
    assert settings.get_enable_skill_commands() is False
    assert settings.get_skill_paths() == ["skills/custom"]
    assert settings.get_compaction_settings() == {"enabled": True, "reserveTokens": 16384, "keepRecentTokens": 20000}

    settings.set_shell_command_prefix("source ~/.profile")
    settings.set_shell_path("/bin/zsh")
    settings.set_image_auto_resize(True)
    settings.set_show_terminal_progress(True)
    settings.set_default_model_and_provider("openrouter", "qwen/qwen3-coder-next")
    settings.set_enabled_models(["openrouter/*:low"])

    assert settings.get_shell_command_prefix() == "source ~/.profile"
    assert settings.get_shell_path() == "/bin/zsh"
    assert settings.get_image_auto_resize() is True
    assert settings.get_show_terminal_progress() is True
    assert settings.get_default_provider() == "openrouter"
    assert settings.get_default_model() == "qwen/qwen3-coder-next"
    assert settings.get_enabled_models() == ["openrouter/*:low"]

def test_settings_manager_create_persists_global_project_and_project_trust(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()

    settings = SettingsManager.create(str(cwd), str(agent_dir))
    settings.set_shell_command_prefix("printf persisted;")
    settings.set_project_skill_paths(["skills/project"])
    settings.flush()

    reloaded = SettingsManager.create(str(cwd), str(agent_dir))
    assert reloaded.get_shell_command_prefix() == "printf persisted;"
    assert reloaded.get_skill_paths() == ["skills/project"]
    assert (agent_dir / "settings.json").exists()
    assert (cwd / ".travis234" / "settings.json").exists()

    untrusted = SettingsManager.create(str(cwd), str(agent_dir), {"projectTrusted": False})
    assert untrusted.get_shell_command_prefix() == "printf persisted;"
    assert untrusted.get_skill_paths() == []
    try:
        untrusted.set_project_skill_paths(["blocked"])
        assert False, "expected project settings write to be rejected"
    except RuntimeError as error:
        assert "Project is not trusted" in str(error)

def test_builtin_tool_definitions_match_travis234_prompt_metadata(tmp_path: Path) -> None:
    prompt_metadata = {
            "bash": (
                "Execute bash commands (ls, grep, find, etc.)",
                [
                    "Leave stdin closed for normal commands, searches, tests, and servers. Set stdin=open only before using process write or write_raw on that command.",
                ],
            ),
        "grep": ("Search file contents for patterns (respects .gitignore)", []),
        "find": ("Find files by glob pattern (respects .gitignore)", []),
        "ls": ("List directory contents", []),
        "write": (
            "Create or overwrite files",
            [
                "Use write only for new files or complete rewrites.",
                "When the user asks for a summary, report, checklist, notes, or other deliverable in a file path, create or update that file with write before your final response.",
            ],
        ),
        "edit": (
            "Make precise file edits with exact text replacement, including multiple disjoint edits in one call",
            [
                "Use edit for precise changes (edits[].oldText must match exactly)",
                "When changing multiple separate locations in one file, use one edit call with multiple entries in edits[] instead of multiple edit calls",
                "Each edits[].oldText is matched against the original file, not after earlier edits are applied. Do not emit overlapping or nested edits. Merge nearby changes into one edit.",
                "Keep edits[].oldText as small as possible while still being unique in the file. Do not pad with large unchanged regions.",
            ],
        ),
    }

    for name, (snippet, guidelines) in prompt_metadata.items():
        definition = create_tool_definition(name, str(tmp_path))
        assert definition.prompt_snippet == snippet
        assert definition.prompt_guidelines == guidelines

    edit_definition = create_tool_definition("edit", str(tmp_path))
    assert "If two changes affect the same block or nearby lines, merge them into one edit" in edit_definition.description
    assert "Do not include large unchanged regions just to connect distant changes." in edit_definition.description

def test_agent_session_does_not_inject_bash_repair_policy(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        assert "Use bash for file operations like ls, rg, find" in session.system_prompt
        assert "Use bash for meaningful project commands and tests, not as a scratchpad" not in session.system_prompt
        assert "after a failed test, inspect the failure and relevant source or test before editing again" not in session.system_prompt
    finally:
        session.shutdown()

def test_build_system_prompt_includes_tools_and_cwd(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["read", "bash"],
            tool_snippets={"read": "Read file contents", "bash": "Run shell commands"},
            prompt_guidelines=["Use read to examine files instead of cat or sed."],
        )
    )
    assert "Available tools:" in prompt
    assert "- read: Read file contents" in prompt
    assert "Use read to examine files instead of cat or sed." in prompt
    assert "Be concise in your responses" in prompt
    assert str(tmp_path).replace("\\", "/") in prompt

def test_default_system_prompt_identifies_travis_and_prefers_file_tools(tmp_path: Path) -> None:
    prompt = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))

    assert "inside Travis234" in prompt
    assert "If an edit fails to apply, re-read the file" not in prompt
    assert "Do not use bash heredocs, echo, printf, tee, cat >, or shell redirection" in prompt
    assert "stop after about three attempts on the same file" not in prompt
    assert "Explicit user process limits override tool-use persistence" not in prompt
    assert "If the user says to run something once, do not retry or work around it" not in prompt
    assert "treat a failing test as the requested behavior unless you can prove the test is invalid" not in prompt
    assert "Do not weaken requested tests to match current implementation behavior" not in prompt
    assert "after a failed test, inspect the failure and relevant source or test before editing again" not in prompt

def test_system_prompt_prioritizes_latest_user_request_over_generated_context(tmp_path: Path) -> None:
    prompt = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))

    assert "latest user request is the active contract" in prompt
    assert "generated reports, plans, summaries" in prompt
    assert "If tests pass but encode the opposite" in prompt

def test_system_prompt_preserves_test_contracts_and_runner_compatibility(tmp_path: Path) -> None:
    prompt = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))

    assert "Preserve existing passing tests as behavioral evidence" in prompt
    assert "compatible with the project's declared test runner and dependencies" in prompt
    assert "inspect the blocking condition before adding timeout wrappers" in prompt


def test_system_prompt_owns_the_bounded_change_workflow(tmp_path: Path) -> None:
    prompt = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))

    assert "Own the complete workflow in the current turn" in prompt
    assert "Retain successful tool results as working context" in prompt
    assert "do not repeat unchanged reads or searches" in prompt
    assert "A failed tool call, failed test, guardrail recovery, or automatic compaction" in prompt
    assert "Respect explicit user scope, stop conditions, and command limits" in prompt
    assert "Only give the final response after the requested code changes are applied" in prompt
    assert "managed processes needed for the task are terminal or intentionally detached" in prompt

def test_system_prompt_routes_nested_file_writes_away_from_shell_setup(tmp_path: Path) -> None:
    write_definition = create_tool_definition("write", str(tmp_path))
    bash_definition = create_tool_definition("bash", str(tmp_path))

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["read", "bash", "write"],
            tool_snippets={
                "bash": bash_definition.prompt_snippet or "",
                "write": write_definition.prompt_snippet or "",
            },
            prompt_guidelines=bash_definition.prompt_guidelines + write_definition.prompt_guidelines,
        )
    )

    assert "write and append create parent directories" not in prompt
    assert "Do not use bash mkdir before write or append" not in prompt
    assert "append" not in prompt
    assert "If the user says not to run shell commands, do not call bash" not in prompt
    assert "Automatically creates parent directories." in write_definition.description

def test_write_prompt_stays_travis234_shaped_with_deliverable_completion_guidance(tmp_path: Path) -> None:
    definition = create_tool_definition("write", str(tmp_path))

    assert definition.prompt_guidelines == [
        "Use write only for new files or complete rewrites.",
        "When the user asks for a summary, report, checklist, notes, or other deliverable in a file path, create or update that file with write before your final response.",
    ]

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["write"],
            tool_snippets={"write": definition.prompt_snippet or ""},
            prompt_guidelines=definition.prompt_guidelines,
        )
    )

    assert "protocol-looking literal chunks" not in prompt
    assert "append" not in prompt
    assert "deliverable in a file path" in prompt

def test_build_system_prompt_accepts_scope_narrowing_recovery_guidelines(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["read", "bash", "edit", "write"],
            tool_snippets={
                "read": "Read file contents",
                "bash": "Run shell commands",
                "edit": "Edit files",
                "write": "Write files",
            },
            prompt_guidelines=[
                "For broad migrations, first produce a bounded audit and NEXT_PATCH before implementation.",
                "When patching, respect allowed_files, forbidden_files, test_command, success_criteria, and stop_condition.",
                "Keep patches independently reviewable.",
            ],
        )
    )

    assert "bounded audit and NEXT_PATCH" in prompt
    assert "allowed_files, forbidden_files, test_command, success_criteria, and stop_condition" in prompt
    assert "Keep patches independently reviewable." in prompt
    assert "continue until finished" not in prompt.lower()

def test_default_system_prompt_does_not_advertise_travis234_docs_without_explicit_scope(tmp_path: Path) -> None:
    prompt = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))

    assert "Travis documentation" not in prompt
    assert "Main documentation:" not in prompt
    assert "Additional docs:" not in prompt
    assert "Examples:" not in prompt
    assert "Always read travis .md files completely" not in prompt

def test_custom_prompt_includes_skills_when_selected_tools_unset(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "audit" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill = Skill(
        name="audit-skill",
        description="Inspect code carefully",
        file_path=str(skill_path),
        base_dir=str(skill_path.parent),
        source_info=create_synthetic_source_info(str(skill_path), source="test"),
    )

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            custom_prompt="Custom system prompt",
            skills=[skill],
        )
    )

    assert "<available_skills>" in prompt
    assert "<name>audit-skill</name>" in prompt
    assert str(skill_path) in prompt

def test_default_resource_loader_discovers_context_and_system_prompt_files(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader, load_project_context_files

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    child = project / "pkg"
    (agent_dir).mkdir()
    (project / ".travis234").mkdir(parents=True)
    child.mkdir(parents=True)
    (agent_dir / "AGENTS.md").write_text("global instructions", encoding="utf-8")
    (project / "AGENTS.md").write_text("project instructions", encoding="utf-8")
    (child / "CLAUDE.md").write_text("child instructions", encoding="utf-8")
    (project / ".travis234" / "SYSTEM.md").write_text("project system", encoding="utf-8")
    (project / ".travis234" / "APPEND_SYSTEM.md").write_text("project append", encoding="utf-8")

    context_files = load_project_context_files(cwd=str(child), agent_dir=str(agent_dir))
    loader = DefaultResourceLoader(cwd=str(child), agent_dir=str(agent_dir))
    loader.reload()
    project_loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    project_loader.reload()

    assert [Path(item["path"]).name for item in context_files] == ["AGENTS.md", "AGENTS.md", "CLAUDE.md"]
    assert [item["content"] for item in loader.get_agents_files()["agentsFiles"]] == [
        "global instructions",
        "project instructions",
        "child instructions",
    ]
    assert loader.get_system_prompt() is None
    assert loader.get_append_system_prompt() == []
    assert project_loader.get_system_prompt() == "project system"
    assert project_loader.get_append_system_prompt() == ["project append"]

def test_agent_session_uses_resource_loader_and_rebuilds_after_reload(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    (agent_dir).mkdir()
    (project / ".travis234").mkdir(parents=True)
    (project / "AGENTS.md").write_text("context v1", encoding="utf-8")
    (project / ".travis234" / "SYSTEM.md").write_text("system v1", encoding="utf-8")
    (project / ".travis234" / "APPEND_SYSTEM.md").write_text("append v1", encoding="utf-8")

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload()
    session = AgentSession(cwd=str(project), model=faux_model(), resource_loader=loader)

    assert "system v1" in session.system_prompt
    assert "append v1" in session.system_prompt
    assert "context v1" in session.system_prompt

    (project / ".travis234" / "APPEND_SYSTEM.md").write_text("append v2", encoding="utf-8")
    session.reload_resources()

    assert "append v1" not in session.system_prompt
    assert "append v2" in session.system_prompt
    assert session.agent.state.system_prompt == session.system_prompt

def test_agent_session_bind_extensions_applies_error_listener_before_session_start(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner

    runner = ExtensionRunner()
    seen_errors: list[dict[str, object]] = []
    runner.on("session_start", lambda event: (_ for _ in ()).throw(RuntimeError(f"boom {event['reason']}")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    session.bind_extensions({"onError": seen_errors.append})

    assert [error["event"] for error in seen_errors] == ["session_start"]
    assert seen_errors[-1]["error"] == "boom startup"

def test_agent_session_bind_extensions_applies_ui_command_abort_and_shutdown_bindings(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner

    runner = ExtensionRunner()
    calls: list[tuple[str, object | None]] = []
    ui_context = {"panel": "main"}
    command_actions = {
        "waitForIdle": lambda: calls.append(("wait", None)),
        "newSession": lambda options=None: calls.append(("new", options)) or {"cancelled": False},
        "fork": lambda entry_id, options=None: calls.append(("fork", (entry_id, options))) or {"cancelled": False},
        "navigateTree": lambda target_id, options=None: calls.append(("tree", (target_id, options)))
        or {"cancelled": False},
        "switchSession": lambda session_path, options=None: calls.append(("switch", (session_path, options)))
        or {"cancelled": False},
        "reload": lambda: calls.append(("reload", None)),
    }

    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    session.bind_extensions(
        {
            "uiContext": ui_context,
            "mode": "tui",
            "commandContextActions": command_actions,
            "abortHandler": lambda: calls.append(("abort", None)),
            "shutdownHandler": lambda: calls.append(("shutdown", None)),
        }
    )

    assert runner.get_ui_context() is ui_context
    assert runner.has_ui() is True
    assert runner.mode == "tui"

    runner.wait_for_idle()
    assert runner.new_session({"parentSession": "parent.jsonl"}) == {"cancelled": False}
    assert runner.fork("entry-1", {"position": "at"}) == {"cancelled": False}
    assert runner.navigate_tree("entry-2", {"summarize": True}) == {"cancelled": False}
    assert runner.switch_session("next.jsonl", {"withSession": None}) == {"cancelled": False}
    runner.reload()
    runner.abort()
    runner.shutdown()

    assert calls == [
        ("wait", None),
        ("new", {"parentSession": "parent.jsonl"}),
        ("fork", ("entry-1", {"position": "at"})),
        ("tree", ("entry-2", {"summarize": True})),
        ("switch", ("next.jsonl", {"withSession": None})),
        ("reload", None),
        ("abort", None),
        ("shutdown", None),
    ]

def test_extension_runner_passes_travis234_context_to_handlers_and_command_context(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner

    runner = ExtensionRunner(cwd=str(tmp_path))
    model = faux_model()
    signal = AbortSignal()
    ui_context = {"surface": "test"}
    calls: list[tuple[str, object | None]] = []
    seen: dict[str, object] = {}

    runner.set_ui_context(ui_context, "tui")
    runner.bind_core(
        {},
        {
            "getModel": lambda: model,
            "isIdle": lambda: False,
            "isProjectTrusted": lambda: True,
            "getSignal": lambda: signal,
            "abort": lambda: calls.append(("abort", None)),
            "hasPendingMessages": lambda: True,
            "shutdown": lambda: calls.append(("shutdown", None)),
            "getContextUsage": lambda: {"tokens": 12, "contextWindow": 100, "percent": 12},
            "compact": lambda options=None: calls.append(("compact", options)),
            "getSystemPrompt": lambda: "system prompt",
            "getSystemPromptOptions": lambda: {"cwd": str(tmp_path), "selectedTools": ["read"]},
        },
    )
    runner.bind_command_context(
        {
            "waitForIdle": lambda: calls.append(("wait", None)),
            "newSession": lambda options=None: calls.append(("new", options)) or {"cancelled": False},
            "fork": lambda entry_id, options=None: calls.append(("fork", (entry_id, options))) or {"cancelled": False},
            "navigateTree": lambda target_id, options=None: calls.append(("tree", (target_id, options)))
            or {"cancelled": False},
            "switchSession": lambda session_path, options=None: calls.append(("switch", (session_path, options)))
            or {"cancelled": False},
            "reload": lambda: calls.append(("reload", None)),
        }
    )

    def handler(event, ctx):
        seen.update(
            {
                "cwd": ctx.cwd,
                "ui": ctx.ui,
                "mode": ctx.mode,
                "has_ui": ctx.has_ui,
                "model": ctx.model,
                "idle": ctx.is_idle(),
                "trusted": ctx.is_project_trusted(),
                "signal": ctx.signal,
                "pending": ctx.has_pending_messages(),
                "usage": ctx.get_context_usage(),
                "prompt": ctx.get_system_prompt(),
            }
        )
        ctx.abort()
        ctx.shutdown()
        ctx.compact({"customInstructions": "focus"})
        return {"action": "transform", "text": event["text"] + " transformed"}

    runner.on("input", handler)

    assert runner.emit_input("hello") == {"action": "transform", "text": "hello transformed", "images": None}
    assert seen == {
        "cwd": str(tmp_path),
        "ui": ui_context,
        "mode": "tui",
        "has_ui": True,
        "model": model,
        "idle": False,
        "trusted": True,
        "signal": signal,
        "pending": True,
        "usage": {"tokens": 12, "contextWindow": 100, "percent": 12},
        "prompt": "system prompt",
    }

    command_ctx = runner.create_command_context()
    assert command_ctx.get_system_prompt_options() == {"cwd": str(tmp_path), "selectedTools": ["read"]}
    command_ctx.wait_for_idle()
    assert command_ctx.new_session({"parentSession": "p.jsonl"}) == {"cancelled": False}
    assert command_ctx.fork("entry", {"position": "before"}) == {"cancelled": False}
    assert command_ctx.navigate_tree("target", {"label": "bookmark"}) == {"cancelled": False}
    assert command_ctx.switch_session("next.jsonl", {"withSession": None}) == {"cancelled": False}
    command_ctx.reload()

    assert calls == [
        ("abort", None),
        ("shutdown", None),
        ("compact", {"customInstructions": "focus"}),
        ("wait", None),
        ("new", {"parentSession": "p.jsonl"}),
        ("fork", ("entry", {"position": "before"})),
        ("tree", ("target", {"label": "bookmark"})),
        ("switch", ("next.jsonl", {"withSession": None})),
        ("reload", None),
    ]

def test_extension_runner_bind_core_exposes_travis234_action_surface() -> None:
    from travis.coding_agent import ExtensionRunner

    runner = ExtensionRunner()
    model = faux_model()
    tool_info = [{"name": "read"}, {"name": "bash"}]
    active_tools = ["read"]
    session_name: dict[str, str | None] = {"value": None}
    labels: dict[str, str | None] = {}
    calls: list[tuple[str, object]] = []

    def set_active_tools(tool_names: list[str]) -> None:
        active_tools[:] = list(tool_names)

    runner.bind_core(
        {
            "sendMessage": lambda message, options=None: calls.append(("sendMessage", (message, options)))
            or ["custom-message"],
            "sendUserMessage": lambda content, options=None: calls.append(("sendUserMessage", (content, options))),
            "appendEntry": lambda custom_type, data=None: calls.append(("appendEntry", (custom_type, data)))
            or "entry-1",
            "setSessionName": lambda name: session_name.update({"value": name}),
            "getSessionName": lambda: session_name["value"],
            "setLabel": lambda entry_id, label: labels.update({entry_id: label}),
            "getActiveTools": lambda: list(active_tools),
            "getAllTools": lambda: list(tool_info),
            "setActiveTools": set_active_tools,
            "refreshTools": lambda: calls.append(("refreshTools", None)),
            "getCommands": lambda: [{"name": "compact", "description": "Compact context"}],
            "setModel": lambda selected_model: calls.append(("setModel", selected_model)) or True,
            "getThinkingLevel": lambda: "off",
            "setThinkingLevel": lambda level: calls.append(("setThinkingLevel", level)),
        },
        {},
    )

    assert runner.send_message({"customType": "notice"}, {"triggerTurn": False}) == ["custom-message"]
    runner.send_user_message("hello", {"deliverAs": "followUp"})
    assert runner.append_entry("state", {"ok": True}) == "entry-1"
    runner.set_session_name("Session A")
    assert runner.get_session_name() == "Session A"
    runner.set_label("entry-1", "review")
    assert labels == {"entry-1": "review"}
    assert runner.get_active_tools() == ["read"]
    assert runner.get_all_tools() == tool_info
    runner.set_active_tools(["read", "bash"])
    assert runner.get_active_tools() == ["read", "bash"]
    runner.refresh_tools()
    assert runner.get_commands() == [{"name": "compact", "description": "Compact context"}]
    assert runner.set_model(model) is True
    assert runner.get_thinking_level() == "off"
    runner.set_thinking_level("high")

    assert calls == [
        ("sendMessage", ({"customType": "notice"}, {"triggerTurn": False})),
        ("sendUserMessage", ("hello", {"deliverAs": "followUp"})),
        ("appendEntry", ("state", {"ok": True})),
        ("refreshTools", None),
        ("setModel", model),
        ("setThinkingLevel", "high"),
    ]

def test_agent_session_binds_travis234_extension_runner_action_surface(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    runner.register_command("hello", {"description": "Say hello", "handler": lambda args, ctx: []})
    seen_user_messages: list[str] = []

    def provider(message, context):
        seen_user_messages.extend(
            _user_text(msg)
            for msg in context.messages
            if isinstance(msg, UserMessage)
        )
        return text_response_events(message, "runner reply")

    register_api_provider(create_faux_provider(provider))

    session_path = tmp_path / "session.jsonl"
    model = dataclasses.replace(faux_model(), reasoning=True)
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        extension_runner=runner,
        session_path=str(session_path),
        active_tool_names=["read"],
    )
    second_model = dataclasses.replace(model, id="second-model", name="Second")

    runner.set_session_name("Runner Session")
    assert session.session_name == "Runner Session"
    assert runner.get_session_name() == "Runner Session"

    custom_messages = runner.send_message(
        {"customType": "notice", "content": "stored", "details": {"source": "runner"}},
        {"triggerTurn": False},
    )
    custom_entry_id = runner.append_entry("state", {"ok": True})
    runner.set_label(custom_entry_id, "bookmark")
    assert custom_messages[0].custom_type == "notice"

    assert runner.get_active_tools() == ["read"]
    assert "bash" in {tool["name"] for tool in runner.get_all_tools()}
    runner.set_active_tools(["read", "bash"])
    assert runner.get_active_tools() == ["read", "bash"]
    runner.refresh_tools()
    commands = runner.get_commands()
    assert {"name": "hello", "description": "Say hello"} in commands
    assert {"agents", "delegate", "cancel-agent"}.issubset({command["name"] for command in commands})

    assert runner.set_model(second_model) is True
    assert session.model.id == "second-model"
    assert runner.get_thinking_level() == "off"
    runner.set_thinking_level("high")
    assert session.thinking_level == "high"

    result = runner.send_user_message("from runner")
    assert any(isinstance(message, AssistantMessage) and message.model == "second-model" for message in result)
    assert seen_user_messages == ["stored", "from runner"]

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert any(entry.get("type") == "session_info" and entry.get("name") == "Runner Session" for entry in persisted)
    assert any(entry.get("type") == "custom_message" and entry.get("customType") == "notice" for entry in persisted)
    assert any(entry.get("type") == "custom" and entry.get("customType") == "state" for entry in persisted)
    assert any(entry.get("type") == "label" and entry.get("label") == "bookmark" for entry in persisted)

def test_agent_session_reload_emits_lifecycle_and_rediscover_resources(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader, ExtensionRunner

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    prompt_dir = project / "extension-prompts"
    agent_dir.mkdir()
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "audit.md").write_text("---\ndescription: Audit\n---\nAudit $ARGUMENTS", encoding="utf-8")

    runner = ExtensionRunner()
    events: list[tuple[str, str]] = []
    discover_reasons: list[str] = []
    runner.register_flag("sticky", {"type": "boolean", "default": False})
    runner.set_flag_value("sticky", True)
    runner.on("session_start", lambda event: events.append(("start", event["reason"])))
    runner.on("session_shutdown", lambda event: events.append(("shutdown", event["reason"])))
    runner.on(
        "resources_discover",
        lambda event: discover_reasons.append(event["reason"]) or {"promptPaths": [str(prompt_dir)]},
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir))
    loader.reload()
    session = AgentSession(cwd=str(project), model=faux_model(), extension_runner=runner, resource_loader=loader)
    assert "audit" in [template.name for template in session.prompt_templates]

    (prompt_dir / "review.md").write_text("---\ndescription: Review\n---\nReview $ARGUMENTS", encoding="utf-8")
    session.bind_extensions({})
    session.reload()

    assert events == [("start", "startup"), ("start", "startup"), ("shutdown", "reload"), ("start", "reload")]
    assert discover_reasons == ["startup", "startup", "reload"]
    assert runner.get_flag("sticky") is True
    assert {"audit", "review"} <= {template.name for template in session.prompt_templates}

def test_agent_session_exposes_state_resource_loader_and_prompt_templates(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    prompts_dir = project / ".travis234" / "prompts"
    agent_dir.mkdir()
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "review.md").write_text(
        "---\ndescription: Review selected files\nargument-hint: FILES\n---\nReview $ARGUMENTS",
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        additional_prompt_template_paths=[str(prompts_dir)],
    )
    loader.reload()
    session = AgentSession(cwd=str(project), model=faux_model(), resource_loader=loader)

    assert session.state is session.agent.state
    assert session.resource_loader is loader
    assert [prompt.name for prompt in session.prompt_templates] == ["review"]
    assert session.prompt_templates == session.prompt_templates

def test_resource_loader_resolves_package_skills_prompts_and_themes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travis.coding_agent import DefaultResourceLoader

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    package = tmp_path / "pkg"
    skill_dir = package / "skills" / "audit"
    prompt_dir = package / "prompts"
    theme_dir = package / "themes"
    skill_dir.mkdir(parents=True)
    prompt_dir.mkdir(parents=True)
    theme_dir.mkdir(parents=True)
    project.mkdir()
    agent_dir.mkdir()
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "travis-resource-package",
                "travis": {
                    "skills": ["skills/audit/SKILL.md"],
                    "prompts": ["prompts/review.md"],
                    "themes": ["themes/test-theme.json"],
                },
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: audit-skill\ndescription: Inspect code carefully\n---\nSkill body\n",
        encoding="utf-8",
    )
    (prompt_dir / "review.md").write_text(
        "---\ndescription: Review selected files\nargument-hint: FILES\n---\nReview $ARGUMENTS",
        encoding="utf-8",
    )
    (theme_dir / "test-theme.json").write_text(
        json.dumps({"name": "test-theme", "colors": {"text": "#ffffff"}, "vars": {}}),
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), package_paths=[str(package)])
    loader.reload()

    skills = loader.get_skills()["skills"]
    prompts = loader.get_prompts()["prompts"]
    themes = loader.get_themes()["themes"]
    assert [skill.name for skill in skills] == ["audit-skill"]
    assert skills[0].description == "Inspect code carefully"
    assert skills[0].source_info.origin == "package"
    assert [prompt.name for prompt in prompts] == ["review"]
    assert prompts[0].argument_hint == "FILES"
    assert prompts[0].content == "Review $ARGUMENTS"
    assert prompts[0].source_info.origin == "package"
    assert [theme.name for theme in themes] == ["test-theme"]
    assert themes[0].source_info.origin == "package"

    session = AgentSession(cwd=str(project), model=faux_model(), resource_loader=loader)
    assert "<available_skills>" in session.system_prompt
    assert "<name>audit-skill</name>" in session.system_prompt
    assert str(skill_dir / "SKILL.md") in session.system_prompt

def test_default_resource_loader_uses_travis234_settings_manager_resource_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travis.coding_agent import DefaultResourceLoader

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    skill_dir = project / "configured-skills" / "audit"
    prompt_dir = project / "configured-prompts"
    theme_dir = project / "configured-themes"
    skill_dir.mkdir(parents=True)
    prompt_dir.mkdir(parents=True)
    theme_dir.mkdir(parents=True)
    agent_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: configured-audit\ndescription: Inspect configured code\n---\nSkill body\n",
        encoding="utf-8",
    )
    (prompt_dir / "review.md").write_text("---\ndescription: Review\n---\nReview $ARGUMENTS", encoding="utf-8")
    (theme_dir / "configured.json").write_text(
        json.dumps({"name": "configured", "colors": {"text": "#fff"}, "vars": {}}),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory(
        {
            "skills": [str(project / "configured-skills")],
            "prompts": [str(prompt_dir)],
            "themes": [str(theme_dir)],
        }
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), settings_manager=settings)
    loader.reload()

    assert [skill.name for skill in loader.get_skills()["skills"]] == ["configured-audit"]
    assert [prompt.name for prompt in loader.get_prompts()["prompts"]] == ["review"]
    assert [theme.name for theme in loader.get_themes()["themes"]] == ["configured"]

def test_default_resource_loader_loads_app_owned_agent_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent import DefaultResourceLoader
    from travis.coding_agent.resource_loader import format_skills_for_prompt

    home = tmp_path / "home"
    project = home / "repo"
    agent_dir = home / ".travis234" / "agent"
    user_skill_dir = agent_dir / "skills" / "systematic-debugging"
    project.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    user_skill_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (user_skill_dir / "SKILL.md").write_text(
        "---\nname: systematic-debugging\ndescription: Find root causes before fixing bugs\n---\nSkill body\n",
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=False,
    )
    loader.reload()

    skills = loader.get_skills()["skills"]
    assert [skill.name for skill in skills] == ["systematic-debugging"]
    assert skills[0].source_info.scope == "user"
    assert skills[0].source_info.base_dir == str(agent_dir)
    assert str(user_skill_dir / "SKILL.md") in format_skills_for_prompt(skills)

def test_default_resource_loader_ignores_legacy_home_agents_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent import DefaultResourceLoader

    home = tmp_path / "home"
    project = home / "repo"
    agent_dir = home / ".travis234" / "agent"
    app_skill_dir = agent_dir / "skills" / "app-owned"
    legacy_state_dir = ".agen" + "ts"
    legacy_skill_dir = home / legacy_state_dir / "skills" / "legacy-agents"
    project.mkdir(parents=True)
    app_skill_dir.mkdir(parents=True)
    legacy_skill_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (app_skill_dir / "SKILL.md").write_text(
        "---\nname: app-owned\ndescription: Use app-owned skill\n---\nactive\n",
        encoding="utf-8",
    )
    (legacy_skill_dir / "SKILL.md").write_text(
        "---\nname: legacy-agents\ndescription: Use legacy agents skill\n---\nlegacy\n",
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(agent_dir), project_trusted=False)
    loader.reload()

    skill_names = [skill.name for skill in loader.get_skills()["skills"]]
    assert "app-owned" in skill_names
    assert "legacy-agents" not in skill_names

def test_agent_session_read_tool_can_load_discovered_user_skill_outside_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = home / "repo"
    agent_dir = home / ".travis234" / "agent"
    user_skill_dir = agent_dir / "skills"
    skill_file = user_skill_dir / "web_search.md"
    project.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    user_skill_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    skill_file.write_text(
        "---\nname: web-search\ndescription: Use curl for public web search\n---\nUse curl only.\n",
        encoding="utf-8",
    )

    session = AgentSession(cwd=str(project), model=faux_model(), agent_dir=str(agent_dir))
    try:
        definition = session.get_tool_definition("read")
        assert definition is not None

        result = definition.execute("call-1", {"path": str(skill_file)})

        assert "Use curl only." in result.content[0].text
        assert "web-search" in session.system_prompt
    finally:
        session.shutdown()

def test_web_search_skill_allowed_tools_profile_enables_bash_for_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = home / "repo"
    agent_dir = home / ".travis234" / "agent"
    user_skill_dir = agent_dir / "skills"
    skill_file = user_skill_dir / "web_search.md"
    project.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    user_skill_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    skill_file.write_text(
        "---\n"
        "name: web-search\n"
        "description: Use curl for public web search\n"
        "allowed-tools: read bash\n"
        "---\n"
        "Use curl only.\n",
        encoding="utf-8",
    )

    session = AgentSession(cwd=str(project), model=faux_model(), agent_dir=str(agent_dir))
    try:
        task = session._build_subagent_task("web-search", "search latest result", None)

        assert task.allowed_tools == ("read", "bash")
        assert task.sandbox == "read_only"
    finally:
        session.shutdown()

def test_create_agent_session_services_ports_travis234_settings_resource_wiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travis.coding_agent import create_agent_session_from_services, create_agent_session_services

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    skill_dir = project / "skills" / "audit"
    skill_dir.mkdir(parents=True)
    agent_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: service-audit\ndescription: Inspect service code\n---\nSkill body\n",
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory(
        {
            "shellCommandPrefix": "printf service-prefix;",
            "skills": [str(project / "skills")],
        }
    )

    services = create_agent_session_services(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": settings,
        }
    )
    result = create_agent_session_from_services({"services": services, "model": faux_model()})

    assert services["settingsManager"] is settings
    assert [skill.name for skill in services["resourceLoader"].get_skills()["skills"]] == ["service-audit"]
    assert result.session.settings_manager is settings
    assert result.extensions_result is services["resourceLoader"].get_extensions()
    assert result.session.execute_bash("printf user").output == "service-prefixuser"

def test_create_agent_session_services_uses_travis234_provided_resource_loader(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader, create_agent_session_services

    loader = DefaultResourceLoader(cwd=str(tmp_path), agent_dir=str(tmp_path / "agent"))
    loader.reload()

    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "resourceLoader": loader,
        }
    )

    assert services["resourceLoader"] is loader

def test_auth_storage_create_persists_api_key_runtime_and_fallback(tmp_path: Path, monkeypatch) -> None:
    from travis.coding_agent import AuthStorage

    auth_path = tmp_path / "auth.json"
    auth = AuthStorage.create(str(auth_path))
    auth.set("stored", {"type": "api_key", "key": "stored-key"})
    auth.set_runtime_api_key("runtime", "runtime-key")
    auth.set_fallback_resolver(lambda provider: "fallback-key" if provider == "fallback" else None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    reloaded = AuthStorage.create(str(auth_path))
    reloaded.set_fallback_resolver(lambda provider: "fallback-key" if provider == "fallback" else None)

    assert reloaded.get("stored") == {"type": "api_key", "key": "stored-key"}
    assert reloaded.list() == ["stored"]
    assert reloaded.get_api_key("stored") == "stored-key"
    assert auth.get_api_key("runtime") == "runtime-key"
    assert reloaded.get_api_key("openrouter") == "env-key"
    assert reloaded.get_api_key("fallback") == "fallback-key"
    assert reloaded.get_api_key("fallback", {"includeFallback": False}) is None
    assert reloaded.get_auth_status("stored") == {"configured": True, "source": "stored"}

    reloaded.remove("stored")
    assert AuthStorage.create(str(auth_path)).get("stored") is None

def test_model_registry_create_loads_models_json_and_resolves_travis234_request_auth(tmp_path: Path) -> None:
    from travis.coding_agent import AuthStorage, ModelRegistry

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    auth = AuthStorage.create(str(tmp_path / "auth.json"))
    auth.set("proxy", {"type": "api_key", "key": "stored-proxy-key"})
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "name": "Proxy Provider",
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "headers": {"X-Provider": "provider"},
                        "authHeader": True,
                        "models": [
                            {
                                "id": "fast",
                                "name": "Fast",
                                "headers": {"X-Model": "model"},
                                "contextWindow": 64000,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    registry = ModelRegistry.create(auth, str(models_path))
    model = registry.find("proxy", "fast")

    assert model is not None
    assert model.name == "Fast"
    assert model.api == "faux"
    assert model.base_url == "https://proxy.example.test/v1"
    assert model.context_window == 64000
    assert registry.get_provider_display_name("proxy") == "proxy"
    assert registry.has_configured_auth(model) is True
    assert registry.get_available() == [model]
    assert registry.get_api_key_for_provider("proxy") == "stored-proxy-key"
    assert registry.get_api_key_and_headers(model) == {
        "ok": True,
        "apiKey": "stored-proxy-key",
        "headers": {
            "X-Provider": "provider",
            "X-Model": "model",
            "Authorization": "Bearer stored-proxy-key",
        },
    }

def test_create_agent_session_services_defaults_travis234_auth_storage_and_model_registry(tmp_path: Path) -> None:
    from travis.coding_agent import AuthStorage, ModelRegistry, create_agent_session_services

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}), encoding="utf-8")
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    services = create_agent_session_services({"cwd": str(project), "agentDir": str(agent_dir)})

    assert isinstance(services["authStorage"], AuthStorage)
    assert isinstance(services["modelRegistry"], ModelRegistry)
    assert services["authStorage"].get_api_key("proxy") == "service-key"
    model = services["modelRegistry"].find("proxy", "service")
    assert model is not None
    assert services["modelRegistry"].get_api_key_and_headers(model)["apiKey"] == "service-key"

def test_create_agent_session_from_services_resolves_travis234_default_model(tmp_path: Path) -> None:
    from travis.coding_agent import SettingsManager, create_agent_session_from_services, create_agent_session_services

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}), encoding="utf-8")
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory({"defaultProvider": "proxy", "defaultModel": "service"})
    services = create_agent_session_services(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": settings,
        }
    )

    result = create_agent_session_from_services({"services": services})

    assert result.session.model.provider == "proxy"
    assert result.session.model.id == "service"
    assert result.model_fallback_message is None

def test_create_agent_session_streams_with_travis234_model_registry_auth_and_retry_settings(tmp_path: Path) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from travis.ai.stream import ApiProvider
    from travis.coding_agent import SettingsManager, create_agent_session

    captured: dict[str, object] = {}

    def stream(model, context, options=None):
        captured["api_key"] = getattr(options, "api_key", None)
        captured["headers"] = dict(getattr(options, "headers", {}) or {})
        captured["timeout_ms"] = getattr(options, "timeout_ms", None)
        captured["websocket_connect_timeout_ms"] = getattr(options, "websocket_connect_timeout_ms", None)
        captured["max_retries"] = getattr(options, "max_retries", None)
        captured["max_retry_delay_ms"] = getattr(options, "max_retry_delay_ms", None)
        s = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            s.push(event)
        return s

    register_api_provider(ApiProvider(api="svc-faux", stream=stream, stream_simple=stream))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(
        json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}),
        encoding="utf-8",
    )
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "svc-faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "headers": {"X-Provider": "provider"},
                        "authHeader": True,
                        "models": [{"id": "service", "name": "Service", "headers": {"X-Model": "model"}}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": SettingsManager.in_memory(
                {
                    "defaultProvider": "proxy",
                    "defaultModel": "service",
                    "httpIdleTimeoutMs": 0,
                    "websocketConnectTimeoutMs": 4321,
                    "retry": {"provider": {"maxRetries": 7, "maxRetryDelayMs": 4567}},
                }
            ),
        }
    )

    result.session.prompt("hi")

    assert captured == {
        "api_key": "service-key",
        "headers": {
            "X-Provider": "provider",
            "X-Model": "model",
            "Authorization": "Bearer service-key",
        },
        "timeout_ms": 2147483647,
        "websocket_connect_timeout_ms": 4321,
        "max_retries": 7,
        "max_retry_delay_ms": 4567,
    }

def test_create_agent_session_defaults_travis234_session_file_and_stream_session_id(tmp_path: Path) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from travis.ai.stream import ApiProvider
    from travis.coding_agent import SettingsManager, create_agent_session

    captured: dict[str, object] = {}

    def stream(model, context, options=None):
        captured["session_id"] = getattr(options, "session_id", None)
        captured["headers"] = dict(getattr(options, "headers", {}) or {})
        s = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            s.push(event)
        return s

    register_api_provider(ApiProvider(api="svc-opencode", stream=stream, stream_simple=stream))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(
        json.dumps({"opencode": {"type": "api_key", "key": "service-key"}}),
        encoding="utf-8",
    )
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "opencode": {
                        "api": "svc-opencode",
                        "baseUrl": "https://opencode.ai/zen/v1",
                        "apiKey": "models-key",
                        "authHeader": True,
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": SettingsManager.in_memory(
                {"defaultProvider": "opencode", "defaultModel": "service", "defaultThinkingLevel": "low"}
            ),
        }
    )

    session_path = Path(result.session.session_path or "")
    safe_cwd = f"--{str(project.resolve()).lstrip(os.sep).replace(os.sep, '-').replace(':', '-')}--"
    assert session_path.parent == agent_dir / "sessions" / safe_cwd
    assert session_path.name.endswith(f"_{result.session.session_id}.jsonl")
    entries = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["id"] == result.session.session_id
    assert entries[0]["cwd"] == str(project.resolve())
    assert [entry["type"] for entry in entries[1:]] == ["model_change", "thinking_level_change"]
    assert entries[1]["provider"] == "opencode"
    assert entries[1]["modelId"] == "service"
    assert entries[2]["thinkingLevel"] == "low"

    result.session.prompt("hi")

    assert captured["session_id"] == result.session.session_id
    assert captured["headers"]["x-opencode-session"] == result.session.session_id
    assert captured["headers"]["x-opencode-client"] == "travis"
    assert captured["headers"]["Authorization"] == "Bearer service-key"

def test_create_agent_session_restores_existing_travis234_session_model_before_settings_default(tmp_path: Path) -> None:
    from travis.coding_agent import SettingsManager, create_agent_session

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(
        json.dumps(
            {
                "default": {"type": "api_key", "key": "default-key"},
                "saved": {"type": "api_key", "key": "saved-key"},
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "default": {
                        "api": "faux",
                        "baseUrl": "https://default.example.test/v1",
                        "apiKey": "default-key",
                        "models": [{"id": "service", "name": "Default"}],
                    },
                    "saved": {
                        "api": "faux",
                        "baseUrl": "https://saved.example.test/v1",
                        "apiKey": "saved-key",
                        "models": [{"id": "session", "name": "Saved"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    session_path = tmp_path / "existing.jsonl"
    store = SessionStore(str(session_path), cwd=str(project.resolve()))
    store.append_model_change("saved", "session")
    store.append_thinking_level_change("medium")
    store.append_message(UserMessage(content=[TextContent(text="previous")], timestamp=now_ms()))

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "sessionPath": str(session_path),
            "settingsManager": SettingsManager.in_memory(
                {"defaultProvider": "default", "defaultModel": "service", "defaultThinkingLevel": "off"}
            ),
        }
    )

    assert result.session.model.provider == "saved"
    assert result.session.model.id == "session"
    assert result.session.thinking_level == "medium"
    assert result.model_fallback_message is None

def test_create_agent_session_ports_travis234_settings_request_options_to_agent_loop(tmp_path: Path) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from travis.coding_agent import SettingsManager, create_agent_session

    captured: dict[str, object] = {}

    def stream(model, context, options=None):
        captured["transport"] = getattr(options, "transport", None)
        captured["thinking_budgets"] = getattr(options, "thinking_budgets", None)
        captured["max_retry_delay_ms"] = getattr(options, "max_retry_delay_ms", None)
        s = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            s.push(event)
        return s

    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "settingsManager": SettingsManager.in_memory(
                {
                    "transport": "websocket",
                    "thinkingBudgets": {"low": 1024, "medium": 2048},
                    "retry": {"provider": {"maxRetryDelayMs": 12345}},
                }
            ),
        }
    )

    result.session.prompt("hi", stream_fn=stream)

    assert captured == {
        "transport": "websocket",
        "thinking_budgets": {"low": 1024, "medium": 2048},
        "max_retry_delay_ms": 12345,
    }

def test_travis_provider_attribution_headers_match_travis_precedence(monkeypatch) -> None:
    from travis.coding_agent.agent_session_services import merge_provider_attribution_headers

    settings = SettingsManager.in_memory()
    openrouter = Model(
        id="m",
        name="m",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    headers = merge_provider_attribution_headers(
        openrouter,
        settings,
        None,
        {"HTTP-Referer": "https://provider.example", "X-OpenRouter-Categories": "provider-category"},
        {"X-OpenRouter-Title": "request-title"},
    )

    assert headers == {
        "HTTP-Referer": "https://provider.example",
        "X-OpenRouter-Title": "request-title",
        "X-OpenRouter-Categories": "provider-category",
    }

    settings.set_enable_install_telemetry(False)
    assert merge_provider_attribution_headers(openrouter, settings, None) is None
    monkeypatch.setenv("TRAVIS234_TELEMETRY", "YES")
    assert merge_provider_attribution_headers(openrouter, settings, None)["X-OpenRouter-Title"] == "travis"

    nvidia = Model(id="m", name="m", api="faux", provider="nvidia", base_url="https://example.test/v1")
    assert merge_provider_attribution_headers(nvidia, settings, None)["X-BILLING-INVOKE-ORIGIN"] == "travis"

    opencode = Model(id="m", name="m", api="faux", provider="opencode", base_url="https://opencode.ai/zen/v1")
    assert merge_provider_attribution_headers(opencode, settings, "session-1") == {
        "x-opencode-session": "session-1",
        "x-opencode-client": "travis",
    }

def test_exported_create_agent_session_matches_travis234_sdk_result_factory(tmp_path: Path) -> None:
    from travis.coding_agent import (
        CreateAgentSessionResult,
        SettingsManager,
        create_agent_session,
    )

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}), encoding="utf-8")
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": SettingsManager.in_memory(
                {"defaultProvider": "proxy", "defaultModel": "service"}
            ),
        }
    )

    assert isinstance(result, CreateAgentSessionResult)
    assert result.session.model.provider == "proxy"
    assert result.session.model.id == "service"

def test_create_agent_session_ports_travis234_no_tools_option(tmp_path: Path) -> None:
    from travis.coding_agent import create_agent_session

    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "noTools": True,
        }
    )

    assert result.session.get_active_tool_names() == []

def test_create_agent_session_ports_travis234_custom_tools_without_replacing_builtins(tmp_path: Path) -> None:
    from travis.coding_agent import create_agent_session
    from travis.coding_agent.tools.types import ToolDefinition

    definition = ToolDefinition(
        name="custom",
        label="custom",
        description="Custom SDK tool",
        parameters={"type": "object", "properties": {}},
        execute=lambda tool_call_id, args, signal=None, on_update=None, ctx=None: AgentToolResult(
            content=[TextContent(text="ok")],
            details={},
        ),
        prompt_snippet="Run custom SDK tool",
    )

    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "customTools": [definition],
        }
    )

    tool_names = {tool["name"] for tool in result.session.get_all_tools()}
    assert {"read", "bash", "edit", "write", "custom"} <= tool_names
    assert "append" not in tool_names
    assert result.session.get_active_tool_names() == ["read", "bash", "edit", "write"]

def test_create_agent_session_wraps_convert_to_llm_with_travis234_block_images_setting(tmp_path: Path) -> None:
    from travis.coding_agent import create_agent_session

    settings = SettingsManager.in_memory({"images": {"blockImages": True}})
    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "settingsManager": settings,
        }
    )

    converted = result.session._convert_to_llm(
        [
            UserMessage(
                content=[
                    TextContent(text="before"),
                    ImageContent(data="aW1hZ2Ux", mime_type="image/png"),
                    ImageContent(data="aW1hZ2Uy", mime_type="image/jpeg"),
                    TextContent(text="after"),
                ]
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content=[
                    ImageContent(data="aW1hZ2Uz", mime_type="image/png"),
                    TextContent(text="tool text"),
                ],
                is_error=False,
            ),
        ]
    )

    assert converted[0].content == [
        TextContent(text="before"),
        TextContent(text="Image reading is disabled."),
        TextContent(text="after"),
    ]
    assert converted[1].content == [
        TextContent(text="Image reading is disabled."),
        TextContent(text="tool text"),
    ]

def test_default_resource_loader_ports_travis234_inline_extension_factories(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader, ExtensionRunner

    def extension_factory(travis: ExtensionRunner) -> None:
        travis.register_flag("mode", {"type": "string", "default": "safe"})
        travis.register_provider(
            "proxy",
            {
                "api": "faux",
                "baseUrl": "https://proxy.example.test/v1",
                "apiKey": "factory-key",
                "models": [{"id": "factory", "name": "Factory"}],
            },
        )

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )
    loader.reload()

    extensions = loader.get_extensions()
    runtime = extensions["runtime"]

    assert isinstance(runtime, ExtensionRunner)
    assert runtime.get_flags()["mode"].default == "safe"
    assert runtime.get_flag("mode") == "safe"
    assert runtime.pending_provider_registrations == [
        (
            "proxy",
            {
                "api": "faux",
                "baseUrl": "https://proxy.example.test/v1",
                "apiKey": "factory-key",
                "models": [{"id": "factory", "name": "Factory"}],
            },
            "<inline:1>",
        )
    ]

def test_create_agent_session_services_ports_travis234_provider_and_flag_diagnostics(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner, create_agent_session_services

    def extension_factory(travis: ExtensionRunner) -> None:
        travis.register_flag("verbose", {"type": "boolean"})
        travis.register_flag("profile", {"type": "string"})
        travis.register_provider(
            "proxy",
            {
                "api": "faux",
                "baseUrl": "https://proxy.example.test/v1",
                "apiKey": "factory-key",
                "models": [{"id": "factory", "name": "Factory"}],
            },
        )

    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "resourceLoaderOptions": {"extension_factories": [extension_factory]},
            "extensionFlagValues": {"verbose": False, "profile": "debug", "missing": True},
        }
    )
    runtime = services["resourceLoader"].get_extensions()["runtime"]
    model = services["modelRegistry"].find("proxy", "factory")

    assert model is not None
    assert services["modelRegistry"].get_api_key_and_headers(model)["apiKey"] == "factory-key"
    assert runtime.get_flag("verbose") is True
    assert runtime.get_flag("profile") == "debug"
    assert runtime.pending_provider_registrations == []
    assert services["diagnostics"] == [{"type": "error", "message": "Unknown option: --missing"}]

def test_create_agent_session_from_services_uses_loaded_extension_runtime(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner, create_agent_session_from_services, create_agent_session_services

    def extension_factory(travis: ExtensionRunner) -> None:
        travis.register_command(
            "service-hello",
            {
                "description": "Service hello",
                "handler": lambda args, ctx: ctx.send_message(
                    {
                        "customType": "service-hello",
                        "content": "hello from service extension",
                    }
                ),
            },
        )

    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "resourceLoaderOptions": {"extension_factories": [extension_factory]},
        }
    )
    result = create_agent_session_from_services({"services": services, "model": faux_model()})

    result.session.prompt("/service-hello")

    assert result.session.extension_runner is services["resourceLoader"].get_extensions()["runtime"]
    assert any(
        getattr(message, "role", None) == "custom" and getattr(message, "custom_type", None) == "service-hello"
        for message in result.session.messages
    )

def test_agent_session_runs_read_tool_call(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("file body here", encoding="utf-8")
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": "hello.txt"})
        return text_response_events(m, "The file says: file body here")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    session.prompt("read hello.txt")
    roles = [getattr(msg, "role", None) for msg in session.messages]
    assert "toolResult" in roles
    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert "file body here" in tool_results[0].content[0].text
    assert calls["n"] == 2

def test_internal_subagent_exposes_only_canonical_bash_tool(tmp_path: Path) -> None:
    model = faux_model()
    session = AgentSession(cwd=str(tmp_path), model=model)
    child_with_bash = AgentSession(
        cwd=str(tmp_path),
        model=model,
        active_tool_names=["read", "bash"],
        allowed_tool_names=["read", "bash"],
    )
    try:
        assert not hasattr(session, "_install_subagent_tool_aliases")
        assert child_with_bash.get_active_tool_names() == ["read", "bash"]
        assert child_with_bash.get_tool_definition("bash") is not None
        assert child_with_bash.get_tool_definition("run") is None
    finally:
        child_with_bash.shutdown()
        session.shutdown()

def test_tool_loop_guardrail_warns_on_repeated_idempotent_no_progress() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(no_progress_warn_after=2))

    first = controller.after_call("read", {"path": "a.txt"}, "same output", failed=False)
    second = controller.after_call("read", {"path": "a.txt"}, "same output", failed=False)

    assert first.action == "allow"
    assert second.action == "warn"
    assert second.code == "idempotent_no_progress_warning"
    assert "returned the same result 2 times" in second.message

def test_tool_failure_recovery_guidance_respects_user_process_limits() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(same_tool_failure_warn_after=2))

    controller.after_call("bash", {"command": "python examples/basic_usage.py"}, "ModuleNotFoundError", failed=True)
    second = controller.after_call("bash", {"command": "PYTHONPATH=. python examples/basic_usage.py"}, "ok", failed=True)

    assert second.action == "warn"
    assert "unless the user explicitly limited attempts, retries, or commands" in second.message


def test_tool_loop_guardrail_allows_repeated_successful_same_path_mutations_without_warning() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailController

    controller = ToolCallGuardrailController()
    first_args = {"path": "LOCAL_REVIEW.md", "content": "first draft"}
    second_args = {"path": "./LOCAL_REVIEW.md", "content": "expanded draft"}
    other_args = {"path": "OTHER_REVIEW.md", "content": "separate draft"}
    edit_args = {"path": "LOCAL_REVIEW.md", "old": "first draft", "new": "first draft\n\nBoundary check"}

    assert controller.before_call("write", first_args).action == "allow"
    first = controller.after_call("write", first_args, "Successfully wrote 11 bytes to LOCAL_REVIEW.md", failed=False)
    repeated = controller.before_call("write", second_args)
    other = controller.before_call("write", other_args)

    assert first.action == "allow"
    assert repeated.action == "allow"
    assert other.action == "allow"
    second = controller.after_call("write", second_args, "Successfully wrote 14 bytes to LOCAL_REVIEW.md", failed=False)
    assert second.action == "allow"
    assert second.code == "allow"
    assert second.message == ""

    controller.after_call("read", {"path": "LOCAL_REVIEW.md"}, "first draft", failed=False)
    after_read_edit = controller.before_call("edit", edit_args)
    assert after_read_edit.action == "allow"

def test_bash_mutation_classifier_detects_attached_redirects_and_absolute_mutators() -> None:
    from travis.coding_agent.policies.bash_classification import BashMutationClass, classify_bash_mutation

    for command in (
        "echo hi > file",
        "echo hi >file",
        "cat <<EOF >out.txt\nx\nEOF",
        "/bin/rm file",
        "/usr/bin/touch file",
    ):
        assert classify_bash_mutation(command).classification is BashMutationClass.MUTATING

def test_workspace_scope_violation_guardrail_counts_across_state_changes() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(blocking_enabled=True))
    message = (
        "Refusing bash outside the current working directory: /Users/example/.ledgerlite.json. "
        "Current working directory is /tmp/work. Ask the user to name this exact absolute path if it is intentional."
    )

    first = controller.workspace_scope_violation_decision(
        "bash",
        {"command": "rm -f /Users/example/.ledgerlite.json && python -m pytest"},
        "/Users/example/.ledgerlite.json",
        message,
    )
    controller.after_call("write", {"path": "ledgerlite_cli.py", "content": "code"}, "ok", failed=False)
    second = controller.workspace_scope_violation_decision(
        "bash",
        {"command": "cd /tmp/work && rm -f /Users/example/.ledgerlite.json && python -m pytest"},
        "/Users/example/.ledgerlite.json",
        message,
    )
    controller.after_call("write", {"path": "tests/test_ledgerlite_cli.py", "content": "tests"}, "ok", failed=False)
    third = controller.workspace_scope_violation_decision(
        "bash",
        {"command": "rm -f /Users/example/.ledgerlite.json && true"},
        "/Users/example/.ledgerlite.json",
        message,
    )

    assert first.action == "block"
    assert first.code == "workspace_scope_violation"
    assert second.action == "warn"
    assert second.code == "workspace_scope_repeated_warning"
    assert third.action == "halt"
    assert third.code == "workspace_scope_repeated_block"
    assert "same out-of-workspace path 3 times" in third.message

def test_tool_loop_guardrail_resets_exact_failure_after_successful_state_change() -> None:
    from travis.coding_agent.policies.tool_guardrails import ToolCallGuardrailController

    controller = ToolCallGuardrailController()
    pytest_args = {"command": "cd /work/project && python -m pytest tests/ -v"}
    install_args = {"command": "cd /work/project && python -m pip install -e . -q"}

    first = controller.after_call(
        "bash",
        pytest_args,
        "FAILED tests/test_cli.py::test_cli\n\nCommand exited with code 1",
        failed=True,
    )
    state_change = controller.after_call("bash", install_args, "", failed=False)
    retry = controller.after_call(
        "bash",
        pytest_args,
        "FAILED tests/test_core.py::test_unicode\n\nCommand exited with code 1",
        failed=True,
    )

    assert first.action == "allow"
    assert state_change.action == "allow"
    assert retry.action == "allow"
    assert retry.code != "repeated_exact_failure_warning"

def test_agent_session_appends_non_halting_guardrail_warnings_to_tool_result_text(tmp_path: Path) -> None:
    from travis.coding_agent.agent_session import AgentSession

    model = faux_model()
    provider_calls = {"n": 0}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        return AgentToolResult(content=[TextContent(text="same output")], details={})

    read_definition = ToolDefinition(
        name="read",
        label="read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=execute,
    )

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] <= 2:
            return tool_call_response_events(
                m,
                "read",
                {"path": "README.md"},
                call_id=f"call_{provider_calls['n']}",
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[read_definition])

    session.prompt("read twice then stop")

    tool_result_text = "\n".join(
        _content_text(message.content)
        for message in session.messages
        if getattr(message, "role", None) == "toolResult"
    )
    assert provider_calls["n"] == 3
    assert "Tool loop warning" in tool_result_text
    assert "idempotent_no_progress_warning" in tool_result_text
    assert "Use the result already provided" in tool_result_text

def test_agent_session_allows_repeated_same_path_write_batch_then_recovers_with_read_edit(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[str] = []

    def multi_write_events(model):
        calls = [
            ToolCall(id="call_1", name="write", arguments={"path": "LOCAL_REVIEW.md", "content": "first"}),
            ToolCall(id="call_2", name="write", arguments={"path": "./LOCAL_REVIEW.md", "content": "second"}),
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
            return multi_write_events(m)
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "read",
                {"path": "LOCAL_REVIEW.md"},
                call_id="call_read",
            )
        if provider_calls["n"] == 3:
            return tool_call_response_events(
                m,
                "edit",
                {
                    "path": "LOCAL_REVIEW.md",
                    "old": "second",
                    "new": "second\n\n## Boundary check\n- one\n- two\n- three\n",
                },
                call_id="call_edit",
            )
        return text_response_events(m, "recovered after read and edit")

    def write_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(args["content"])
        (tmp_path / args["path"]).write_text(args["content"], encoding="utf-8")
        return AgentToolResult(content=[TextContent(text=f"wrote:{args['content']}")], details={})

    def read_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append("read")
        return AgentToolResult(content=[TextContent(text=(tmp_path / args["path"]).read_text(encoding="utf-8"))], details={})

    def edit_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append("edit")
        target = tmp_path / args["path"]
        content = target.read_text(encoding="utf-8")
        target.write_text(content.replace(args["old"], args["new"], 1), encoding="utf-8")
        return AgentToolResult(content=[TextContent(text="edited")], details={})

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
    read_definition = ToolDefinition(
        name="read",
        label="Read",
        description="read",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=read_execute,
    )
    edit_definition = ToolDefinition(
        name="edit",
        label="Edit",
        description="edit",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=edit_execute,
    )
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[write_definition, read_definition, edit_definition])

    session.prompt("write twice then recover")

    tool_result_text = "\n".join(
        _content_text(message.content)
        for message in session.messages
        if getattr(message, "role", None) == "toolResult"
    )
    user_message_text = "\n".join(
        _content_text(message.content)
        for message in session.messages
        if getattr(message, "role", None) == "user"
    )
    assert executions == ["first", "second", "read", "edit"]
    assert provider_calls["n"] == 4
    assert "repeated_file_mutation_block" not in tool_result_text
    assert "repeated_file_mutation_warning" not in tool_result_text
    assert "repeated_file_mutation_warning" not in user_message_text
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "recovered after read and edit"
