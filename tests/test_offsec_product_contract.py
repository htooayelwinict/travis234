from __future__ import annotations

import json
import tomllib
from pathlib import Path

from travis import cli
from travis.coding_agent import BuildSystemPromptOptions, build_system_prompt


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_identity_is_single_offsec_product() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    npm = json.loads(
        (ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8")
    )

    assert project["name"] == "travis234-offsec"
    assert project["scripts"] == {"travis234": "travis.cli:main"}
    assert npm["name"] == "@htooayelwinict/travis234-offsec"
    assert npm["bin"] == {"travis234": "bin/travis234.js"}


def test_core_cli_is_offsec_native_without_legacy_profiles() -> None:
    help_text = cli._build_parser(include_prompt=True).format_help()

    assert "Travis234 OffSec" in help_text
    for forbidden in (
        "--profile",
        "--agent-profile",
        "--engagement",
        "--challenge",
        "--ctfd-url",
        "--ctf-fixture-root",
        "--offsec-worker-user",
    ):
        assert forbidden not in help_text


def test_removed_dual_profile_tree_does_not_return() -> None:
    assert not (ROOT / "travis/offsec").exists()
    assert not (ROOT / "tests/offsec").exists()


def test_default_system_prompt_is_complete_offsec_contract(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=[
                "read",
                "grep",
                "find",
                "ls",
                "bash",
                "process",
                "tmux",
                "edit",
                "write",
                "spawn_subagent",
            ],
            tool_snippets={"tmux": "Manage named long-lived tmux sessions"},
        )
    )

    for required in (
        "Travis234 OffSec",
        "operator-directed security work",
        "DFIR cases",
        "incident response",
        "Orient",
        "Acquire",
        "Analyze",
        "Act",
        "Verify",
        "Record",
        "Kali Linux",
        "command -v",
        "apt-cache",
        "python3 -m venv",
        "/usr/share/wordlists",
        "ip -brief address",
        "ip route",
        "Facts",
        "Hypotheses",
        "Failed attempts",
        "Use bash for finite commands",
        "Use bash plus process for interactive programs",
        "Use tmux for listeners, reverse connections, OOB callbacks, relays",
        "Do not claim a flag, shell, vulnerability, credential, or impact",
        "running tmux sessions",
    ):
        assert required in prompt
    assert "expert coding assistant" not in prompt
    assert len(prompt) < 16_000


def test_bundled_delegation_guidance_matches_workspace_write_runtime() -> None:
    skill_paths = (
        ROOT / "skills/subagent-delegation/SKILL.md",
        ROOT / "travis/resources/skills/subagent-delegation/SKILL.md",
        ROOT / "packages/travis234-cli/skills/subagent-delegation/SKILL.md",
    )

    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert "workspace-write" in text
        assert "bash plus process" in text
        assert "tmux" in text
        assert "disjoint" in text
        assert "Do not let children spawn more subagents" in text
        assert "Subagents must remain read-only" not in text
        assert "parent should write" not in text


def test_investigating_security_targets_skill_contract() -> None:
    paths = (
        ROOT / "skills/investigating-security-targets/SKILL.md",
        ROOT / "travis/resources/skills/investigating-security-targets/SKILL.md",
        ROOT / "packages/travis234-cli/skills/investigating-security-targets/SKILL.md",
    )
    texts = [path.read_text(encoding="utf-8") for path in paths]

    assert len({text.encode("utf-8") for text in texts}) == 1
    text = texts[0]
    assert "description: Use when" in text
    for required in (
        "ranked hypotheses",
        "atomic test",
        "observable",
        "stop condition",
        "command -v",
        "bash plus process",
        "tmux",
        "Facts",
        "Failed attempts",
        "Verify",
    ):
        assert required in text
    assert len(text.split()) < 500


def test_triaging_security_incidents_skill_contract() -> None:
    paths = (
        ROOT / "skills/triaging-security-incidents/SKILL.md",
        ROOT / "travis/resources/skills/triaging-security-incidents/SKILL.md",
        ROOT / "packages/travis234-cli/skills/triaging-security-incidents/SKILL.md",
    )
    texts = [path.read_text(encoding="utf-8") for path in paths]

    assert len({text.encode("utf-8") for text in texts}) == 1
    text = texts[0]
    assert "description: Use when" in text
    for required in (
        "preserve",
        "SHA-256",
        "UTC",
        "volatile",
        "timeline",
        "scope",
        "containment",
        "Facts",
        "Hypotheses",
        "Chain of custody",
    ):
        assert required in text
    assert len(text.split()) < 500
