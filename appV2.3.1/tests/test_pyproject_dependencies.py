from __future__ import annotations

import tomllib
from pathlib import Path


def test_base_install_keeps_playwright_optional() -> None:
    pyproject = tomllib.loads(Path("appV2.3.1/pyproject.toml").read_text())

    dependencies = pyproject["project"]["dependencies"]
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    assert not any(dependency.startswith("playwright") for dependency in dependencies)
    assert "browser" in optional_dependencies
    assert any(
        dependency.startswith("playwright")
        for dependency in optional_dependencies["browser"]
    )
