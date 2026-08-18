from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> tuple[dict, str]:
    assert CI_PATH.is_file(), "source CI workflow is missing"
    source = CI_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(source), source


def _triggers(workflow: dict) -> dict:
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def _run_commands(workflow: dict) -> list[str]:
    return [
        str(step["run"])
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def test_source_ci_triggers_and_permissions_are_least_privilege() -> None:
    workflow, _source = _workflow()

    assert set(_triggers(workflow)) == {"pull_request", "push"}
    assert _triggers(workflow)["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert re.search(r"@v\d+$", str(step["uses"]))


def test_source_ci_runs_locked_quality_root_adapter_npm_and_build_gates() -> None:
    workflow, _source = _workflow()
    commands = _run_commands(workflow)
    joined = "\n".join(commands)

    assert "uv sync --locked --all-extras --dev" in joined
    assert "ruff check --select E9,F63,F7,F82 travis tests" in joined
    assert "uv run --locked --all-extras --dev pyright" in joined
    assert "pytest -q -p no:cacheprovider tests" in joined
    assert "--project packages/travis234-mcp-adapter" in joined
    assert "packages/travis234-mcp-adapter/tests" in joined
    assert "npm --prefix packages/travis234-cli test" in joined
    assert "npm --prefix packages/travis234-cli run pack:dry-run" in joined
    assert "uv build --out-dir" in joined
    assert "packages/travis234-mcp-adapter" in joined
    assert "twine check" in joined


def test_source_ci_records_then_strictly_verifies_temporary_evidence() -> None:
    workflow, source = _workflow()
    commands = _run_commands(workflow)
    joined = "\n".join(commands)

    record_index = joined.index("--record-automated-evidence")
    verify_index = joined.index("--require-current-commit")
    assert record_index < verify_index
    assert "acceptance-evidence.json" in joined
    assert "docs/verification/acceptance-evidence.json" not in source


def test_source_ci_runs_reproducible_statement_and_branch_coverage() -> None:
    workflow, source = _workflow()
    commands = _run_commands(workflow)
    joined = "\n".join(commands)
    coverage_run = next(command for command in commands if "coverage run -m pytest" in command)

    erase_index = joined.index("coverage erase")
    run_index = joined.index("coverage run -m pytest")
    combine_index = joined.index("coverage combine")
    json_index = joined.index("coverage json -o coverage.json")
    floor_index = joined.index("scripts/check_coverage_floor.py")
    evidence_index = joined.index("--record-automated-evidence")

    assert erase_index < run_index < combine_index < json_index < floor_index < evidence_index
    assert "PYTHONDONTWRITEBYTECODE: \"1\"" in source
    assert "-q -p no:cacheprovider tests" in joined
    assert '--with "./packages/travis234-mcp-adapter"' in coverage_run
    assert "coverage.json --statements 83.0 --branches 68.0" in joined


def test_source_ci_contains_no_publish_registry_or_container_mutation() -> None:
    _workflow_data, source = _workflow()
    lowered = source.casefold()

    assert "docker/login-action" not in lowered
    assert "docker build" not in lowered
    assert "docker push" not in lowered
    assert "twine upload" not in lowered
    assert "npm publish" not in lowered
    assert "ghcr.io" not in lowered
