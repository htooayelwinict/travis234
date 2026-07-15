from __future__ import annotations

from pathlib import Path

from tests._support_tui import *  # noqa: F403
from travis.coding_agent.project_trust import ProjectTrustStore
from travis.coding_agent.settings_manager import SettingsManager
from travis.tui.interactive_extensions import _manual_compression_options


def test_compact_deep_help_describes_the_generational_checkpoint(tmp_path: Path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=40),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode._run_help_command()
        rendered = strip_ansi("\n".join(mode.history.render(1_000)))

        assert (
            "/compact deep [focus] - Create an aggressive bounded generational checkpoint."
            in rendered
        )
        assert "multi-pass compaction" not in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_compress_deep_remains_a_manual_deep_alias() -> None:
    assert _manual_compression_options("/compress deep context envelope") == (
        "context envelope",
        True,
    )


def test_interactive_trust_command_persists_without_executing_project_code(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    sentinel = tmp_path / "executed"
    extension_path = tmp_path / ".travis234" / "extensions" / "unsafe.py"
    extension_path.parent.mkdir(parents=True)
    extension_path.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
        "def extension(travis):\n"
        "    return None\n",
        encoding="utf-8",
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(agent_dir),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.prompt_extension_select = lambda title, choices, options=None, **kwargs: "Trust"

    try:
        mode._run_trust_command()

        assert ProjectTrustStore(agent_dir).get(tmp_path) is True
        assert sentinel.exists() is False
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "Run /reload or restart" in history
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_mode_binds_extension_ui_before_session_start(tmp_path) -> None:
    extension_path = tmp_path / ".travis234" / "extensions" / "ui.py"
    extension_path.parent.mkdir(parents=True)
    extension_path.write_text(
        "def extension(travis):\n"
        "    def started(event, ctx):\n"
        "        ctx.ui.set_status('loaded-extension', 'ready')\n"
        "    travis.on('session_start', started)\n",
        encoding="utf-8",
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        assert mode.extension_statuses == {}
        mode.init()
        assert mode.extension_statuses == {"loaded-extension": "ready"}
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_reload_clears_old_extension_ui_before_new_session_start(tmp_path) -> None:
    terminal = FakeTerminal(columns=100, rows=30)
    extension_path = tmp_path / ".travis234" / "extensions" / "ui.py"
    extension_path.parent.mkdir(parents=True)
    extension_path.write_text(
        "def extension(travis):\n"
        "    def started(event, ctx):\n"
        "        ctx.ui.set_status('old-status', 'old')\n"
        "        ctx.ui.set_title('old extension title')\n"
        "    travis.on('session_start', started)\n",
        encoding="utf-8",
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=terminal,
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()
    assert mode.extension_statuses == {"old-status": "old"}

    extension_path.write_text(
        "def extension(travis):\n"
        "    def started(event, ctx):\n"
        "        ctx.ui.set_status('new-status', 'new')\n"
        "    travis.on('session_start', started)\n",
        encoding="utf-8",
    )
    try:
        mode._run_reload_command()

        assert mode.extension_statuses == {"new-status": "new"}
        title_writes = [write for write in terminal.writes if write.startswith("\x1b]0;")]
        assert title_writes[-1] == "\x1b]0;Travis234\x07"
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_mode_reload_refreshes_extension_code_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def provider(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(provider))
    extension_path = tmp_path / ".travis234" / "extensions" / "version.py"
    extension_path.parent.mkdir(parents=True)
    extension_path.write_text(
        "def extension(travis):\n"
        "    travis.register_command('version', {'description': 'one', 'handler': lambda args, ctx: []})\n",
        encoding="utf-8",
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    extension_path.write_text(
        "def extension(travis):\n"
        "    travis.register_command('version', {'description': 'two', 'handler': lambda args, ctx: []})\n",
        encoding="utf-8",
    )
    inputs = iter(["/reload", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    command = app.session.extension_runner.get_registered_command("version")
    history = strip_ansi("\n".join(mode.history.render(1_000)))
    assert command is not None
    assert command.description == "two"
    assert calls["model"] == 0
    assert "Extensions reloaded" in history


def test_interactive_theme_registry_connects_set_theme_and_falls_back_on_reload(tmp_path) -> None:
    themes = tmp_path / "themes"
    extensions = tmp_path / "extensions"
    themes.mkdir()
    extensions.mkdir()
    night = themes / "night.json"
    day = themes / "day.json"
    night.write_text(
        json.dumps({"name": "night", "colors": {"accent": "blue"}}),
        encoding="utf-8",
    )
    day.write_text(
        json.dumps({"name": "day", "colors": {"accent": "yellow"}}),
        encoding="utf-8",
    )
    (extensions / "theme_extension.py").write_text(
        "def extension(travis):\n"
        "    def started(event, ctx):\n"
        "        result = ctx.ui.setTheme('night')\n"
        "        ctx.ui.set_status('theme-selected', str(result['success']).lower())\n"
        "    travis.on('session_start', started)\n",
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory()
    settings.set_theme_paths([str(themes)])
    settings.set_extension_paths([str(extensions)])
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        assert mode.theme_registry.active_name == "night"
        assert mode.extension_statuses["theme-selected"] == "true"

        night.unlink()
        mode._run_reload_command()

        history = strip_ansi("\n".join(mode.history.render(1_000)))
        assert mode.theme_registry.active_name == "day"
        assert 'Theme "night" was removed' in history
        assert "Extensions reloaded (extensions: 0; skills: 0; prompts: 0; themes: 0)" in history
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_package_parser_ignores_ordinary_prompt_with_apostrophe(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        before = strip_ansi("\n".join(mode.history.render(500)))
        assert mode._run_package_command("Report README.md's exact byte count") is False
        assert mode._run_package_command("/packages-extra README.md's") is False
        after = strip_ansi("\n".join(mode.history.render(500)))
        assert after == before
        assert "Invalid package command" not in after
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_package_parser_reports_malformed_recognized_command(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        assert mode._run_package_command("/install 'unterminated") is True
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "Invalid package command: No closing quotation" in history
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_package_commands_confirm_mutate_and_refresh_resources(tmp_path) -> None:
    package = tmp_path / "package"
    prompts = package / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "audit.md").write_text(
        "---\ndescription: Audit files\n---\nAudit $ARGUMENTS",
        encoding="utf-8",
    )
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "audit-package",
                "travis": {"prompts": ["prompts/audit.md"]},
            }
        ),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory()
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    confirmations: list[tuple[str, str]] = []
    mode.prompt_extension_confirm = (
        lambda title, message, options=None, **kwargs: confirmations.append((title, message)) or True
    )

    try:
        mode.init()
        assert mode._run_package_command(f'/install "{package}"') is True
        assert [prompt.name for prompt in app.session.prompt_templates] == ["audit"]
        assert settings.global_settings["packages"] == [str(package)]

        assert mode._run_package_command("/packages") is True
        assert mode._run_package_command(f'/remove "{package}"') is True
        assert app.session.prompt_templates == []
        assert settings.global_settings["packages"] == []

        history = strip_ansi("\n".join(mode.history.render(2_000)))
        assert [title for title, _message in confirmations] == [
            "Install package",
            "Remove package",
        ]
        assert "Installed package" in history
        assert str(package) in history
        assert "Removed package" in history
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_package_cancel_does_not_mutate_or_reload(tmp_path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "cancelled", "travis": {"extensions": []}}),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory()
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.prompt_extension_confirm = lambda *args, **kwargs: False

    try:
        assert mode._run_package_command(f'/install "{package}"') is True
        assert "packages" not in settings.global_settings
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "Package install cancelled" in history
        assert "Extensions reloaded" not in history
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_mode_resume_rebinds_history_footer_and_session_subscription(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    target_path = _seed_tui_resume_session(agent_dir, tmp_path)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=140, rows=40),
        enable_tui=True,
        session_path=None,
        agent_dir=str(agent_dir),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "1")
    mode.init()
    old_subscription = mode._unsubscribe_session_events

    try:
        assert mode._run_resume_command() is True

        rendered = strip_ansi("\n".join(app.tui.render(140)))
        assert app.session.session_path == target_path
        assert "persisted marker" in rendered
        assert "Resumed session: resume-target" in rendered
        assert mode.footer.cwd == str(tmp_path.resolve())
        assert mode.footer.thinking_level == "medium"
        assert mode._unsubscribe_session_events is not old_subscription
        assert app.renderer.output_container is mode.history
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_new_and_session_commands_do_not_call_model(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    initial_path, initial_id = catalog.new_session_path(str(tmp_path), "initial")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=160, rows=40),
        enable_tui=True,
        session_path=initial_path,
        session_id=initial_id,
        agent_dir=str(agent_dir),
    )
    inputs = iter(["/session", "/new", "/session", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(160)))
    full_history = strip_ansi("\n".join(mode.history.render(2_000)))
    assert calls["model"] == 0
    assert app.session.session_path != initial_path
    assert "Session" in rendered
    assert f"File: {app.session.session_path}" in full_history
    assert f"ID: {app.session.session_id}" in full_history
    assert "Model: faux/faux-model" in full_history
    assert "model should not run" not in rendered

def test_interactive_mode_startup_resume_selects_before_first_editor_prompt(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    target_path = _seed_tui_resume_session(agent_dir, tmp_path, marker="startup marker")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=140, rows=40),
        enable_tui=True,
        session_path=None,
        agent_dir=str(agent_dir),
    )
    inputs = iter(["1", "/exit"])
    mode = InteractiveMode(
        app,
        input_fn=lambda prompt: next(inputs),
        open_resume_picker=True,
    )

    exit_code = mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert exit_code == 0
    assert app.session.session_path == target_path
    assert "startup marker" in rendered

def test_interactive_mode_startup_resume_cancellation_stays_ephemeral_and_exits(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    target_path = _seed_tui_resume_session(agent_dir, tmp_path)
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=140, rows=40),
        enable_tui=True,
        session_path=None,
        agent_dir=str(agent_dir),
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "", open_resume_picker=True)

    exit_code = mode.run()

    assert exit_code == 0
    assert app.session.session_path is None
    assert [str(path) for path in agent_dir.rglob("*.jsonl")] == [target_path]

def test_interactive_mode_help_and_autocomplete_include_session_commands(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=140, rows=40),
        enable_tui=True,
        session_path=None,
        agent_dir=str(tmp_path / "agent"),
    )
    inputs = iter(["/help", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "/resume - Switch to a previous session." in rendered
    assert "/new - Start a new persistent session." in rendered
    assert "/session - Show active session details." in rendered
    assert "/name <name> - Name the active session." in rendered
    assert "/fork - Fork before a selected user message." in rendered
    assert "/clone - Clone the complete active branch." in rendered
    assert "/tree - Navigate the active session tree." in rendered
    assert "/export [path] - Export HTML or JSONL." in rendered
    assert "/import <path.jsonl> - Import and switch session." in rendered
    assert "/theme [name] - Select a discovered theme." in rendered
    assert "/processes - Inspect and control managed processes." in rendered
    suggestions = mode.create_base_autocomplete_provider().get_suggestions(
        ["/se"],
        0,
        3,
        {"signal": None, "force": False},
    )
    labels = [item["label"] for item in suggestions["items"]]
    assert "session" in labels
    session_parity_labels = {
        item["label"]
        for item in mode.create_base_autocomplete_provider().get_suggestions(
            ["/"],
            0,
            1,
            {"signal": None, "force": False},
        )["items"]
    }
    assert {"name", "fork", "clone", "tree", "export", "import", "theme"} <= session_parity_labels
    process_suggestions = mode.create_base_autocomplete_provider().get_suggestions(
        ["/pro"],
        0,
        4,
        {"signal": None, "force": False},
    )
    assert "processes" in [item["label"] for item in process_suggestions["items"]]


def test_interactive_session_parity_commands_use_runtime_owners_without_model_turns(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, f"reply-{calls['model']}")

    register_api_provider(create_faux_provider(script))
    agent_dir = tmp_path / "agent"
    catalog = SessionCatalog(str(agent_dir))
    session_path, session_id = catalog.new_session_path(str(tmp_path), "parity")
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=160, rows=40),
        enable_tui=True,
        session_path=session_path,
        session_id=session_id,
        agent_dir=str(agent_dir),
    )
    app.run_turn("first")
    app.run_turn("second")
    assert calls["model"] == 2
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    try:
        mode._run_name_command("/name Release repair")
        assert app.session.session_name == "Release repair"
        assert catalog.list_for_cwd(str(tmp_path))[0].name == "Release repair"
        assert "Session name set: Release repair" in strip_ansi("\n".join(mode.history.render(2_000)))

        tree_choices: list[str] = []

        def choose_tree(title, choices, options=None, **kwargs):
            tree_choices.extend(choices)
            return next(choice for choice in choices if "user: second" in choice)

        mode.prompt_extension_select = choose_tree
        mode._run_tree_command()
        assert mode.editor_text == "second"
        assert "Navigated to selected point" in strip_ansi("\n".join(mode.history.render(2_000)))

        source_path = Path(app.session.session_path)
        source_bytes = source_path.read_bytes()
        mode._run_clone_command()
        assert Path(app.session.session_path) != source_path
        assert source_path.read_bytes() == source_bytes

        export_path = tmp_path / "exported.jsonl"
        mode._run_export_command(f"/export {export_path}")
        assert export_path.exists()
        assert calls["model"] == 2

        rendered = strip_ansi("\n".join(mode.history.render(2_000)))
        assert "Cloned to new session" in rendered
        assert f"Session exported to: {export_path}" in rendered
        assert tree_choices
    finally:
        mode.footer_data_provider.dispose()
        app.close()


