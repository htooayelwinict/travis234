from __future__ import annotations

import subprocess
import tomllib
import zipfile
from email.parser import Parser
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

    expected = "2.4.5"
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


def test_release_versions_include_bundled_ghost_addon() -> None:
    adapter = tomllib.loads(
        (ROOT / "packages/travis234-mcp-adapter/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    ghost = tomllib.loads(
        (ROOT / "packages/travis234-ghost-mcp/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert adapter["project"]["version"] == "0.1.2"
    assert ghost["project"]["version"] == "0.1.0"


def test_root_distribution_excludes_optional_mcp_packages(tmp_path: Path) -> None:
    output = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--clear", "-o", str(output), str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheel = next(output.glob("*.whl"))
    assert wheel.name.endswith("-py3-none-any.whl")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))

    requirements = metadata.get_all("Requires-Dist", [])
    assert not any("travis234-mcp-adapter" in item for item in requirements)
    assert not any("travis234-ghost-mcp" in item for item in requirements)
    assert not any("ghost_mcp" in name or "ghost-os" in name for name in names)


def test_bundled_ghost_documentation_matches_runtime_contract() -> None:
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    adapter_readme = (
        ROOT / "packages/travis234-mcp-adapter/README.md"
    ).read_text(encoding="utf-8")
    ghost_readme = (
        ROOT / "packages/travis234-ghost-mcp/README.md"
    ).read_text(encoding="utf-8")

    for required in (
        "travis234 install travis234-ghost-mcp",
        "travis234 --mcp",
        "/ghost-doctor",
        "/ghost-setup",
        "/ghost-setup vision",
        "~/.travis234/ghost-mcp",
        "macOS 14",
        "Apple Silicon",
    ):
        assert required in root_readme
        assert required in ghost_readme
    assert "trusted extension API" in adapter_readme
    assert "not a user configuration format" in adapter_readme


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
