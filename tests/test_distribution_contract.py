from __future__ import annotations

import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _locked_project_version(path: Path, name: str) -> str:
    lock = tomllib.loads(path.read_text(encoding="utf-8"))
    project = next(item for item in lock["package"] if item["name"] == name)
    return project["version"]


def test_python_distribution_names_only_travis234() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    assert project["name"] == "travis234"
    assert project["scripts"] == {"travis234": "travis.cli:main"}
    assert metadata["tool"]["setuptools"]["package-data"]["travis"] == [
        "resources/**/*.md",
        "resources/skills/**/*.py",
        "resources/roles/*.json",
    ]


def test_npm_distribution_names_only_travis234() -> None:
    import json

    package = json.loads((ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8"))
    assert package["name"] == "@htooayelwinict/travis234"
    assert package["bin"] == {"travis234": "bin/travis234.js"}
    assert "roles/**/*.json" in package["files"]


def test_release_versions_are_aligned() -> None:
    import json

    expected = "2.5.0"
    python_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    adapter_metadata = tomllib.loads(
        (ROOT / "packages/travis234-mcp-adapter/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    workspace = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    npm_package = json.loads(
        (ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8")
    )
    config_source = (ROOT / "travis/coding_agent/config.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert python_metadata["project"]["version"] == expected
    assert adapter_metadata["project"]["version"] == "0.2.0"
    assert workspace["version"] == expected
    assert npm_package["version"] == expected
    assert f'VERSION = "{expected}"' in config_source
    assert f"Version {expected}" in readme
    assert f"version-{expected}-" in readme


def test_readme_explains_durable_orchestration_without_private_grammar() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Every PyPI wheel includes four read-only fallback skills:" in readme
    assert "`coordination` for optional plain-language planning" in readme
    section = readme.split("### Durable multi-Travis orchestration", 1)[1].split(
        "## Managed processes", 1
    )[0]
    normalized = " ".join(section.split())
    for value in (
        "ordinary language",
        "Supervised",
        "Full handoff",
        "tmux",
        "RPC",
        "Git",
        "SQLite",
        "n8n",
        "generic MCP adapter",
        "subagent",
        "No result automatically",
        "explicitly requests a safe cleanup",
    ):
        assert value in normalized
    assert "_relay" not in normalized
    assert "TRAVIS234_ORCHESTRATION_CAPABILITY" not in normalized
    assert "global system prompt" in normalized


def test_release_locks_match_project_metadata() -> None:
    assert _locked_project_version(
        ROOT / "packages/travis234-mcp-adapter/uv.lock",
        "travis234-mcp-adapter",
    ) == "0.2.0"


def test_retired_ghost_addon_has_no_active_product_surface() -> None:
    retired_package = "travis234-ghost-mcp"
    assert not (ROOT / "packages" / retired_package).exists()
    assert not (ROOT / "evals/bundled_ghost_mcp_smoke.py").exists()

    active_docs = (
        ROOT / "README.md",
        ROOT / "packages/travis234-mcp-adapter/README.md",
    )
    forbidden = (
        "travis234 install travis234-ghost-mcp",
        "ghost-os",
        "/ghost-setup",
        "/ghost-doctor",
    )
    for path in active_docs:
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden), path


def test_root_wheel_distribution_excludes_optional_mcp_packages(tmp_path: Path) -> None:
    output = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--sdist", "--clear", "-o", str(output), str(ROOT)],
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
    assert {
        "travis/resources/roles/coordination-planner.json",
        "travis/resources/skills/coordination/SKILL.md",
        "travis/resources/skills/coordination/references/planning-contract.md",
        "travis/resources/skills/orchestration/SKILL.md",
        "travis/resources/skills/orchestration/references/protocol.md",
        "travis/resources/skills/orchestration/scripts/orchestrate.py",
    } <= set(names)

    sdist = next(output.glob("*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()
    assert any(
        name.endswith("/travis/resources/roles/coordination-planner.json")
        for name in sdist_names
    )


def test_packaged_builtin_skills_match_npm_distribution() -> None:
    python_skills = ROOT / "travis" / "resources" / "skills"
    npm_skills = ROOT / "packages" / "travis234-cli" / "skills"

    python_files = {
        path.relative_to(python_skills).as_posix(): path.read_bytes()
        for path in python_skills.rglob("*")
        if path.is_file()
    }
    npm_files = {
        path.relative_to(npm_skills).as_posix(): path.read_bytes()
        for path in npm_skills.rglob("*")
        if path.is_file()
    }
    assert npm_files == python_files
    assert {
        "coordination/SKILL.md",
        "coordination/references/planning-contract.md",
        "orchestration/SKILL.md",
        "orchestration/references/protocol.md",
        "orchestration/scripts/orchestrate.py",
    } <= python_files.keys()


def test_packaged_builtin_roles_match_npm_distribution() -> None:
    python_roles = ROOT / "travis" / "resources" / "roles"
    npm_roles = ROOT / "packages" / "travis234-cli" / "roles"

    python_files = {
        path.relative_to(python_roles).as_posix(): path.read_bytes()
        for path in python_roles.rglob("*.json")
    }
    npm_files = {
        path.relative_to(npm_roles).as_posix(): path.read_bytes()
        for path in npm_roles.rglob("*.json")
    }

    assert npm_files == python_files
    assert "coordination-planner.json" in python_files


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
