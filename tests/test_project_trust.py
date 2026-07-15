from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

import travis.coding_agent as coding_agent
from travis.coding_agent import project_trust
from travis.coding_agent.extensions import ExtensionRunner


def test_project_trust_module_is_available() -> None:
    assert importlib.util.find_spec("travis.coding_agent.project_trust") is not None


def test_project_trust_store_is_public_coding_agent_api() -> None:
    assert coding_agent.ProjectTrustStore is project_trust.ProjectTrustStore


def test_trust_store_uses_nearest_parent_and_child_override(tmp_path: Path) -> None:
    assert hasattr(project_trust, "ProjectTrustStore")
    ProjectTrustStore = project_trust.ProjectTrustStore
    agent_dir = tmp_path / "agent"
    parent = tmp_path / "work"
    child = parent / "repo"
    child.mkdir(parents=True)
    store = ProjectTrustStore(agent_dir)

    store.set(parent, True)
    assert store.get(child) is True

    store.set(child, False)
    assert store.get(child) is False

    store.set(child, None)
    assert store.get(child) is True


def test_malformed_trust_store_fails_closed(tmp_path: Path) -> None:
    assert hasattr(project_trust, "ProjectTrustError")
    assert hasattr(project_trust, "ProjectTrustStore")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "trust.json").write_text('["invalid"]', encoding="utf-8")
    store = project_trust.ProjectTrustStore(agent_dir)

    with pytest.raises(project_trust.ProjectTrustError, match="expected an object"):
        store.get(tmp_path / "repo")


@pytest.mark.parametrize("decision", [1, "yes", [], {}])
def test_invalid_trust_decision_fails_closed(tmp_path: Path, decision: object) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    project = tmp_path / "repo"
    (agent_dir / "trust.json").write_text(
        json.dumps({str(project.resolve()): decision}),
        encoding="utf-8",
    )
    store = project_trust.ProjectTrustStore(agent_dir)

    with pytest.raises(project_trust.ProjectTrustError, match="true, false, or null"):
        store.get(project)


def test_invalid_trust_json_fails_closed_with_store_path(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    trust_path = agent_dir / "trust.json"
    trust_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(project_trust.ProjectTrustError, match=str(trust_path)):
        project_trust.ProjectTrustStore(agent_dir).get(tmp_path / "repo")


@pytest.mark.parametrize(
    "relative",
    [
        ".travis234/settings.json",
        ".travis234/extensions/example.py",
        ".travis234/skills/demo/SKILL.md",
        ".travis234/prompts/review.md",
        ".travis234/themes/night.json",
        ".travis234/SYSTEM.md",
        ".travis234/APPEND_SYSTEM.md",
        ".agents/skills/demo/SKILL.md",
    ],
)
def test_project_resource_requires_trust(tmp_path: Path, relative: str) -> None:
    assert hasattr(project_trust, "has_trust_requiring_project_resources")
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("resource", encoding="utf-8")

    assert project_trust.has_trust_requiring_project_resources(tmp_path) is True


def test_project_without_behavior_changing_resources_does_not_require_trust(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("context only", encoding="utf-8")
    assert project_trust.has_trust_requiring_project_resources(tmp_path) is False


def test_global_agents_skills_do_not_make_home_projects_untrusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = home / "repo"
    (home / ".agents" / "skills").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert project_trust.has_trust_requiring_project_resources(project) is False


def test_project_trust_options_include_parent_and_session_only_choices(tmp_path: Path) -> None:
    assert hasattr(project_trust, "get_project_trust_options")
    project = tmp_path / "work" / "repo"
    project.mkdir(parents=True)

    options = project_trust.get_project_trust_options(project, include_session_only=True)

    assert [option.label for option in options] == [
        "Trust",
        f"Trust parent folder ({project.parent.resolve()})",
        "Trust (this session only)",
        "Do not trust",
        "Do not trust (this session only)",
    ]
    assert options[1].updates == (
        project_trust.ProjectTrustUpdate(str(project.parent.resolve()), True),
        project_trust.ProjectTrustUpdate(str(project.resolve()), None),
    )
    assert options[2].updates == ()


def test_no_ui_unknown_project_fails_closed(tmp_path: Path) -> None:
    assert hasattr(project_trust, "ProjectTrustContext")
    assert hasattr(project_trust, "resolve_project_trust")
    project = tmp_path / "repo"
    extension = project / ".travis234" / "extensions" / "unsafe.py"
    extension.parent.mkdir(parents=True)
    extension.write_text("raise RuntimeError('executed')", encoding="utf-8")

    trusted = asyncio.run(
        project_trust.resolve_project_trust(
            cwd=project,
            trust_store=project_trust.ProjectTrustStore(tmp_path / "agent"),
            context=project_trust.ProjectTrustContext(has_ui=False, select=None),
            default_project_trust="ask",
        )
    )

    assert trusted is False


def test_process_override_precedes_saved_decision(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".travis234" / "extensions").mkdir(parents=True)
    store = project_trust.ProjectTrustStore(tmp_path / "agent")
    store.set(project, False)

    trusted = asyncio.run(
        project_trust.resolve_project_trust(
            cwd=project,
            trust_store=store,
            context=project_trust.ProjectTrustContext(has_ui=False, select=None),
            trust_override=True,
        )
    )

    assert trusted is True


def test_saved_parent_decision_precedes_default_policy(tmp_path: Path) -> None:
    parent = tmp_path / "work"
    project = parent / "repo"
    (project / ".travis234" / "skills").mkdir(parents=True)
    store = project_trust.ProjectTrustStore(tmp_path / "agent")
    store.set(parent, True)

    trusted = asyncio.run(
        project_trust.resolve_project_trust(
            cwd=project,
            trust_store=store,
            context=project_trust.ProjectTrustContext(has_ui=False, select=None),
            default_project_trust="never",
        )
    )

    assert trusted is True


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("always", True), ("never", False)],
)
def test_default_project_trust_policy_applies_before_ui(
    tmp_path: Path,
    policy: str,
    expected: bool,
) -> None:
    project = tmp_path / "repo"
    (project / ".travis234" / "themes").mkdir(parents=True)

    trusted = asyncio.run(
        project_trust.resolve_project_trust(
            cwd=project,
            trust_store=project_trust.ProjectTrustStore(tmp_path / "agent"),
            context=project_trust.ProjectTrustContext(has_ui=False, select=None),
            default_project_trust=policy,
        )
    )

    assert trusted is expected


