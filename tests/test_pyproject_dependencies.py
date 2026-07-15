from __future__ import annotations

import tomllib
import json
from pathlib import Path

from packaging.requirements import Requirement


def test_direct_runtime_dependencies_match_imported_owners() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    runtime = {Requirement(item).name for item in metadata["project"]["dependencies"]}
    browser = {
        Requirement(item).name
        for item in metadata["project"]["optional-dependencies"]["browser"]
    }

    assert runtime == {
        "boto3",
        "google-auth",
        "httpx",
        "jsonschema",
        "psutil",
        "PyYAML",
        "websockets",
        "zstandard",
    }
    assert browser == {"playwright"}


def test_base_install_keeps_playwright_optional() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

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


def test_package_metadata_has_one_python_authority() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    package_json = json.loads(Path("package.json").read_text())

    assert pyproject["project"]["name"] == "travis234"
    assert pyproject["project"]["version"] == "2.3.1"
    assert pyproject["project"]["scripts"] == {"travis234": "travis.cli:main"}
    assert "travisConfig" not in package_json