@pytest.mark.parametrize("busy_state", ["turn", "compaction"])
def test_interactive_mutable_session_commands_fail_closed_while_busy(
    tmp_path,
    busy_state,
) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "reply")))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=30),
        enable_tui=True,
        session_path=str(tmp_path / "busy.jsonl"),
        agent_dir=str(tmp_path / "agent"),
    )
    app.run_turn("seed")
    original_path = app.session.session_path
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()
    if busy_state == "turn":
        app.session.agent.state.is_streaming = True
    else:
        app.session._compaction_adapter._running = True  # noqa: SLF001 - precise busy-state fixture.

    try:
        mode._run_clone_command()

        assert app.session.session_path == original_path
        rendered = strip_ansi("\n".join(mode.history.render(1_000)))
        assert f"session command unavailable while {busy_state} is active" in rendered
    finally:
        app.session.agent.state.is_streaming = False
        app.session._compaction_adapter._running = False  # noqa: SLF001
        mode.footer_data_provider.dispose()
        app.close()

def test_interactive_mode_process_completion_uses_dispatcher_without_model_turn(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=140, rows=40),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()
    before_messages = list(app.messages)
    listener = app.process_service._listeners[0]  # noqa: SLF001 - verify service/TUI subscription boundary.
    event = ProcessEvent(
        "proc_0123456789abcdef",
        ProcessState.EXITED,
        0,
        app.process_owner(),
    )
    def emit_events() -> None:
        listener(event)
        listener(event)
        listener(ProcessEvent(event.session_id, ProcessState.RUNNING, None, event.owner))
        listener(
            ProcessEvent(
                "proc_other_workspace",
                ProcessState.EXITED,
                0,
                ProcessOwner(event.owner.app_instance_id, str(tmp_path / "other"), "agent"),
            )
        )

    worker = threading.Thread(target=emit_events)
    worker.start()
    worker.join(timeout=1)

    try:
        before_drain = strip_ansi("\n".join(mode.history.render(200)))
        mode.tui.drain_dispatcher()
        after_drain = strip_ansi("\n".join(mode.history.render(200)))

        assert "proc_0123456789abcdef" not in before_drain
        assert "Process proc_0123456789abcdef exited (0)" in after_drain
        assert after_drain.count("Process proc_0123456789abcdef exited (0)") == 1
        assert "proc_other_workspace" not in after_drain
        assert app.messages == before_messages
    finally:
        if mode._unsubscribe_process_events is not None:
            mode._unsubscribe_process_events()
        mode.footer_data_provider.dispose()
        mode.tui.stop()
        app.close()

