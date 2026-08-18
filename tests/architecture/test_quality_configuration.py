from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYRIGHT_CONFIG = ROOT / "pyrightconfig.json"
RUFF_CONFIG = ROOT / "ruff.toml"
MIGRATED_OWNERS = (
    "travis/runtime_facade.py",
    "tests/test_runtime_facade_contract.py",
    "tests/architecture/test_quality_configuration.py",
)
FATAL_RUFF_RULES = {"E9", "F63", "F7", "F82"}
NORMAL_RUFF_RULES = "E4,E7,E9,F,I,UP,B,SIM"
EXACT_F821_DEFERRALS = {
    "travis/coding_agent/subagent_trace.py",
    "travis/tui/footer_data.py",
    "travis/tui/interactive_extensions.py",
}


def _load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def test_root_lock_is_tracked_and_not_ignored() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "uv.lock"],
        cwd=ROOT,
        check=False,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "uv.lock"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert ignored.returncode != 0
    assert tracked.returncode == 0
    assert (ROOT / "uv.lock").is_file()


def test_development_group_locks_required_quality_tools() -> None:
    project = _load_toml(ROOT / "pyproject.toml")
    dependency_groups = project.get("dependency-groups")

    assert isinstance(dependency_groups, dict)
    dev = dependency_groups.get("dev")
    assert isinstance(dev, list)
    package_names = {
        str(requirement).split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].lower()
        for requirement in dev
    }
    assert {"build", "coverage", "pyright", "pytest", "ruff", "twine"} <= package_names


def test_pyright_scope_is_explicit_monotonic_and_not_broadly_suppressed() -> None:
    assert PYRIGHT_CONFIG.is_file()
    config = json.loads(PYRIGHT_CONFIG.read_text(encoding="utf-8"))

    assert config["pythonVersion"] == "3.13"
    assert config["typeCheckingMode"] in {"standard", "strict"}
    included = config.get("include")
    assert isinstance(included, list)
    assert set(MIGRATED_OWNERS) <= set(included)

    excluded = config.get("exclude", [])
    assert isinstance(excluded, list)
    for included_path in included:
        assert not any(fnmatch.fnmatch(str(included_path), str(pattern)) for pattern in excluded)

    ignored = config.get("ignore", [])
    assert isinstance(ignored, list)
    assert not any(str(pattern).startswith("travis") for pattern in ignored)
    assert not any(
        key.startswith("report") and str(value).lower() in {"none", "false"}
        for key, value in config.items()
    )


def test_ruff_runs_fatal_rules_repository_wide_without_blanket_ignores() -> None:
    assert RUFF_CONFIG.is_file()
    config = _load_toml(RUFF_CONFIG)
    lint = config.get("lint")

    assert isinstance(lint, dict)
    selected = lint.get("select")
    assert isinstance(selected, list)
    assert set(selected) >= FATAL_RUFF_RULES
    assert not (FATAL_RUFF_RULES & set(lint.get("ignore", [])))

    per_file_ignores = lint.get("per-file-ignores", {})
    assert isinstance(per_file_ignores, dict)
    assert not any(
        str(pattern).startswith("travis/") and "*" in str(pattern)
        for pattern in per_file_ignores
    )
    assert set(per_file_ignores) == EXACT_F821_DEFERRALS
    assert all(set(rule_codes) == {"F821"} for rule_codes in per_file_ignores.values())


def test_migrated_owners_pass_normal_ruff_rules() -> None:
    assert RUFF_CONFIG.is_file()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(RUFF_CONFIG),
            "--select",
            NORMAL_RUFF_RULES,
            *MIGRATED_OWNERS,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
