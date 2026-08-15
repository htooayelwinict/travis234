from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[2]
VERIFIER_PATH = ROOT / "scripts/verify_acceptance.py"


def _verifier_module():
    assert VERIFIER_PATH.is_file(), "acceptance verifier is missing"
    spec = importlib.util.spec_from_file_location("verify_acceptance", VERIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_matrix_has_every_required_row() -> None:
    verifier = _verifier_module()
    matrix = verifier.load_acceptance_matrix(ROOT / "docs/verification/acceptance-matrix.md")

    assert set(matrix) == verifier.REQUIRED_IDS
    assert all(row.requirement for row in matrix.values())
    assert all(row.command for row in matrix.values())
    assert all(row.expected for row in matrix.values())
    assert all(row.evidence for row in matrix.values())
    assert all(row.status in {"pending", "passed", "failed", "blocked"} for row in matrix.values())


def test_parity_report_has_only_resolved_evidence() -> None:
    verifier = _verifier_module()

    report = verifier.verify_parity_contracts(root=ROOT)

    assert report["schema_version"] == 1
    assert report["summary"]["pi"]["invalid"] == 0
    assert report["summary"]["hermes"]["invalid"] == 0
    assert set(report["toolPolicy"]) == {
        "mode",
        "effectCounts",
        "undeclaredToolCount",
    }
    assert report["toolPolicy"]["mode"] == "audit"
    assert set(report["toolPolicy"]["effectCounts"]) == {
        "read",
        "write",
        "execute",
        "network",
    }
    assert all(
        isinstance(count, int) and count >= 0
        for count in report["toolPolicy"]["effectCounts"].values()
    )
    assert report["toolPolicy"]["undeclaredToolCount"] == 0
    assert set(report["languageServices"]) == {"configured", "active", "limits"}
    assert report["languageServices"]["active"] == 0
    assert set(report["languageServices"]["limits"]) == {
        "maxActiveServers",
        "startupSeconds",
        "requestSeconds",
        "maxRestarts",
        "restartWindowSeconds",
        "maxFrameBytes",
        "maxInlineOutputBytes",
        "maxApplyOriginalBytes",
    }
    assert set(report["agentRoles"]) == {"roles"}
    assert all(
        set(role) == {"name", "provenance"}
        and set(role["provenance"]) == {"provider", "source", "scope", "origin"}
        for role in report["agentRoles"]["roles"]
    )
    assert set(report["subagentSupervisor"]) == {
        "maxThreads",
        "maxDepth",
        "activeCount",
    }
    assert report["subagentSupervisor"] == {
        "maxThreads": 3,
        "maxDepth": 1,
        "activeCount": 0,
    }
    assert report["operationJournal"] == {
        "mode": "observe",
        "schemaVersion": 1,
        "counts": {
            "operationStates": 5,
            "effectStates": 5,
            "replayPolicies": 1,
        },
    }

    encoded = json.dumps(
        {
            "agentRoles": report["agentRoles"],
            "subagentSupervisor": report["subagentSupervisor"],
            "operationJournal": report["operationJournal"],
        },
        sort_keys=True,
    )
    assert all(
        forbidden not in encoded
        for forbidden in ("description", "context", "goal", "result", "credential")
    )


def test_current_commit_verifier_rejects_stale_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": "current-head\n"})(),
    )
    evidence = tmp_path / "acceptance-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "commit": "not-the-current-commit",
                "results": {acceptance_id: "passed" for acceptance_id in verifier.REQUIRED_IDS},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.AcceptanceEvidenceError, match="current commit"):
        verifier.verify_current_commit(evidence, root=ROOT)


def test_parity_json_cli_runs_directly_without_pythonpath() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFIER_PATH), "--parity-json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["toolPolicy"]["mode"] == "audit"