def test_interactive_mode_processes_refreshes_job_without_model_turn(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=160, rows=40),
        enable_tui=True,
    )
    bash = app.session.get_tool_definition("bash")
    assert bash is not None
    started = bash.execute("managed", {"command": "sleep 30", "yield_time_ms": 0})
    prompts = iter(["/processes", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(prompts))

    def select(title, choices, options=None, *, kind="select"):
        return "Refresh" if "action" in title.lower() else choices[0]

    mode.prompt_extension_select = select
    app.run_turn = lambda *_args, **_kwargs: pytest.fail("/processes must not call the model")
    try:
        assert mode.run() == 0
        rendered = strip_ansi("\n".join(mode.history.render(2_000)))

        assert started.details["sessionId"] in rendered
        assert "running" in rendered
        assert app.process_service._listeners == []  # noqa: SLF001 - run() must unsubscribe.
    finally:
        app.close()

@pytest.mark.parametrize(
    ("selected_action", "service_method", "expected_kwargs"),
    [
        ("Refresh", "poll", {"wait_ms": 0, "max_bytes": 8192}),
        ("Interrupt", "interrupt", {"wait_ms": 0}),
        ("Terminate", "terminate", {"wait_ms": 250}),
        ("Kill", "kill", {}),
    ],
)
def test_interactive_mode_processes_routes_explicit_controls(
    tmp_path,
    monkeypatch,
    selected_action,
    service_method,
    expected_kwargs,
) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=80, rows=24),
        enable_tui=True,
    )
    snapshot = ProcessSnapshot(
        session_id="proc_0123456789abcdef0123456789abcdef",
        state=ProcessState.RUNNING,
        output="",
        cursor=0,
        next_cursor=0,
        output_size=0,
        exit_code=None,
        tty=False,
        elapsed_ms=1_000,
        command="printf ready",
        cwd=str(tmp_path),
    )
    calls = []
    monkeypatch.setattr(app.process_service, "list", lambda owner: (snapshot,))

    def invoke(owner, session_id, *args, **kwargs):
        calls.append((owner, session_id, args, kwargs))
        return snapshot

    monkeypatch.setattr(app.process_service, service_method, invoke)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    def select(title, choices, options=None, *, kind="select"):
        if title == "Managed processes":
            return choices[0]
        assert choices == ["Refresh", "Interrupt", "Terminate", "Kill"]
        return selected_action

    mode.prompt_extension_select = select
    before_messages = list(app.messages)
    try:
        mode._run_processes_command()

        expected_args = (0,) if service_method == "poll" else ()
        assert calls == [(app.process_owner(), snapshot.session_id, expected_args, expected_kwargs)]
        assert app.messages == before_messages
    finally:
        mode.footer_data_provider.dispose()
        app.close()

def test_interactive_mode_terminal_process_offers_only_refresh(tmp_path, monkeypatch) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=80, rows=24),
        enable_tui=True,
    )
    snapshot = ProcessSnapshot(
        session_id="proc_0123456789abcdef0123456789abcdef",
        state=ProcessState.EXITED,
        output="",
        cursor=0,
        next_cursor=0,
        output_size=0,
        exit_code=0,
        tty=True,
        elapsed_ms=1_000,
        command="x" * 200,
        cwd=str(tmp_path),
    )
    monkeypatch.setattr(app.process_service, "list", lambda owner: (snapshot,))
    monkeypatch.setattr(app.process_service, "poll", lambda *args, **kwargs: snapshot)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    seen = []

    def select(title, choices, options=None, *, kind="select"):
        seen.append((title, choices))
        return choices[0]

    mode.prompt_extension_select = select
    try:
        mode._run_processes_command()

        assert seen[1] == ("Process action", ["Refresh"])
        assert len(seen[0][1][0]) <= len("agent | ") + 13 + len(" | exited | 1s | tty | ") + 80
    finally:
        mode.footer_data_provider.dispose()
        app.close()

