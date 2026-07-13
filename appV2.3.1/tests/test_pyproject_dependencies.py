from __future__ import annotations

import tomllib
import json
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
    assert "jsonschema>=4.23,<5" in dependencies


def test_root_runtime_uses_same_jsonschema_bounds() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert "jsonschema>=4.23,<5" in pyproject["project"]["dependencies"]


def test_package_metadata_sets_appv231_config_defaults() -> None:
    pyproject = tomllib.loads(Path("appV2.3.1/pyproject.toml").read_text())
    package_json = json.loads(Path("appV2.3.1/package.json").read_text())

    assert pyproject["project"]["version"] == "2.3.1"
    assert package_json["version"] == "2.3.1"
    assert package_json["appv231Config"] == {
        "name": "appv231",
        "configDir": ".appv231",
    }
