from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from travis.coding_agent.capabilities import CapabilityKind
from travis.coding_agent.prompt_templates import (
    PromptTemplate,
    expand_prompt_template,
    load_prompt_templates,
)
from travis.coding_agent.package_manager import ResolvedPaths
from travis.coding_agent.resource_candidates import (
    ResourceContentRequest,
    build_resource_content,
    extend_resource_content,
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


def make_resource_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    package = tmp_path / "package"
    project.mkdir()
    agent_dir.mkdir()
    package.mkdir()
    return project, agent_dir, package


def write_skill(path: Path, name: str, description: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def write_package_manifest(package: Path, *, skills: list[str]) -> None:
    (package / "package.json").write_text(
        json.dumps({"name": "fixture", "travis": {"skills": skills}}),
        encoding="utf-8",
    )


def write_prompt(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ndescription: {version}\n---\nPrompt {version}\n",
        encoding="utf-8",
    )
    return path


def content_request(
    tmp_path: Path,
    *,
    prompt_paths: tuple[str, ...] = (),
) -> ResourceContentRequest:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(exist_ok=True)
    return ResourceContentRequest(
        cwd=str(tmp_path),
        agent_dir=str(agent_dir),
        project_trusted=False,
        resolved_paths=ResolvedPaths(),
        additional_skill_paths=(),
        additional_prompt_paths=prompt_paths,
        additional_theme_paths=(),
        no_context_files=True,
        no_skills=True,
        no_prompt_templates=False,
        no_themes=False,
        system_prompt_source=None,
        append_system_prompt_source=None,
        agents_files_override=None,
        skills_override=None,
        prompts_override=None,
        themes_override=None,
        system_prompt_override=None,
        append_system_prompt_override=None,
    )


def write_extension_resources(tmp_path: Path) -> tuple[Path, Path, Path]:
    skill = write_skill(
        tmp_path / "skills/extension-skill/SKILL.md",
        "extension-skill",
        "skill",
    )
    prompt = tmp_path / "prompts/extension-prompt.md"
    prompt.parent.mkdir()
    prompt.write_text(
        "---\ndescription: prompt\n---\nPrompt\n", encoding="utf-8"
    )
    theme = tmp_path / "themes/extension-theme.json"
    theme.parent.mkdir()
    theme.write_text(
        json.dumps({"name": "extension-theme", "colors": {}, "vars": {}}),
        encoding="utf-8",
    )
    return skill, prompt, theme


def configured_loader_with_every_kind(
    tmp_path: Path,
) -> tuple[DefaultResourceLoader, dict[str, str]]:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    agent_dir.mkdir()
    context = project / "AGENTS.md"
    context.write_text("repository context\n", encoding="utf-8")
    skill = write_skill(
        project / "skills/audit/SKILL.md", "audit", "audit code"
    )
    prompt = project / "prompts/review.md"
    prompt.parent.mkdir()
    prompt.write_text(
        "---\ndescription: review\n---\nReview\n", encoding="utf-8"
    )
    theme = project / "themes/night.json"
    theme.parent.mkdir()
    theme.write_text(
        json.dumps({"name": "night", "colors": {}, "vars": {}}),
        encoding="utf-8",
    )
    extension = project / "extensions/sample.py"
    extension.parent.mkdir()
    extension.write_text(
        "def extension(travis):\n    return None\n", encoding="utf-8"
    )
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=True,
        additional_skill_paths=[str(skill)],
        additional_prompt_template_paths=[str(prompt)],
        additional_theme_paths=[str(theme)],
        additional_extension_paths=[str(extension)],
    )
    return loader, {
        "skill": str(skill.resolve()),
        "prompt": str(prompt.resolve()),
        "theme": str(theme.resolve()),
        "context": str(context.resolve()),
        "extension": str(extension.resolve()),
    }


def test_loader_explains_every_phase_one_resource_source(tmp_path: Path) -> None:
    loader, paths = configured_loader_with_every_kind(tmp_path)

    loader.reload()
    snapshot = loader.get_capability_snapshot()
    cases = (
        (CapabilityKind.SKILL, "audit", paths["skill"]),
        (CapabilityKind.PROMPT_TEMPLATE, "review", paths["prompt"]),
        (CapabilityKind.THEME, "night", paths["theme"]),
        (CapabilityKind.CONTEXT_FILE, paths["context"], paths["context"]),
        (CapabilityKind.EXTENSION, paths["extension"], paths["extension"]),
    )

    for kind, key, expected_path in cases:
        resolution = snapshot.resolve(kind, key)
        assert resolution.winner is not None
        assert resolution.winner.source.path == expected_path
        assert resolution.winner.source.provider == "default-resources"


def test_candidate_preserves_package_before_bundled_skill_precedence(
    tmp_path: Path,
) -> None:
    project, agent_dir, package = make_resource_roots(tmp_path)
    package_skill = write_skill(
        package / "skills/web-search/SKILL.md",
        "web-search",
        "package winner",
    )
    write_package_manifest(package, skills=["skills/web-search/SKILL.md"])
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=False,
        package_paths=[str(package)],
    )

    loader.reload()

    skill = next(
        item for item in loader.get_skills()["skills"] if item.name == "web-search"
    )
    assert skill.description == "package winner"
    assert skill.source_info.origin == "package"
    collisions = [
        item
        for item in loader.get_skills()["diagnostics"]
        if item.type == "collision"
    ]
    assert any(
        item.collision
        and item.collision["winnerPath"] == str(package_skill)
        for item in collisions
    )