def test_interactive_choice_is_persisted(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".travis234" / "SYSTEM.md").parent.mkdir(parents=True)
    (project / ".travis234" / "SYSTEM.md").write_text("system", encoding="utf-8")
    store = project_trust.ProjectTrustStore(tmp_path / "agent")
    prompts: list[tuple[str, list[str]]] = []

    def select(prompt: str, choices: list[str]) -> str:
        prompts.append((prompt, choices))
        return "Trust"

    trusted = asyncio.run(
        project_trust.resolve_project_trust(
            cwd=project,
            trust_store=store,
            context=project_trust.ProjectTrustContext(has_ui=True, select=select),
        )
    )

    assert trusted is True
    assert store.get(project) is True
    assert prompts and str(project.resolve()) in prompts[0][0]


def test_bootstrap_extension_decision_precedes_saved_store(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".travis234" / "prompts").mkdir(parents=True)
    store = project_trust.ProjectTrustStore(tmp_path / "agent")
    store.set(project, False)

    class BootstrapRunner:
        async def async_emit_project_trust(self, event, context):
            assert event == {"type": "project_trust", "cwd": str(project.resolve())}
            assert context.has_ui is False
            return {"trusted": "yes", "remember": True}

    trusted = asyncio.run(
        project_trust.resolve_project_trust(
            cwd=project,
            trust_store=store,
            context=project_trust.ProjectTrustContext(has_ui=False, select=None),
            extension_runner=BootstrapRunner(),
        )
    )

    assert trusted is True
    assert store.get(project) is True


def test_extension_runner_project_trust_uses_first_decision(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    calls: list[str] = []

    def undecided(event, context):
        calls.append("undecided")
        return {"trusted": "undecided"}

    async def trusted(event, context):
        calls.append("trusted")
        return {"trusted": "yes", "remember": False}

    def ignored(event, context):
        calls.append("ignored")
        return {"trusted": "no"}

    runner.on("project_trust", undecided)
    runner.on("project_trust", trusted)
    runner.on("project_trust", ignored)

    assert hasattr(runner, "async_emit_project_trust")
    result = asyncio.run(
        runner.async_emit_project_trust(
            {"type": "project_trust", "cwd": str(tmp_path)},
            project_trust.ProjectTrustContext(False, None),
        )
    )

    assert result == {"trusted": "yes", "remember": False}
    assert calls == ["undecided", "trusted"]