@pytest.mark.parametrize(
    ("state", "actions"),
    [
        (ProcessState.RUNNING, ["Refresh", "Interrupt", "Terminate", "Kill"]),
        (ProcessState.STOPPING, ["Refresh", "Kill"]),
        (ProcessState.DRAINING, ["Refresh"]),
        (ProcessState.EXITED, ["Refresh"]),
    ],
)
def test_interactive_mode_process_actions_match_state(state, actions) -> None:
    assert InteractiveMode._process_actions(state) == actions

def test_interactive_mode_reports_unknown_slash_command_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["/does-not-exist", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "Unknown command: /does-not-exist" in rendered
    assert "Type /help for available commands." in rendered
    assert "model should not run" not in rendered

def test_interactive_mode_routes_subagents_prompt_trigger_to_model(tmp_path) -> None:
    prompts: list[str] = []

    def script(model, context):
        prompts.append(context.messages[-1].content[0].text)
        return text_response_events(model, "subagent prompt received")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompt = "/subagents delegate one reviewer to inspect package.json"
    inputs = iter([prompt, "/exit"])

    InteractiveMode(app, input_fn=lambda input_prompt: next(inputs)).run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert prompts == [prompt]
    assert "subagent prompt received" in rendered
    assert "Unknown command: /subagents" not in rendered

def test_interactive_mode_runs_delegate_command_through_turn_thread(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    def backend(task):
        return "delegate command ok"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("test", backend))
    inputs = iter(["/delegate --backend test reviewer inspect package.json", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls["model"] == 0
    assert "delegate command ok" in rendered
    assert "Unknown command: /delegate" not in rendered
    assert "model should not run" not in rendered

def test_interactive_mode_does_not_run_delegate_command_on_input_thread(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()

    assert mode._dispatch_extension_command("/delegate reviewer inspect package.json") is False

def test_interactive_mode_runs_agents_command_during_active_turn_without_queueing(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=120, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    stop = threading.Event()
    active_turn = threading.Thread(target=stop.wait)

    mode.init()
    active_turn.start()
    try:
        with mode._turn_lock:
            mode._turn_thread = active_turn

        assert mode._handle_active_turn_prompt("/agents") is True

        rendered = strip_ansi("\n".join(app.tui.render(120)))
        assert "No subagents have been spawned" in rendered
        assert "Queued message for after current turn" not in rendered
        assert mode._queued_after_turn == []
    finally:
        stop.set()
        active_turn.join(timeout=1)

def test_interactive_mode_runs_agents_command_while_subagent_tool_waits(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    provider_calls = {"n": 0}
    provider_context_texts: list[str] = []

    def script(model, context):
        provider_calls["n"] += 1
        context_text_parts: list[str] = []
        for message in context.messages:
            content = getattr(message, "content", [])
            if isinstance(content, str):
                context_text_parts.append(content)
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    context_text_parts.append(str(text))
        provider_context_texts.append("\n".join(context_text_parts))
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {"role": "reviewer", "goal": "slow review", "wait": True, "timeoutSeconds": 30},
            )
        return text_response_events(model, "parent done")

    def slow_backend(task):
        started.set()
        release.wait(2)
        return "child done"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    prompt_count = {"n": 0}

    def input_fn(_prompt: str) -> str:
        prompt_count["n"] += 1
        if prompt_count["n"] == 1:
            return "spawn a slow reviewer subagent"
        if prompt_count["n"] == 2:
            assert started.wait(timeout=2)
            return "/agents"
        release.set()
        return "/exit"

    mode = InteractiveMode(app, input_fn=input_fn)

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert provider_calls["n"] == 2
    assert "Subagents:" in rendered
    assert "reviewer" in rendered
    assert "running - slow review" in rendered
    assert "Queued message for after current turn" not in rendered
    assert "parent done" in rendered
    assert all("Subagents:" not in text for text in provider_context_texts)

def test_interactive_mode_live_terminal_runs_agents_command_while_subagent_tool_waits(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    provider_calls = {"n": 0}

    def script(model, context):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {"role": "reviewer", "goal": "slow review", "wait": True, "timeoutSeconds": 30},
            )
        return text_response_events(model, "parent done")

    def slow_backend(task):
        started.set()
        release.wait(2)
        return "child done"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    mode = InteractiveMode(app)
    outcome: dict[str, object] = {}

    def run_mode() -> None:
        try:
            outcome["code"] = mode.run()
        except BaseException as error:  # noqa: BLE001 - test thread must surface failures.
            outcome["error"] = error

    thread = threading.Thread(target=run_mode)
    thread.start()
    try:
        assert _wait_until(lambda: terminal.input_handler is not None and mode.active_editor is not None)
        assert terminal.input_handler is not None

        terminal.input_handler("spawn a slow reviewer subagent\r")
        assert started.wait(timeout=2)
        assert mode._is_turn_active()

        terminal.input_handler("/agents\r")

        assert _wait_until(
            lambda: "Subagents:" in strip_ansi(terminal.output)
            and "running - slow review" in strip_ansi(terminal.output),
            timeout=0.5,
        )

        release.set()
        assert _wait_until(lambda: not mode._is_turn_active() and mode.active_editor is not None, timeout=2)
        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        release.set()
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0
    assert provider_calls["n"] == 2

def test_interactive_mode_live_terminal_ctrl_c_cancels_waiting_subagent_tool(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    provider_calls = {"n": 0}

    def script(model, context):
        provider_calls["n"] += 1
        return tool_call_response_events(
            model,
            "spawn_subagent",
            {"role": "reviewer", "goal": "slow review", "wait": True, "timeoutSeconds": 30},
        )

    def slow_backend(task):
        started.set()
        release.wait(2)
        return "late summary"

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    mode = InteractiveMode(app)
    outcome: dict[str, object] = {}

    def run_mode() -> None:
        try:
            outcome["code"] = mode.run()
        except BaseException as error:  # noqa: BLE001 - test thread must surface failures.
            outcome["error"] = error

    thread = threading.Thread(target=run_mode)
    thread.start()
    try:
        assert _wait_until(lambda: terminal.input_handler is not None and mode.active_editor is not None)
        assert terminal.input_handler is not None

        terminal.input_handler("spawn a slow reviewer subagent\r")
        assert started.wait(timeout=2)
        assert mode._is_turn_active()

        terminal.input_handler("\x03")

        assert _wait_until(
            lambda: not mode._is_turn_active()
            and mode.status._message == "Idle"
            and mode.active_editor is not None,
            timeout=2,
        )
        results = app.session.subagents.list_results()
        assert len(results) == 1
        assert results[0].status == "cancelled"
        assert results[0].errors == ["Cancelled by parent abort."]

        release.set()
        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        release.set()
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0
    assert provider_calls["n"] == 1

def test_interactive_mode_hides_successful_subagent_tool_trace_event(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "unused")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    mode._handle_session_event(
        {
            "type": "subagent_tool_end",
            "taskId": "subagent-fixed",
            "role": "reviewer",
            "toolName": "read",
            "status": "ok",
            "argsPreview": "path=child.md",
            "resultPreview": "child trace body",
        }
    )

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "subagent reviewer read ok path=child.md" not in rendered
    assert "child trace body" not in rendered

def test_interactive_mode_dispatches_extension_shortcut_without_model_turn(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "model should not run")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=120, rows=40)
    model = faux_model()
    model.context_window = 1000
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal, enable_tui=True)
    contexts: list[dict[str, object]] = []

    def handle_shortcut(ctx):
        contexts.append(ctx)
        ctx["ui"].notify("shortcut ran")

    app.session.extension_runner.register_shortcut(
        "ctrl+y",
        {"description": "Run shortcut", "handler": handle_shortcut},
    )
    inputs = iter(["ctrl+y", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert calls["model"] == 0
    assert "shortcut ran" in rendered
    assert "ctrl+y" not in rendered
    assert len(contexts) == 1
    assert contexts[0]["mode"] == "tui"
    assert contexts[0]["hasUI"] is True
    assert contexts[0]["cwd"] == str(tmp_path)
    assert contexts[0]["isIdle"]() is True
    context_usage = contexts[0]["getContextUsage"]()
    assert context_usage == app.session.get_context_usage()
    assert context_usage is not None
    assert {"tokens", "contextWindow", "percent"}.issubset(context_usage)
    assert isinstance(context_usage.get("confidence"), str)
    assert context_usage["contextWindow"] == 1000

def test_interactive_mode_extension_shortcut_can_set_footer_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def handle_shortcut(ctx):
        ctx["ui"].set_status("ext", "ready")

    app.session.extension_runner.register_shortcut(
        "ctrl+s",
        {"description": "Set status", "handler": handle_shortcut},
    )
    inputs = iter(["ctrl+s", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "\nready" in rendered
    assert "ctrl+s" not in rendered

def test_interactive_mode_extension_shortcut_can_set_working_message(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_working(ctx):
        ctx["ui"].set_working_message("Indexing workspace")

    app.session.extension_runner.register_shortcut(
        "ctrl+w",
        {"description": "Set working", "handler": set_working},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+w") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: Indexing workspace" in rendered
    assert "ctrl+w" not in rendered

def test_interactive_mode_extension_shortcut_can_hide_working_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def hide_working(ctx):
        ctx["ui"].set_working_message("Hidden extension status")
        ctx["ui"].set_working_visible(False)

    app.session.extension_runner.register_shortcut(
        "ctrl+h",
        {"description": "Hide working", "handler": hide_working},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+h") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: Hidden extension status" not in rendered
    assert "faux-model" in rendered
    assert "ctrl+h" not in rendered

def test_interactive_mode_extension_shortcut_can_set_working_indicator(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_indicator(ctx):
        ctx["ui"].set_working_message("Indexing workspace")
        ctx["ui"].set_working_indicator({"frames": ["*"]})

    app.session.extension_runner.register_shortcut(
        "ctrl+i",
        {"description": "Set indicator", "handler": set_indicator},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+i") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: * Indexing workspace" in rendered
    assert "ctrl+i" not in rendered

def test_interactive_mode_extension_shortcut_can_prompt_for_input(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[str | None] = []

    def ask_for_input(ctx):
        captured.append(ctx["ui"].input("Project name", "travis"))

    app.session.extension_runner.register_shortcut(
        "ctrl+n",
        {"description": "Ask for input", "handler": ask_for_input},
    )
    inputs = iter(["ported-ui"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+n") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["ported-ui"]
    assert prompts == ["Project name (travis): "]
    assert "input: Project name" in rendered
    assert "ported-ui" in rendered
    assert "ctrl+n" not in rendered

def test_interactive_mode_extension_shortcut_can_select_option(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[str | None] = []

    def pick_option(ctx):
        captured.append(ctx["ui"].select("Deployment target", ["staging", "production"]))

    app.session.extension_runner.register_shortcut(
        "ctrl+d",
        {"description": "Pick target", "handler": pick_option},
    )
    inputs = iter(["2"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+d") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["production"]
    assert prompts == ["Deployment target [1-2]: "]
    assert "select: Deployment target" in rendered
    assert "1. staging" in rendered
    assert "2. production" in rendered
    assert "production" in rendered
    assert "ctrl+d" not in rendered

def test_interactive_mode_extension_shortcut_can_confirm(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[bool] = []

    def confirm_action(ctx):
        captured.append(ctx["ui"].confirm("Delete deployment?", "This cannot be undone"))

    app.session.extension_runner.register_shortcut(
        "ctrl+delete",
        {"description": "Confirm delete", "handler": confirm_action},
    )
    inputs = iter(["1"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+delete") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == [True]
    assert prompts == ["Delete deployment? This cannot be undone [1-2]: "]
    assert "confirm: Delete deployment? This cannot be undone" in rendered
    assert "1. Yes" in rendered
    assert "2. No" in rendered
    assert "Yes" in rendered
    assert "ctrl+delete" not in rendered

def test_interactive_mode_extension_shortcut_can_listen_to_terminal_input(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "listener reply")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    seen: list[str] = []
    unsubscribe_holder: list[object] = []

    def install_listener(ctx):
        def listener(data):
            seen.append(data)
            if data == "rewrite":
                unsubscribe_holder[0]()
                return {"data": "rewritten prompt"}
            return None

        unsubscribe_holder.append(ctx["ui"].on_terminal_input(listener))

    app.session.extension_runner.register_shortcut(
        "ctrl+l",
        {"description": "Install listener", "handler": install_listener},
    )
    inputs = iter(["ctrl+l", "rewrite", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert calls["model"] == 1
    assert seen == ["rewrite"]
    assert "rewritten prompt" in rendered
    assert "listener reply" in rendered
    assert "rewrite" not in rendered
    assert "ctrl+l" not in rendered

def test_interactive_mode_extension_shortcut_can_set_hidden_thinking_label(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        AssistantMessage(
            content=[ThinkingContent(thinking="private chain of thought"), TextContent(text="Visible answer")],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
    ]

    def set_label(ctx):
        ctx["ui"].set_hidden_thinking_label("Reasoning hidden")

    app.session.extension_runner.register_shortcut(
        "ctrl+t",
        {"description": "Hide thinking", "handler": set_label},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.hide_thinking_block = True

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+t") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Reasoning hidden" in rendered
    assert "Visible answer" in rendered
    assert "private chain of thought" not in rendered
    assert "ctrl+t" not in rendered

def test_interactive_mode_hides_existing_thinking_content_by_default(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.agent.state.messages = [
        AssistantMessage(
            content=[
                ThinkingContent(thinking="private replayed chain of thought"),
                TextContent(text="Visible replayed answer"),
            ],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
    ]

    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    mode.init()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Visible replayed answer" in rendered
    assert "private replayed chain of thought" not in rendered
    assert "Thinking:" not in rendered

def test_interactive_mode_extension_shortcut_can_set_terminal_title(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_title(ctx):
        ctx["ui"].set_title("travis - workspace")

    app.session.extension_runner.register_shortcut(
        "ctrl+shift+t",
        {"description": "Set title", "handler": set_title},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+shift+t") is True

    assert "\x1b]0;travis - workspace\x07" in terminal.output

def test_interactive_mode_extension_shortcut_can_set_and_clear_widgets(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_widgets(ctx):
        ctx["ui"].set_widget("above", ["Above editor widget"])
        ctx["ui"].set_widget("below", ["Below editor widget"], {"placement": "belowEditor"})

    def replace_widgets(ctx):
        ctx["ui"].set_widget("above", ["Above replacement"])
        ctx["ui"].set_widget("below", None)

    app.session.extension_runner.register_shortcut(
        "ctrl+u",
        {"description": "Set widgets", "handler": set_widgets},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+u",
        {"description": "Replace widgets", "handler": replace_widgets},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+u") is True

    rendered_lines = [strip_ansi(line) for line in app.tui.render(140)]
    above_index = rendered_lines.index("Above editor widget")
    below_index = rendered_lines.index("Below editor widget")
    status_index = next(index for index, line in enumerate(rendered_lines) if line.startswith("status:"))
    assert above_index < below_index < status_index

    assert mode._dispatch_extension_shortcut("ctrl+shift+u") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Above replacement" in rendered
    assert "Above editor widget" not in rendered
    assert "Below editor widget" not in rendered

def test_interactive_mode_extension_shortcut_can_replace_and_restore_footer(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    class DynamicFooter(Component):
        def __init__(self, provider) -> None:
            self.provider = provider
            self.disposed = False

        def render(self, width: int) -> list[str]:
            statuses = self.provider.get_extension_statuses()
            return [f"custom footer: plan={statuses.get('plan', 'missing')}"]

        def dispose(self) -> None:
            self.disposed = True

    custom_footers: list[DynamicFooter] = []

    def set_footer(ctx):
        ctx["ui"].set_status("plan", "ready")

        def make_footer(tui, theme, footer_data):
            footer = DynamicFooter(footer_data)
            custom_footers.append(footer)
            return footer

        ctx["ui"].set_footer(make_footer)

    def restore_footer(ctx):
        ctx["ui"].set_footer(None)

    app.session.extension_runner.register_shortcut(
        "ctrl+f",
        {"description": "Set footer", "handler": set_footer},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+f",
        {"description": "Restore footer", "handler": restore_footer},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+f") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "custom footer: plan=ready" in rendered
    assert "model: faux-model" not in rendered
    assert custom_footers and custom_footers[-1].disposed is False

    assert mode._dispatch_extension_shortcut("ctrl+shift+f") is True

    restored = strip_ansi("\n".join(app.tui.render(140)))
    assert custom_footers[-1].disposed is True
    assert "custom footer" not in restored
    assert "faux-model" in restored
    assert "\nready" in restored

def test_interactive_footer_data_provider_ports_travis234_nested_git_branch_and_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "nested"
    git_dir = repo / ".git"
    nested.mkdir(parents=True)
    git_dir.mkdir()
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n")

    app = CodingApp(cwd=str(nested), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    provider = mode.footer_data_provider

    try:
        assert provider.get_git_branch() == "main"
        assert provider.get_available_provider_count() == 0

        seen: list[str | None] = []
        unsubscribe = provider.on_branch_change(lambda: seen.append(provider.get_git_branch()))
        head.write_text("ref: refs/heads/feature\n")
        provider.refresh_git_branch()
        unsubscribe()
        head.write_text("ref: refs/heads/ignored\n")
        provider.refresh_git_branch()

        assert provider.get_git_branch() == "ignored"
        assert seen == ["feature"]
    finally:
        provider.dispose()

def test_interactive_footer_data_provider_ports_travis234_worktree_and_detached_resolution(tmp_path) -> None:
    common_git_dir = tmp_path / "repo" / ".git"
    git_dir = common_git_dir / "worktrees" / "src"
    worktree = tmp_path / "worktree"
    git_dir.mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/worktree-branch\n")
    (git_dir / "commondir").write_text("../..\n")

    app = CodingApp(cwd=str(worktree), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    try:
        assert mode.footer_data_provider.get_git_branch() == "worktree-branch"
    finally:
        mode.footer_data_provider.dispose()

    detached = tmp_path / "detached"
    detached_git_dir = detached / ".git"
    detached_git_dir.mkdir(parents=True)
    (detached_git_dir / "HEAD").write_text("abcdef123456\n")
    detached_app = CodingApp(cwd=str(detached), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    detached_mode = InteractiveMode(detached_app, input_fn=lambda prompt: "/exit")
    try:
        assert detached_mode.footer_data_provider.get_git_branch() == "detached"
    finally:
        detached_mode.footer_data_provider.dispose()

def test_interactive_mode_builtin_footer_renders_travis234_git_branch(tmp_path) -> None:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    repo.mkdir()
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    app = CodingApp(cwd=str(repo), model=faux_model(), terminal=FakeTerminal(columns=360), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(360)))
        assert f"{repo} (main)" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_footer_data_provider_auto_refreshes_head_changes_and_rerenders(tmp_path) -> None:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    repo.mkdir()
    git_dir.mkdir()
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n")
    app = CodingApp(cwd=str(repo), model=faux_model(), terminal=FakeTerminal(columns=360), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")
    seen: list[str | None] = []

    mode.init()
    unsubscribe = mode.footer_data_provider.on_branch_change(lambda: seen.append(mode.footer_data_provider.get_git_branch()))
    try:
        assert mode.footer_data_provider.get_git_branch() == "main"

        head.write_text("ref: refs/heads/feature\n")

        def refreshed() -> bool:
            app.tui.drain_dispatcher()
            return seen == ["feature"] and f"{repo} (feature)" in strip_ansi("\n".join(app.tui.render(360)))

        assert _wait_until(refreshed)
    finally:
        unsubscribe()
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_footer_ports_travis234_available_provider_count_for_scoped_models(tmp_path) -> None:
    primary = faux_model()
    primary.base_url = "http://localhost"
    secondary = faux_model(api="other")
    secondary.provider = "other"
    secondary.id = "other-model"
    secondary.name = "Other"
    secondary.base_url = "http://localhost"
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    register_api_provider(ApiProvider(api="other", stream=lambda *args: None, stream_simple=lambda *args: None))
    app = CodingApp(
        cwd=str(tmp_path),
        model=primary,
        scoped_models=[ScopedModel(model=primary), ScopedModel(model=secondary)],
        terminal=FakeTerminal(columns=140),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(140)))

        assert mode.footer_data_provider.get_available_provider_count() == 2
        assert "(faux) faux-model" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_footer_ports_travis234_usage_stats_from_session_messages(tmp_path) -> None:
    model = faux_model()
    model.context_window = 200_000
    usage = Usage(input=12345, output=6789, cache_read=50, cache_write=50)
    usage.cost = Cost(total=1.234)
    assistant = AssistantMessage(
        content=[TextContent(text="done")],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=usage,
        stop_reason="stop",
        timestamp=now_ms(),
    )
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(columns=160), enable_tui=True)
    app.session.agent.state.messages = [assistant]
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert "↑12k ↓6.8k R50 W50 CH0.4% $1.234" in rendered
        assert "faux-model" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_footer_ports_travis234_unknown_context_usage(tmp_path) -> None:
    model = faux_model()
    model.context_window = 200_000
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(columns=160), enable_tui=True)
    app.session.get_context_usage = lambda: {"tokens": None, "contextWindow": 200_000, "percent": None}
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert "?/200k (auto)" in rendered
        assert "0.0%/200k" not in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_footer_marks_estimated_context_usage_with_tilde(tmp_path) -> None:
    model = faux_model()
    model.context_window = 200_000
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(columns=160), enable_tui=True)
    app.session.get_context_usage = lambda: {
        "tokens": 20_000,
        "contextWindow": 200_000,
        "percent": 10.0,
        "estimated": True,
    }
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert "~10.0%/200k (auto)" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_footer_ports_travis234_session_name_updates(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=160), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        mode.init()
        app.session.set_session_name("work session")
        rendered = strip_ansi("\n".join(app.tui.render(160)))

        assert f"{tmp_path} • work session" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.tui.stop()

def test_interactive_mode_extension_shortcut_can_replace_and_restore_header(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    class DynamicHeader(Component):
        def __init__(self) -> None:
            self.disposed = False

        def render(self, width: int) -> list[str]:
            return ["custom header", "extension startup"]

        def dispose(self) -> None:
            self.disposed = True

    custom_headers: list[DynamicHeader] = []

    def set_header(ctx):
        def make_header(tui, theme):
            header = DynamicHeader()
            custom_headers.append(header)
            return header

        ctx["ui"].set_header(make_header)

    def restore_header(ctx):
        ctx["ui"].set_header(None)

    app.session.extension_runner.register_shortcut(
        "ctrl+g",
        {"description": "Set header", "handler": set_header},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+g",
        {"description": "Restore header", "handler": restore_header},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+g") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "custom header" in rendered
    assert "extension startup" in rendered
    assert "Travis234 TUI" not in rendered
    assert custom_headers and custom_headers[-1].disposed is False

    assert mode._dispatch_extension_shortcut("ctrl+shift+g") is True

    restored = strip_ansi("\n".join(app.tui.render(140)))
    assert custom_headers[-1].disposed is True
    assert "custom header" not in restored
    assert "Travis234 TUI" in restored

def test_interactive_mode_extension_shortcut_can_control_editor_text(tmp_path) -> None:
    calls = {"model": 0}

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "editor submitted")

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    captured: list[str] = []

    def edit_buffer(ctx):
        ctx["ui"].set_editor_text("prefill")
        ctx["ui"].paste_to_editor(" + pasted")
        captured.append(ctx["ui"].get_editor_text())

    app.session.extension_runner.register_shortcut(
        "ctrl+e",
        {"description": "Edit buffer", "handler": edit_buffer},
    )
    inputs = iter(["ctrl+e", "", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["prefill + pasted"]
    assert calls["model"] == 1
    assert "prefill + pasted" in rendered
    assert "editor submitted" in rendered
    assert "ctrl+e" not in rendered

def test_interactive_mode_extension_shortcut_can_open_multiline_editor(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    prompts: list[str] = []
    captured: list[str | None] = []

    def open_editor(ctx):
        captured.append(ctx["ui"].editor("Edit handoff prompt", "prefill line 1\nprefill line 2"))

    app.session.extension_runner.register_shortcut(
        "ctrl+m",
        {"description": "Open editor", "handler": open_editor},
    )
    inputs = iter(["edited line 1\nedited line 2"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+m") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert captured == ["edited line 1\nedited line 2"]
    assert prompts == ["Edit handoff prompt: "]
    assert "editor: Edit handoff prompt" in rendered
    assert "prefill line 1" in rendered
    assert "prefill line 2" in rendered
    assert "edited line 1" in rendered
    assert "edited line 2" in rendered
    assert "ctrl+m" not in rendered

def test_interactive_mode_extension_shortcut_can_add_autocomplete_provider(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.session.extension_runner.register_command(
        "review",
        {"description": "Review files", "handler": lambda args, ctx=None: None},
    )
    app.session.extension_runner.register_command(
        "deploy",
        {
            "description": "Deploy to environment",
            "getArgumentCompletions": lambda prefix: [
                {"value": env, "label": env} for env in ("dev", "staging", "prod") if env.startswith(prefix)
            ],
            "handler": lambda args, ctx=None: None,
        },
    )

    def install_provider(ctx):
        def wrap(current):
            class IssueProvider:
                trigger_characters = ["#"]

                def get_suggestions(self, lines, cursor_line, cursor_col, options):
                    before_cursor = (lines[cursor_line] if cursor_line < len(lines) else "")[:cursor_col]
                    if not before_cursor.endswith("#2"):
                        return current.get_suggestions(lines, cursor_line, cursor_col, options)
                    return {
                        "prefix": "#2",
                        "items": [
                            {
                                "value": "#2983",
                                "label": "#2983",
                                "description": "Extension API for autocomplete",
                            }
                        ],
                    }

                def apply_completion(self, lines, cursor_line, cursor_col, item, prefix):
                    return current.apply_completion(lines, cursor_line, cursor_col, item, prefix)

                def should_trigger_file_completion(self, lines, cursor_line, cursor_col):
                    return current.should_trigger_file_completion(lines, cursor_line, cursor_col)

            return IssueProvider()

        ctx["ui"].add_autocomplete_provider(wrap)

    app.session.extension_runner.register_shortcut(
        "ctrl+a",
        {"description": "Install autocomplete", "handler": install_provider},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+a") is True

    issue_suggestions = mode.get_autocomplete_suggestions(["please fix #2"], 0, len("please fix #2"))
    assert issue_suggestions == {
        "prefix": "#2",
        "items": [
            {
                "value": "#2983",
                "label": "#2983",
                "description": "Extension API for autocomplete",
            }
        ],
    }

    slash_suggestions = mode.get_autocomplete_suggestions(["/rev"], 0, len("/rev"))
    assert slash_suggestions == {
        "prefix": "/rev",
        "items": [
            {"value": "review", "label": "review", "description": "Review files"},
            {
                "value": "remove",
                "label": "remove",
                "description": "Remove an installed resource package",
            },
        ],
    }
    argument_suggestions = mode.get_autocomplete_suggestions(["/deploy st"], 0, len("/deploy st"))
    assert argument_suggestions == {
        "prefix": "st",
        "items": [{"value": "staging", "label": "staging"}],
    }
    assert mode.autocomplete_provider.trigger_characters == ["#"]

    editor = Input("please fix #2")
    editor.set_autocomplete_provider(mode.autocomplete_provider)
    editor.handle_input("\t")
    assert editor.get_value() == "please fix #2983"

def test_interactive_mode_extension_shortcut_can_open_custom_component(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    class ConfirmComponent(Component):
        def __init__(self, done) -> None:
            self.done = done
            self.inputs: list[str] = []
            self.disposed = False

        def render(self, width: int) -> list[str]:
            return ["custom confirm", "press enter to accept"]

        def handle_input(self, data: str) -> None:
            self.inputs.append(data)
            if data == "\r":
                self.done({"accepted": True})

        def dispose(self) -> None:
            self.disposed = True

    captured: list[object] = []
    components: list[ConfirmComponent] = []
    factory_args: list[tuple[object, object, object]] = []

    def open_custom(ctx):
        def make_component(tui, theme, keybindings, done):
            factory_args.append((tui, theme, keybindings))
            component = ConfirmComponent(done)
            components.append(component)
            return component

        captured.append(ctx["ui"].custom(make_component))

    app.session.extension_runner.register_shortcut(
        "ctrl+k",
        {"description": "Open custom component", "handler": open_custom},
    )
    inputs = iter(["\r"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.init()
    assert mode._dispatch_extension_shortcut("ctrl+k") is True

    rendered_now = strip_ansi("\n".join(app.tui.render(140)))
    rendered_history = strip_ansi(terminal.output)
    assert captured == [{"accepted": True}]
    assert len(components) == 1
    assert factory_args == [(app.tui, None, None)]
    assert components[0].inputs == ["\r"]
    assert components[0].disposed is True
    assert "custom confirm" in rendered_history
    assert "custom confirm" not in rendered_now
    assert "press enter to accept" not in rendered_now
    assert "ctrl+k" not in rendered_now

def test_tui_footer_status_diff_and_width_constraints() -> None:
    terminal = FakeTerminal(columns=24)
    tui = TUI(terminal)
    footer = FooterComponent(cwd="/tmp/very/long/project/path", model="faux-model", thinking_level="off")
    status = StatusLine("Idle")
    tui.add(footer)
    tui.add(status)

    first = tui.request_render()
    status.set_message("Working on a long operation")
    second = tui.request_render()

    assert first.full is True
    assert second.full is False
    assert second.first_changed == 2
    assert all(visible_width(line) <= 24 for line in second.lines)

def test_tui_diff_render_keeps_complete_history_and_addresses_only_visible_tail() -> None:
    terminal = FakeTerminal(columns=80, rows=5)
    tui = TUI(terminal)
    for index in range(8):
        tui.add(Text(f"history {index}"))
    footer = StatusLine("Idle")
    tui.add(footer)

    first = tui.request_render()
    footer.set_message("Running")
    second = tui.request_render()

    assert len(first.lines) == 9
    assert len(second.lines) == 9
    assert first.lines[:2] == ["history 0", "history 1"]
    assert second.lines[-1] == "status: Running"
    assert "\x1b[6;1H" not in terminal.writes[-1]
    assert "\x1b[9;1H" not in terminal.writes[-1]

def test_interactive_mode_renders_real_prompt_loop(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "tui reply")))
    terminal = FakeTerminal(columns=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    inputs = iter(["hi", "/exit"])
    input_prompts = []
    mode = InteractiveMode(
        app,
        input_fn=lambda prompt: input_prompts.append(prompt) or next(inputs),
    )

    exit_code = mode.run()

    rendered = strip_ansi(terminal.output)
    assert exit_code == 0
    assert "Travis234" in rendered
    assert "Current working directory:" in rendered
    assert "hi" in rendered
    assert "> hi" not in rendered
    assert "tui reply" in rendered
    assert '{"type":' not in rendered
    assert input_prompts == ["", ""]

def test_interactive_mode_queues_prompt_while_turn_is_streaming(tmp_path) -> None:
    first_stream_started = threading.Event()
    first_stream_released = threading.Event()
    first_stream_finished = threading.Event()
    second_input_requested = threading.Event()
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        events = text_response_events(model, f"turn {stream_calls['n']}")
        if stream_calls["n"] > 1:
            provider = create_faux_provider(lambda m, c: events)
            return provider.stream_simple(model, context, options)

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
    input_calls = {"n": 0}

    def input_fn(prompt: str) -> str:
        index = input_calls["n"]
        input_calls["n"] += 1
        if index == 0:
            return "first"
        if index == 1:
            first_stream_started.wait(timeout=2)
            second_input_requested.set()
            return "second"
        if index == 2:
            first_stream_finished.wait(timeout=2)
            return "/exit"
        raise EOFError

    mode = InteractiveMode(app, input_fn=input_fn)
    thread = threading.Thread(target=mode.run)
    thread.start()

    assert first_stream_started.wait(timeout=2)
    try:
        assert second_input_requested.wait(timeout=0.25)
        assert _wait_until(lambda: app.session.get_steering_messages() == ["second"], timeout=0.25)
        assert app.session.pending_message_count == 1
    finally:
        first_stream_released.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
