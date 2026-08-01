from __future__ import annotations

import json
import tomllib
from pathlib import Path

from travis import cli


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
