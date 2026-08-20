"""TUI extension shortcuts, widgets, footer, and live prompt behavior."""


from __future__ import annotations


from tests._support_tui import *  # noqa: F403


from travis.tui import Editor


def _raw_shortcut(key_id: str) -> str:
    parts = key_id.split("+")
    key = parts[-1]
    modifiers = set(parts[:-1])
    modifier = 1
    modifier += 4 if "ctrl" in modifiers else 0
    modifier += 2 if "alt" in modifiers else 0
    modifier += 1 if "shift" in modifiers else 0
    modifier += 8 if "super" in modifiers else 0
    functional = {"delete": 57426}
    codepoint = functional[key] if key in functional else ord(key)
    return f"\x1b[{codepoint};{modifier}u"


def _dispatch_raw_shortcut(mode: InteractiveMode, key_id: str) -> bool:
    return mode._dispatch_extension_shortcut(_raw_shortcut(key_id))


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
        "ctrl+g",
        {"description": "Run shortcut", "handler": handle_shortcut},
    )
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
        terminal.input_handler("\x07")
        assert _wait_until(lambda: "shortcut ran" in strip_ansi(terminal.output))
        terminal.input_handler("/exit\r")
        thread.join(timeout=2)
    finally:
        if thread.is_alive():
            mode._shutdown_requested = True
            if terminal.input_handler is not None:
                terminal.input_handler("/exit\r")
            thread.join(timeout=2)

    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["code"] == 0
    assert calls["model"] == 0
    assert "shortcut ran" in rendered
    assert "ctrl+g" not in rendered
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
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+s") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "\nready" in rendered
    assert "ctrl+s" not in rendered


def test_extension_shortcut_receives_raw_key_without_submit(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=40),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    seen: list[str] = []
    app.session.extension_runner.register_shortcut(
        "ctrl+g",
        {"description": "raw probe", "handler": lambda ctx: seen.append(ctx["mode"])},
    )
    editor = Editor(value="draft")
    editor.on_extension_shortcut = mode._dispatch_extension_shortcut

    try:
        mode.init()
        editor.handle_input("\x07")

        assert seen == ["tui"]
        assert editor.get_value() == "draft"
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_extension_shortcut_cannot_override_protected_editor_key(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=40),
        enable_tui=True,
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    seen: list[str] = []
    app.session.extension_runner.register_shortcut(
        "ctrl+w",
        {"description": "conflicting probe", "handler": lambda ctx: seen.append(ctx["mode"])},
    )
    editor = Editor(value="draft")
    editor.on_extension_shortcut = mode._dispatch_extension_shortcut

    try:
        mode.init()
        editor.handle_input("\x17")

        assert seen == []
        assert editor.get_value() == ""
        rendered = strip_ansi("\n".join(mode.history.render(120)))
        assert "ctrl+w" in rendered
        assert "<python-extension>" in rendered
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_line_input_does_not_emulate_shortcut_from_submitted_text(tmp_path) -> None:
    calls = {"model": 0}
    seen: list[str] = []

    def script(model, context):
        calls["model"] += 1
        return text_response_events(model, "literal prompt handled")

    register_api_provider(create_faux_provider(script))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=120, rows=40),
        enable_tui=True,
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+g",
        {"description": "raw only", "handler": lambda ctx: seen.append(ctx["mode"])},
    )
    inputs = iter(["ctrl+g", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda _prompt: next(inputs))

    mode.run()

    assert seen == []
    assert calls["model"] == 1
    rendered = strip_ansi("\n".join(app.tui.render(120)))
    assert "literal prompt handled" in rendered


def test_interactive_mode_extension_shortcut_can_set_working_message(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def set_working(ctx):
        ctx["ui"].set_working_message("Indexing workspace")

    app.session.extension_runner.register_shortcut(
        "ctrl+shift+w",
        {"description": "Set working", "handler": set_working},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+w") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "status: Indexing workspace..." in rendered
    assert "ctrl+w" not in rendered


def test_interactive_mode_extension_shortcut_can_hide_working_status(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "model should not run")))
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)

    def hide_working(ctx):
        ctx["ui"].set_working_message("Hidden extension status")
        ctx["ui"].set_working_visible(False)

    app.session.extension_runner.register_shortcut(
        "ctrl+shift+h",
        {"description": "Hide working", "handler": hide_working},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+h") is True

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
        "ctrl+shift+i",
        {"description": "Set indicator", "handler": set_indicator},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+i") is True

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
        "ctrl+shift+n",
        {"description": "Ask for input", "handler": ask_for_input},
    )
    inputs = iter(["ported-ui"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+n") is True

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
        "ctrl+shift+d",
        {"description": "Pick target", "handler": pick_option},
    )
    inputs = iter(["2"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+d") is True

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
        "ctrl+shift+delete",
        {"description": "Confirm delete", "handler": confirm_action},
    )
    inputs = iter(["1"])
    mode = InteractiveMode(app, input_fn=lambda prompt: prompts.append(prompt) or next(inputs))

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+delete") is True

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
        "ctrl+shift+l",
        {"description": "Install listener", "handler": install_listener},
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+l") is True
    consumed, rewritten = mode._dispatch_terminal_input("rewrite")

    assert consumed is False
    assert rewritten == "rewritten prompt"
    assert calls["model"] == 0
    assert seen == ["rewrite"]


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
    assert _dispatch_raw_shortcut(mode, "ctrl+t") is True

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
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+t") is True

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
        "ctrl+alt+u",
        {"description": "Set widgets", "handler": set_widgets},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+u",
        {"description": "Replace widgets", "handler": replace_widgets},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+alt+u") is True

    rendered_lines = [strip_ansi(line) for line in app.tui.render(140)]
    above_index = rendered_lines.index("Above editor widget")
    below_index = rendered_lines.index("Below editor widget")
    status_index = next(index for index, line in enumerate(rendered_lines) if line.startswith("status:"))
    assert above_index < below_index < status_index

    assert _dispatch_raw_shortcut(mode, "ctrl+shift+u") is True

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
        "ctrl+alt+f",
        {"description": "Set footer", "handler": set_footer},
    )
    app.session.extension_runner.register_shortcut(
        "ctrl+shift+f",
        {"description": "Restore footer", "handler": restore_footer},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+alt+f") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "custom footer: plan=ready" in rendered
    assert "model: faux-model" not in rendered
    assert custom_footers and custom_footers[-1].disposed is False

    assert _dispatch_raw_shortcut(mode, "ctrl+shift+f") is True

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
    assert _dispatch_raw_shortcut(mode, "ctrl+g") is True

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "custom header" in rendered
    assert "extension startup" in rendered
    assert "Travis234 TUI" not in rendered
    assert custom_headers and custom_headers[-1].disposed is False

    assert _dispatch_raw_shortcut(mode, "ctrl+shift+g") is True

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
        "ctrl+shift+e",
        {"description": "Edit buffer", "handler": edit_buffer},
    )
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+e") is True

    assert captured == ["prefill + pasted"]
    assert mode.editor_text == "prefill + pasted"
    assert calls["model"] == 0


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
    assert _dispatch_raw_shortcut(mode, "ctrl+m") is True

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
        "ctrl+shift+a",
        {"description": "Install autocomplete", "handler": install_provider},
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+a") is True

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
        "ctrl+shift+k",
        {"description": "Open custom component", "handler": open_custom},
    )
    inputs = iter(["\r"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.init()
    assert _dispatch_raw_shortcut(mode, "ctrl+shift+k") is True

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
    assert second.lines[:2] == ["history 0", "history 1"]
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
