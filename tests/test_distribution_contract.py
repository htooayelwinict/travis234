from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_python_distribution_names_only_travis234() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "travis234"
    assert project["scripts"] == {"travis234": "travis.cli:main"}


def test_npm_distribution_names_only_travis234() -> None:
    import json

    package = json.loads((ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8"))
    assert package["name"] == "@htooayelwinict/travis234"
    assert package["bin"] == {"travis234": "bin/travis234.js"}


def test_repository_has_one_sandbox_launcher_implementation() -> None:
    import json

    workspace = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert workspace["scripts"]["tui:sandbox"] == "node packages/travis234-cli/bin/travis234.js"
    assert not (ROOT / "travis/sandbox_launcher.py").exists()
    assert not (ROOT / "scripts/travis234_sandbox.py").exists()
