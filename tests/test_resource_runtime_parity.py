from __future__ import annotations

from pathlib import Path

import pytest

from travis.coding_agent.prompt_templates import (
    PromptTemplate,
    expand_prompt_template,
    load_prompt_templates,
)
from travis.coding_agent.skills import load_skills, parse_frontmatter
from travis.coding_agent.source_info import create_synthetic_source_info
from travis.coding_agent.themes import Theme, ThemeRegistry
from travis.coding_agent import AgentSession, DefaultResourceLoader, SettingsManager
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import UserMessage
from tests._provider_runtime import register_api_provider, reset_api_providers, reset_models


def setup_function() -> None:
    reset_api_providers()
    reset_models()


def _user_text(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(getattr(block, "text", "") for block in message.content)


def test_resource_loader_default_agent_dir_honors_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_dir = tmp_path / "isolated-agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    loader = DefaultResourceLoader(cwd=str(tmp_path))

    assert Path(loader.agent_dir) == agent_dir.resolve()


def test_yaml_frontmatter_supports_pi_metadata_shapes() -> None:
    metadata, body = parse_frontmatter(
        """---
name: quoted-skill
description: |
  First line
  second line
allowed-tools: [read, bash]
disable-model-invocation: true
metadata:
  owner: platform
  retries: 2
---
Skill body
"""
    )

    assert metadata == {
        "name": "quoted-skill",
        "description": "First line\nsecond line\n",
        "allowed-tools": ["read", "bash"],
        "disable-model-invocation": True,
        "metadata": {"owner": "platform", "retries": 2},
    }
    assert body == "Skill body"


@pytest.mark.parametrize("frontmatter", ["- one\n- two", "plain scalar"])
def test_frontmatter_rejects_non_mapping_yaml(frontmatter: str) -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse_frontmatter(f"---\n{frontmatter}\n---\nbody")


def test_malformed_yaml_becomes_resource_diagnostic(tmp_path: Path) -> None:
    skill_file = tmp_path / "broken" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("---\nname: [unterminated\n---\nbody", encoding="utf-8")

    result = load_skills([str(tmp_path)], cwd=str(tmp_path))

    assert result["skills"] == []
    assert len(result["diagnostics"]) == 1
    assert "YAML" in result["diagnostics"][0].message


def test_skill_validation_rejects_invalid_name_and_long_description(tmp_path: Path) -> None:
    skill_file = tmp_path / "invalid" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(
        "---\nname: Not Valid\ndescription: " + ("x" * 1_025) + "\n---\nbody",
        encoding="utf-8",
    )

    result = load_skills([str(skill_file)], cwd=str(tmp_path))

    assert result["skills"] == []
    messages = [diagnostic.message for diagnostic in result["diagnostics"]]
    assert any("skill-name contract" in message for message in messages)
    assert any("1024" in message for message in messages)


def test_resource_discovery_merges_ignore_files_but_explicit_file_wins(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    for name in ("visible", "git-hidden", "ignore-hidden", "fd-hidden", "node_modules"):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\nbody",
            encoding="utf-8",
        )
    (root / ".gitignore").write_text("git-hidden/\n", encoding="utf-8")
    (root / ".ignore").write_text("ignore-hidden/\n", encoding="utf-8")
    (root / ".fdignore").write_text("fd-hidden/\n", encoding="utf-8")

    discovered = load_skills([str(root)], cwd=str(tmp_path))
    explicit = load_skills([str(root / "git-hidden" / "SKILL.md")], cwd=str(tmp_path))

    assert [skill.name for skill in discovered["skills"]] == ["visible"]
    assert [skill.name for skill in explicit["skills"]] == ["git-hidden"]


def test_prompt_template_uses_yaml_and_reports_malformed_frontmatter(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "review.md").write_text(
        "---\ndescription: 'Review selected files'\nargument-hint: '[path ...]'\n---\nReview $ARGUMENTS",
        encoding="utf-8",
    )
    (prompts / "broken.md").write_text(
        "---\ndescription: [broken\n---\nbody",
        encoding="utf-8",
    )

    result = load_prompt_templates([str(prompts)], cwd=str(tmp_path))

    assert [(prompt.name, prompt.argument_hint) for prompt in result["prompts"]] == [
        ("review", "[path ...]"),
    ]
    assert len(result["diagnostics"]) == 1


def test_prompt_expansion_supports_shell_quoting_and_positional_arguments(tmp_path: Path) -> None:
    template = PromptTemplate(
        name="review",
        description="Review files",
        content="Review: $ARGUMENTS\nFirst: $1\nSecond: $2",
        source_info=create_synthetic_source_info(
            str(tmp_path / "review.md"),
            source="local",
        ),
        file_path=str(tmp_path / "review.md"),
    )

    assert expand_prompt_template('/review "src/app one.py" tests', [template]) == (
        "Review: src/app one.py tests\nFirst: src/app one.py\nSecond: tests"
    )
    assert expand_prompt_template("prefix /review src/app.py", [template]) == (
        "prefix /review src/app.py"
    )
    assert expand_prompt_template("/missing literal", [template]) == "/missing literal"


def test_theme_registry_preserves_or_falls_back_across_reload(tmp_path: Path) -> None:
    first = Theme(
        name="night",
        colors={"accent": "blue"},
        vars={},
        source_path=str(tmp_path / "night.json"),
        source_info=create_synthetic_source_info(
            str(tmp_path / "night.json"),
            source="local",
        ),
    )
    second = Theme(
        name="day",
        colors={"accent": "yellow"},
        vars={},
        source_path=str(tmp_path / "day.json"),
        source_info=create_synthetic_source_info(
            str(tmp_path / "day.json"),
            source="local",
        ),
    )
    registry = ThemeRegistry()
    registry.register_many([first, second])
    registry.select("night")

    assert registry.reload([first]) is None
    diagnostic = registry.reload([second])

    assert registry.active_name == "day"
    assert diagnostic is not None
    assert "night" in diagnostic


def test_prompt_template_expands_before_provider_and_refreshes_after_reload(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    prompts = project / "prompts"
    prompts.mkdir(parents=True)
    agent_dir.mkdir()
    prompt_file = prompts / "review.md"
    prompt_file.write_text(
        "---\ndescription: Review files\n---\nReview v1: $1 / $ARGUMENTS",
        encoding="utf-8",
    )
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=True,
        additional_prompt_template_paths=[str(prompts)],
    )
    loader.reload()
    submitted: list[str] = []

    def provider(model, context):
        submitted.append(
            _user_text(next(message for message in reversed(context.messages) if isinstance(message, UserMessage)))
        )
        return text_response_events(model, "ok")

    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(project), model=faux_model(), resource_loader=loader)
    try:
        session.prompt('/review "src/app one.py" tests')
        prompt_file.write_text(
            "---\ndescription: Review files\n---\nReview v2: $2 / $ARGUMENTS",
            encoding="utf-8",
        )
        session.reload()
        session.prompt('/review "src/app one.py" tests')
    finally:
        session.shutdown()

    assert submitted == [
        "Review v1: src/app one.py / src/app one.py tests",
        "Review v2: tests / src/app one.py tests",
    ]


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (
            True,
            '<skill name="lint" location="{skill_file}">\n'
            "References are relative to {skill_dir}.\n\n"
            "Inspect lint failures carefully.\n</skill>\n\n"
            'check "src/app.py"',
        ),
        (False, '/skill:lint check "src/app.py"'),
    ],
)
def test_skill_command_injects_selected_skill_only_when_enabled(
    tmp_path: Path,
    enabled: bool,
    expected: str,
) -> None:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    skill_dir = project / "skills" / "lint"
    skill_dir.mkdir(parents=True)
    agent_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: lint\ndescription: Inspect lint failures\n---\n"
        "Inspect lint failures carefully.\n",
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory({"enableSkillCommands": enabled})
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=True,
        settings_manager=settings,
        additional_skill_paths=[str(skill_dir)],
    )
    loader.reload()
    submitted: list[str] = []

    def provider(model, context):
        submitted.append(
            _user_text(next(message for message in reversed(context.messages) if isinstance(message, UserMessage)))
        )
        return text_response_events(model, "ok")

    register_api_provider(create_faux_provider(provider))
    session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=loader,
        settings_manager=settings,
    )
    try:
        session.prompt('/skill:lint check "src/app.py"')
    finally:
        session.shutdown()

    assert submitted == [
        expected.format(skill_file=skill_file, skill_dir=skill_dir)
    ]
