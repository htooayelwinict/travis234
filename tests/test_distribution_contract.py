from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_python_distribution_names_only_travis234() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    assert project["name"] == "travis234"
    assert project["scripts"] == {"travis234": "travis.cli:main"}
    assert metadata["tool"]["setuptools"]["package-data"]["travis"] == ["resources/**/*.md"]


def test_npm_distribution_names_only_travis234() -> None:
    import json

    package = json.loads((ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8"))
    assert package["name"] == "@htooayelwinict/travis234"
    assert package["bin"] == {"travis234": "bin/travis234.js"}


def test_release_versions_are_aligned() -> None:
    import json

    expected = "2.3.4"
    python_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workspace = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    npm_package = json.loads(
        (ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8")
    )
    config_source = (ROOT / "travis/coding_agent/config.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert python_metadata["project"]["version"] == expected
    assert workspace["version"] == expected
    assert npm_package["version"] == expected
    assert f'VERSION = "{expected}"' in config_source
    assert f"Version {expected}" in readme
    assert f"version-{expected}-" in readme


def test_packaged_builtin_skills_match_npm_distribution() -> None:
    python_skills = ROOT / "travis" / "resources" / "skills"
    npm_skills = ROOT / "packages" / "travis234-cli" / "skills"

    for name in ("subagent-delegation", "web-search"):
        assert (python_skills / name / "SKILL.md").read_bytes() == (
            npm_skills / name / "SKILL.md"
        ).read_bytes()


def test_repository_has_one_sandbox_launcher_implementation() -> None:
    import json

    workspace = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert workspace["scripts"]["tui:sandbox"] == "node packages/travis234-cli/bin/travis234.js"
    assert not (ROOT / "travis/sandbox_launcher.py").exists()
    assert not (ROOT / "scripts/travis234_sandbox.py").exists()


def test_pytest_only_discovers_the_product_test_tree() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def test_release_build_context_excludes_reference_oracles_and_plans() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {"pi", "hermes-agent", "appv231", "docs/superpowers", "PI_HERMES_TRAVIS_CROSS_CHECK_REPORT.md"} <= ignored