def test_content_build_does_not_mutate_previous_candidate(tmp_path: Path) -> None:
    prompt = write_prompt(tmp_path / "prompts/review.md", "v1")
    request = content_request(tmp_path, prompt_paths=(str(prompt.parent),))
    first = build_resource_content(request)
    write_prompt(prompt, "v2")

    second = build_resource_content(request)

    assert first.prompts_result["prompts"][0].content == "Prompt v1"
    assert second.prompts_result["prompts"][0].content == "Prompt v2"


def test_content_build_preserves_discovery_and_override_order(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "AGENTS.md").write_text("global context\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("project context\n", encoding="utf-8")
    project_config = tmp_path / ".travis234"
    project_config.mkdir()
    (project_config / "SYSTEM.md").write_text("project system\n", encoding="utf-8")
    (project_config / "APPEND_SYSTEM.md").write_text("project append\n", encoding="utf-8")
    override_inputs: list[object] = []

    def override_agents(value: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
        override_inputs.append(tuple(item["content"] for item in value["agentsFiles"]))
        return {
            "agentsFiles": [
                *value["agentsFiles"],
                {"path": "synthetic", "content": "extension context\n"},
            ]
        }

    def override_system(value: str | None) -> str | None:
        override_inputs.append(value)
        return f"{value}extension system\n"

    def override_append(value: list[str]) -> list[str]:
        override_inputs.append(tuple(value))
        return [*value, "extension append\n"]

    request = replace(
        content_request(tmp_path),
        project_trusted=True,
        no_context_files=False,
        no_agent_roles=True,
        agents_files_override=override_agents,
        system_prompt_override=override_system,
        append_system_prompt_override=override_append,
    )

    candidate = build_resource_content(request)

    assert override_inputs == [
        ("global context\n", "project context\n"),
        "project system\n",
        ("project append\n",),
    ]
    assert tuple(item["content"] for item in candidate.agents_files) == (
        "global context\n",
        "project context\n",
        "extension context\n",
    )
    assert candidate.system_prompt == "project system\nextension system\n"
    assert candidate.append_system_prompt == (
        "project append\n",
        "extension append\n",
    )


def test_extend_builds_skill_prompt_and_theme_as_one_candidate(
    tmp_path: Path,
) -> None:
    initial = build_resource_content(content_request(tmp_path))
    skill, prompt, theme = write_extension_resources(tmp_path)

    extended = extend_resource_content(
        initial,
        cwd=str(tmp_path),
        skill_paths=(str(skill),),
        prompt_paths=(str(prompt),),
        theme_paths=(str(theme),),
        skills_override=None,
        prompts_override=None,
        themes_override=None,
    )

    assert [item.name for item in extended.skills_result["skills"]] == [
        "extension-skill"
    ]
    assert [item.name for item in extended.prompts_result["prompts"]] == [
        "extension-prompt"
    ]
    assert [item.name for item in extended.themes_result["themes"]] == [
        "extension-theme"
    ]
    assert initial.skills_result["skills"] == []


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


def _write_coordination_skill(
    root: Path,
    *,
    disable_model_invocation: bool = False,
) -> Path:
    skill_dir = root / "skills" / "coordination"
    skill_dir.mkdir(parents=True)
    disabled = "disable-model-invocation: true\n" if disable_model_invocation else ""
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: coordination\n"
        "description: Use when the user explicitly selects the coordination skill.\n"
        f"{disabled}"
        "---\n"
        "# Coordination fixture\n\nCOORDINATION_BODY_SENTINEL\n",
        encoding="utf-8",
    )
    return skill_dir


def test_coordination_commands_inject_one_skill_and_runtime_parsed_request(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    skill_dir = _write_coordination_skill(project)
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=True,
        additional_skill_paths=[str(skill_dir)],
    )
    loader.reload()
    submitted: list[str] = []

    def provider(model, context):
        submitted.append(
            _user_text(
                next(
                    message
                    for message in reversed(context.messages)
                    if isinstance(message, UserMessage)
                )
            )
        )
        return text_response_events(model, "ok")

    register_api_provider(create_faux_provider(provider))
    session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=loader,
    )
    try:
        session.prompt('/coordination --deep inspect "src/app.py"')
        session.prompt("/skill:coordination --plan inspect tests")
        session.prompt("/coordination -- --deep literal λ")

        calls_before_invalid = len(submitted)
        for prompt in (
            "/coordination",
            "/coordination --deep",
            "/coordination --unknown goal",
        ):
            with pytest.raises(ValueError, match="coordination"):
                session.prompt(prompt)
        assert len(submitted) == calls_before_invalid
    finally:
        session.shutdown()

    assert len(submitted) == 3
    for text in submitted:
        assert text.count('<skill name="coordination"') == 1
        assert text.count("COORDINATION_BODY_SENTINEL") == 1
    assert '{"mode":"deep","goal":"inspect \\"src/app.py\\""}' in submitted[0]
    assert '{"mode":"plan","goal":"inspect tests"}' in submitted[1]
    assert '{"mode":"auto","goal":"--deep literal λ"}' in submitted[2]


