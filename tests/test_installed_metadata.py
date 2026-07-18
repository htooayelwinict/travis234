from __future__ import annotations

import importlib
from importlib import metadata
from pathlib import Path


def test_config_uses_python_distribution_metadata(monkeypatch) -> None:
    import travis.coding_agent.config as config

    with monkeypatch.context() as patch:
        patch.setattr(metadata, "version", lambda distribution: "9.8.7")
        reloaded = importlib.reload(config)

        assert reloaded.PACKAGE_NAME == "travis234"
        assert reloaded.APP_NAME == "travis234"
        assert reloaded.APP_TITLE == "Travis234"
        assert reloaded.VERSION == "9.8.7"

    importlib.reload(config)


def test_packaged_context_resources_exist() -> None:
    from travis.coding_agent.config import get_packaged_context_paths

    resources = tuple(Path(path) for path in get_packaged_context_paths())

    assert {path.name for path in resources} == {"README.md", "docs", "examples"}
    assert all(path.exists() for path in resources)


def test_packaged_builtin_skills_exist() -> None:
    from travis.coding_agent.config import get_packaged_skills_path

    skills_root = Path(get_packaged_skills_path())

    assert skills_root.is_dir()
    assert {path.parent.name for path in skills_root.glob("*/SKILL.md")} == {
        "subagent-delegation",
        "web-search",
    }


def test_session_directory_has_an_independent_hard_cutover_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from travis.coding_agent.config import get_sessions_dir

    session_dir = tmp_path / "sessions"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_SESSION_DIR", str(session_dir))

    assert get_sessions_dir() == str(session_dir)
