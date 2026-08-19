from __future__ import annotations

import fnmatch
import json
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYRIGHT_CONFIG = ROOT / "pyrightconfig.json"
RUFF_CONFIG = ROOT / "ruff.toml"
MIGRATED_OWNERS = (
    "travis/ai/__init__.py",
    "travis/controller_ports.py",
    "travis/runtime_facade.py",
    "travis/coding_agent/agent_harness.py",
    "travis/coding_agent/agent_session.py",
    "travis/coding_agent/agent_session_runtime.py",
    "travis/coding_agent/agent_session_services.py",
    "travis/coding_agent/session_bash.py",
    "travis/coding_agent/session_composition.py",
    "travis/coding_agent/session_controllers.py",
    "travis/coding_agent/session_contracts.py",
    "travis/coding_agent/session_events.py",
    "travis/coding_agent/session_extensions.py",
    "travis/coding_agent/session_generation_params.py",
    "travis/coding_agent/session_models.py",
    "travis/coding_agent/session_operations.py",
    "travis/coding_agent/session_options.py",
    "travis/coding_agent/session_persistence.py",
    "travis/coding_agent/session_policy_controller.py",
    "travis/coding_agent/session_ports.py",
    "travis/coding_agent/session_state.py",
    "travis/coding_agent/session_subagents.py",
    "travis/coding_agent/session_tooling.py",
    "travis/coding_agent/session_turns.py",
    "travis/coding_agent/subagent_trace.py",
    "travis/tui/component.py",
    "travis/tui/components/__init__.py",
    "travis/tui/footer_data.py",
    "travis/tui/interactive_command_dispatcher.py",
    "travis/tui/interactive_contracts.py",
    "travis/tui/interactive_controllers.py",
    "travis/tui/interactive_extensions.py",
    "travis/tui/interactive_lsp.py",
    "travis/tui/interactive_memory.py",
    "travis/tui/interactive_mode.py",
    "travis/tui/interactive_model_auth.py",
    "travis/tui/interactive_motion.py",
    "travis/tui/interactive_operations.py",
    "travis/tui/interactive_params.py",
    "travis/tui/interactive_process_commands.py",
    "travis/tui/interactive_services.py",
    "travis/tui/interactive_session_commands.py",
    "travis/tui/interactive_shutdown.py",
    "travis/tui/interactive_state.py",
    "travis/tui/interactive_subagents.py",
    "travis/tui/interactive_turn_controller.py",
    "travis/tui/interactive_view.py",
    "tests/architecture/test_public_type_hints.py",
    "tests/architecture/test_refactor_contracts.py",
    "tests/coding_agent/test_session_composition.py",
    "tests/coding_agent/test_session_options.py",
    "tests/test_runtime_facade_contract.py",
    "tests/architecture/test_quality_configuration.py",
)
FATAL_RUFF_RULES = {"E9", "F63", "F7", "F82"}
NORMAL_RUFF_RULES = "E4,E7,E9,F,I,UP,B,SIM"
EXACT_F821_DEFERRALS: set[str] = set()


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
            "uv",
            "run",
            "--locked",
            "--all-extras",
            "--dev",
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