def test_coordination_alias_obeys_skill_command_and_model_invocation_controls(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    disabled_commands = SettingsManager.in_memory({"enableSkillCommands": False})
    enabled_skill = _write_coordination_skill(project / "enabled")
    disabled_skill = _write_coordination_skill(
        project / "disabled",
        disable_model_invocation=True,
    )

    command_disabled_loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent-command-disabled"),
        project_trusted=True,
        settings_manager=disabled_commands,
        additional_skill_paths=[str(enabled_skill)],
    )
    command_disabled_loader.reload()
    command_disabled_session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=command_disabled_loader,
        settings_manager=disabled_commands,
    )
    model_disabled_loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent-model-disabled"),
        project_trusted=True,
        additional_skill_paths=[str(disabled_skill)],
    )
    model_disabled_loader.reload()
    model_disabled_session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=model_disabled_loader,
    )
    no_skills_loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent-no-skills"),
        project_trusted=False,
        no_skills=True,
    )
    no_skills_loader.reload()
    no_skills_session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=no_skills_loader,
    )
    try:
        for session in (
            command_disabled_session,
            model_disabled_session,
            no_skills_session,
        ):
            assert session.extension_runner.get_registered_command("coordination") is None
            assert session.extension_runner.get_registered_command("skill:coordination") is None
    finally:
        command_disabled_session.shutdown()
        model_disabled_session.shutdown()
        no_skills_session.shutdown()


def test_coordination_alias_never_shadows_extension_command(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    skill_dir = _write_coordination_skill(project)

    def extension(runner):
        runner.register_command(
            "coordination",
            {"description": "Extension coordination", "handler": lambda *_args: []},
        )

    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
        additional_skill_paths=[str(skill_dir)],
        extension_factories=[extension],
    )
    loader.reload()
    session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=loader,
    )
    try:
        alias = session.extension_runner.get_registered_command("coordination")
        canonical = session.extension_runner.get_registered_command("skill:coordination")
        assert alias is not None
        assert alias.source_info.source == "extension"
        assert canonical is not None
        assert canonical.source_info.source == "skill"
        assert session.extension_runner.get_registered_command("coordination:1") is None
    finally:
        session.shutdown()


def test_coordination_skill_removal_reconciles_only_skill_owned_commands(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    skill_dir = _write_coordination_skill(project)
    skill_file = skill_dir / "SKILL.md"
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
        additional_skill_paths=[str(skill_dir)],
    )
    loader.reload()
    session = AgentSession(
        cwd=str(project),
        model=faux_model(),
        resource_loader=loader,
    )
    try:
        assert session.extension_runner.get_registered_command("coordination") is not None
        assert session.extension_runner.get_registered_command("skill:coordination") is not None

        skill_file.unlink()
        loader.no_skills = True
        session.reload()

        assert session.extension_runner.get_registered_command("coordination") is None
        assert session.extension_runner.get_registered_command("skill:coordination") is None
    finally:
        session.shutdown()
