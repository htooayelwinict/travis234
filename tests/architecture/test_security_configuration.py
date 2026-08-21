"""Reproducible source and dependency security-gate contracts."""

from __future__ import annotations

from pathlib import Path
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE_CI = ROOT / ".github/workflows/ci.yml"
SCHEDULED_AUDIT = ROOT / ".github/workflows/security-audit.yml"


def _workflow(path: Path) -> tuple[dict, str]:
    source = path.read_text(encoding="utf-8")
    return yaml.safe_load(source), source


def _commands(workflow: dict) -> list[str]:
    return [str(step["run"]) for job in workflow["jobs"].values() for step in job.get("steps", []) if "run" in step]


def test_security_tools_are_bounded_and_owned_by_the_root_lock() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    with (ROOT / "uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)

    assert "bandit>=1.8,<2" in project["dependency-groups"]["dev"]
    assert "pip-audit>=2.9,<3" in project["dependency-groups"]["dev"]
    locked_names = {package["name"] for package in lock["package"]}
    assert {"bandit", "pip-audit"} <= locked_names


def test_source_ci_runs_strict_security_gates_without_global_suppression() -> None:
    workflow, source = _workflow(SOURCE_CI)
    joined = "\n".join(_commands(workflow))

    assert "bandit -r travis -lll -f json" in joined
    assert "uv export --locked --all-extras --dev --no-emit-project" in joined
    assert "pip-audit --strict --requirement" in joined
    assert "continue-on-error" not in source
    assert "bandit -r travis -lll -f json ||" not in joined
    assert "pip-audit --strict ||" not in joined
    assert "--skip" not in joined
    assert "printenv" not in joined
    assert "env |" not in joined


def test_scheduled_dependency_audit_is_locked_read_only_and_fail_visible() -> None:
    workflow, source = _workflow(SCHEDULED_AUDIT)
    triggers = workflow.get("on", workflow.get(True))
    joined = "\n".join(_commands(workflow))

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "uv sync --locked --all-extras --dev" in joined
    assert "uv export --locked --all-extras --dev --no-emit-project" in joined
    assert "pip-audit --strict --requirement" in joined
    assert "continue-on-error" not in source
    assert "|| true" not in source
    assert "printenv" not in source
    assert "env |" not in source
