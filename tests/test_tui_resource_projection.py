from __future__ import annotations

import json
from pathlib import Path

from tests._support_tui import (
    CodingApp,
    FakeTerminal,
    InteractiveMode,
    create_faux_provider,
    faux_model,
    register_api_provider,
    text_response_events,
)


def test_interactive_startup_projects_extension_discovered_resources(
    tmp_path: Path,
) -> None:
    resources = tmp_path / ".travis234" / "resources"
    skill = resources / "skills" / "capability-probe" / "SKILL.md"
    prompt = resources / "prompts" / "capability-check.md"
    theme = resources / "themes" / "capability-test.json"
    extension_path = tmp_path / ".travis234" / "extensions" / "resources.py"
    skill.parent.mkdir(parents=True)
    prompt.parent.mkdir(parents=True)
    theme.parent.mkdir(parents=True)
    extension_path.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: capability-probe\ndescription: Probe skill\n---\nProbe\n",
        encoding="utf-8",
    )
    prompt.write_text(
        "---\ndescription: Probe prompt\n---\nProbe $ARGUMENTS\n",
        encoding="utf-8",
    )
    theme.write_text(
        json.dumps({"name": "capability-test", "colors": {}, "vars": {}}),
        encoding="utf-8",
    )
    extension_path.write_text(
        "from pathlib import Path\n"
        "def extension(travis):\n"
        "    root = Path(__file__).resolve().parent.parent / 'resources'\n"
        "    travis.on('resources_discover', lambda event, ctx: {\n"
        "        'skillPaths': [str(root / 'skills')],\n"
        "        'promptPaths': [str(root / 'prompts')],\n"
        "        'themePaths': [str(root / 'themes')],\n"
        "    })\n",
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
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    try:
        mode.init()
        theme_names = {item.name for item in mode.theme_registry.list()}
        command = app.session.extension_runner.get_registered_command(
            "skill:capability-probe"
        )
        suggestions = mode.create_base_autocomplete_provider().get_suggestions(
            ["/capability"],
            0,
            len("/capability"),
            {"signal": None, "force": False},
        )
        labels = {item["label"] for item in suggestions["items"]}

        assert "capability-test" in theme_names
        assert command is not None
        assert {"capability-check", "skill:capability-probe"} <= labels
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_routes_extension_discovered_prompt_template_to_model(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    def script(model, context):
        prompts.append(context.messages[-1].content[0].text)
        return text_response_events(model, "template received")

    register_api_provider(create_faux_provider(script))
    prompt_root = tmp_path / ".travis234" / "resources" / "prompts"
    extension_path = tmp_path / ".travis234" / "extensions" / "prompts.py"
    prompt_root.mkdir(parents=True)
    extension_path.parent.mkdir(parents=True)
    (prompt_root / "capability-check.md").write_text(
        "---\ndescription: Probe prompt\n---\nExpanded $ARGUMENTS\n",
        encoding="utf-8",
    )
    extension_path.write_text(
        "from pathlib import Path\n"
        "def extension(travis):\n"
        "    prompts = Path(__file__).resolve().parent.parent / 'resources' / 'prompts'\n"
        "    travis.on('resources_discover', lambda event, ctx: {\n"
        "        'promptPaths': [str(prompts)],\n"
        "    })\n",
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
    inputs = iter(["/capability-check smoke", "/exit"])

    InteractiveMode(app, input_fn=lambda _prompt: next(inputs)).run()

    assert prompts == ["Expanded smoke"]
