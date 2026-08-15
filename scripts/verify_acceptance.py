#!/usr/bin/env python3
"""Validate the Travis234 requirement-to-evidence acceptance matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

_SOURCE_ROOT = str(Path(__file__).resolve().parents[1])
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

from travis.coding_agent.policy import TOOL_EFFECT_ORDER
from travis.coding_agent.language_services.types import LanguageServiceLimits
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.tools import create_all_tool_definitions

try:
    from scripts.parity_contracts import build_parity_report
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from parity_contracts import build_parity_report


REQUIRED_IDS = {
    "rebrand",
    "finding-01-monitor-ownership",
    "finding-02-stdin-ack",
    "finding-03-ctrl-c-escalation",
    "finding-06-installed-metadata",
    "finding-07-bounded-shutdown",
    "finding-08-facade-decomposition",
    "finding-09-provider-ownership",
    "finding-10-session-index",
    "finding-11-compaction-transactions",
    "finding-12-advisory-classifier",
    "finding-14-cleanup",
    "red-zone-parity",
    "yellow-zone-faults",
    "green-zone-package",
    "live-21-prompt-tui",
    "public-repository",
    "production-project-trust",
    "openrouter-route-capacity",
    "canonical-context-envelope",
    "python-native-pi-resource-parity",
    "pi-cli-tui-session-parity",
    "pi-sdk-production-qualification",
}
VALID_STATUSES = {"pending", "passed", "failed", "blocked"}


class AcceptanceMatrixError(RuntimeError):
    pass


class AcceptanceEvidenceError(RuntimeError):
    pass


class AcceptanceRow(NamedTuple):
    acceptance_id: str
    requirement: str
    command: str
    expected: str
    evidence: str
    status: str


def load_acceptance_matrix(path: str | Path) -> dict[str, AcceptanceRow]:
    matrix_path = Path(path)
    if not matrix_path.is_file():
        raise AcceptanceMatrixError(f"acceptance matrix is missing: {matrix_path}")
    rows: dict[str, AcceptanceRow] = {}
    for raw_line in matrix_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] in {"ID", "---"} or set(cells[0]) == {"-"}:
            continue
        row = AcceptanceRow(*cells)
        if row.acceptance_id in rows:
            raise AcceptanceMatrixError(f"duplicate acceptance ID: {row.acceptance_id}")
        if row.status not in VALID_STATUSES:
            raise AcceptanceMatrixError(
                f"invalid status for {row.acceptance_id}: {row.status}"
            )
        rows[row.acceptance_id] = row
    missing = REQUIRED_IDS - set(rows)
    extra = set(rows) - REQUIRED_IDS
    if missing or extra:
        raise AcceptanceMatrixError(
            f"acceptance IDs differ; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return rows


def verify_current_commit(evidence_path: str | Path, *, root: str | Path) -> dict[str, object]:
    repository = Path(root).resolve()
    evidence_file = Path(evidence_path)
    try:
        payload = json.loads(evidence_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceEvidenceError(f"acceptance evidence is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise AcceptanceEvidenceError("acceptance evidence root must be an object")
    current_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if payload.get("commit") != current_commit:
        raise AcceptanceEvidenceError(
            "acceptance evidence does not describe the current commit"
        )
    results = payload.get("results")
    if not isinstance(results, dict):
        raise AcceptanceEvidenceError("acceptance evidence results must be an object")
    missing = REQUIRED_IDS - set(results)
    failures = {
        acceptance_id: results.get(acceptance_id)
        for acceptance_id in REQUIRED_IDS
        if results.get(acceptance_id) != "passed"
    }
    if missing or failures:
        raise AcceptanceEvidenceError(
            f"acceptance evidence is incomplete; missing={sorted(missing)}, failures={failures}"
        )
    return payload


def verify_parity_contracts(*, root: str | Path) -> dict[str, object]:
    repository = Path(root).resolve()
    report = build_parity_report(root=repository)
    invalid = {
        source: values["invalid"]
        for source, values in report["summary"].items()
        if values["invalid"]
    }
    if invalid:
        raise AcceptanceMatrixError(f"parity contract evidence is invalid: {invalid}")
    definitions = create_all_tool_definitions(str(repository))
    policy_settings = SettingsManager.in_memory().get_tool_policy_settings()
    report["toolPolicy"] = {
        "mode": policy_settings["mode"],
        "effectCounts": {
            effect: sum(effect in definition.effects for definition in definitions)
            for effect in TOOL_EFFECT_ORDER
        },
        "undeclaredToolCount": sum(not definition.effects for definition in definitions),
    }
    language_configs = SettingsManager.in_memory().get_language_server_configs()
    language_limits = LanguageServiceLimits()
    report["languageServices"] = {
        "configured": len(language_configs),
        "active": 0,
        "limits": {
            "maxActiveServers": language_limits.max_active_servers,
            "startupSeconds": language_limits.startup_timeout_seconds,
            "requestSeconds": language_limits.request_timeout_seconds,
            "maxRestarts": language_limits.max_restarts,
            "restartWindowSeconds": language_limits.restart_window_seconds,
            "maxFrameBytes": language_limits.max_frame_bytes,
            "maxInlineOutputBytes": language_limits.max_inline_output_bytes,
            "maxApplyOriginalBytes": language_limits.max_apply_original_bytes,
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix",
        default=str(root / "docs/verification/acceptance-matrix.md"),
    )
    parser.add_argument(
        "--evidence",
        default=str(root / "docs/verification/acceptance-evidence.json"),
    )
    parser.add_argument("--require-current-commit", action="store_true")
    parser.add_argument(
        "--parity-json",
        action="store_true",
        help="Print the validated Pi/Hermes contract report as JSON.",
    )
    args = parser.parse_args(argv)
    try:
        rows = load_acceptance_matrix(args.matrix)
        parity_report = verify_parity_contracts(root=root)
        if args.require_current_commit:
            verify_current_commit(args.evidence, root=root)
    except (AcceptanceMatrixError, AcceptanceEvidenceError, subprocess.CalledProcessError) as error:
        print(f"acceptance verification failed: {error}")
        return 1
    if args.parity_json:
        print(json.dumps(parity_report, sort_keys=True))
    else:
        print(f"acceptance matrix: {len(rows)} required rows")
        print(
            "parity contracts: "
            f"pi={parity_report['summary']['pi']['total']} "
            f"hermes={parity_report['summary']['hermes']['total']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
