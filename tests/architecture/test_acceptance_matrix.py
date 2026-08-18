from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

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


def _classified_rows(verifier):
    assert verifier.AcceptanceRow._fields[-1] == "evidence_class"
    rows = {}
    for acceptance_id in verifier.REQUIRED_IDS:
        if acceptance_id in {"live-21-prompt-tui", "public-repository"}:
            evidence_class = "live-required"
            status = "blocked" if acceptance_id == "live-21-prompt-tui" else "pending"
        elif acceptance_id == "pi-sdk-production-qualification":
            evidence_class = "manual"
            status = "passed"
        else:
            evidence_class = "automated-required"
            status = "passed"
        rows[acceptance_id] = verifier.AcceptanceRow(
            acceptance_id,
            f"requirement {acceptance_id}",
            "command",
            "expected",
            "evidence",
            status,
            evidence_class,
        )
    return rows


def _mock_current_commit(verifier, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": "current-head\n"})(),
    )


def _write_evidence(path: Path, results: dict[str, str], *, commit: str = "current-head") -> None:
    path.write_text(
        json.dumps({"commit": commit, "results": results}),
        encoding="utf-8",
    )


def test_acceptance_matrix_has_every_required_row() -> None:
    verifier = _verifier_module()
    assert verifier.AcceptanceRow._fields[-1] == "evidence_class"
    assert hasattr(verifier, "VALID_CLASSES")
    matrix = verifier.load_acceptance_matrix(ROOT / "docs/verification/acceptance-matrix.md")

    assert set(matrix) == verifier.REQUIRED_IDS
    assert all(row.requirement for row in matrix.values())
    assert all(row.command for row in matrix.values())
    assert all(row.expected for row in matrix.values())
    assert all(row.evidence for row in matrix.values())
    assert all(row.status in {"pending", "passed", "failed", "blocked"} for row in matrix.values())
    assert all(row.evidence_class in verifier.VALID_CLASSES for row in matrix.values())
    assert matrix["live-21-prompt-tui"].evidence_class == "live-required"
    assert matrix["public-repository"].evidence_class == "live-required"
    assert matrix["pi-sdk-production-qualification"].evidence_class == "manual"
    assert {
        row.acceptance_id
        for row in matrix.values()
        if row.evidence_class == "automated-required"
    }


@pytest.mark.parametrize("bad_class", [None, "always-pass"])
def test_acceptance_matrix_rejects_missing_or_invalid_classes(
    tmp_path: Path,
    bad_class: str | None,
) -> None:
    verifier = _verifier_module()
    rows = [
        "| ID | Requirement | Command | Expected | Evidence | Status | Class |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, acceptance_id in enumerate(sorted(verifier.REQUIRED_IDS)):
        evidence_class = bad_class if index == 0 else "automated-required"
        cells = [acceptance_id, "requirement", "command", "expected", "evidence", "passed"]
        if evidence_class is not None:
            cells.append(evidence_class)
        rows.append("| " + " | ".join(cells) + " |")
    matrix_path = tmp_path / "acceptance-matrix.md"
    matrix_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(verifier.AcceptanceMatrixError, match="evidence class"):
        verifier.load_acceptance_matrix(matrix_path)


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
    assert report["memory"] == {
        "enabled": False,
        "storeAvailable": False,
        "allowedScopes": ["project"],
        "limits": {
            "maxFactBytes": 65536,
            "maxFactsPerScope": 5000,
            "maxTotalBytes": 1073741824,
            "recallLimit": 20,
            "recallBytes": 32768,
        },
        "counts": {"project": None, "global": None},
        "automaticRetention": False,
        "automaticInjection": False,
    }
    assert report["nativeAcceleration"] == {
        "baseline": "python",
        "benchmarkAvailable": True,
        "candidatePresent": False,
        "decision": "retain_python",
        "thresholds": {
            "minimumSpeedup": 2.0,
            "minimumWallShare": 0.05,
            "maximumCoefficientOfVariation": 0.15,
        },
    }

    encoded = json.dumps(
        {
            "agentRoles": report["agentRoles"],
            "subagentSupervisor": report["subagentSupervisor"],
            "operationJournal": report["operationJournal"],
            "memory": report["memory"],
            "nativeAcceleration": report["nativeAcceleration"],
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
    assert "rows" in inspect.signature(verifier.verify_current_commit).parameters
    rows = _classified_rows(verifier)
    _mock_current_commit(verifier, monkeypatch)
    evidence = tmp_path / "acceptance-evidence.json"
    automated_results = {
        acceptance_id: "passed"
        for acceptance_id, row in rows.items()
        if row.evidence_class == "automated-required"
    }
    _write_evidence(
        evidence,
        automated_results,
        commit="not-the-current-commit",
    )

    with pytest.raises(verifier.AcceptanceEvidenceError, match="current commit"):
        verifier.verify_current_commit(evidence, rows, root=ROOT)


@pytest.mark.parametrize("failed_value", [None, "failed"])
def test_current_commit_requires_every_automated_result_to_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_value: str | None,
) -> None:
    verifier = _verifier_module()
    assert "rows" in inspect.signature(verifier.verify_current_commit).parameters
    rows = _classified_rows(verifier)
    _mock_current_commit(verifier, monkeypatch)
    automated_ids = sorted(
        acceptance_id
        for acceptance_id, row in rows.items()
        if row.evidence_class == "automated-required"
    )
    results = {acceptance_id: "passed" for acceptance_id in automated_ids}
    if failed_value is None:
        results.pop(automated_ids[0])
    else:
        results[automated_ids[0]] = failed_value
    evidence = tmp_path / "acceptance-evidence.json"
    _write_evidence(evidence, results)

    with pytest.raises(verifier.AcceptanceEvidenceError, match="incomplete"):
        verifier.verify_current_commit(evidence, rows, root=ROOT)


def test_blocked_live_evidence_is_reported_without_failing_automated_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    assert "rows" in inspect.signature(verifier.verify_current_commit).parameters
    rows = _classified_rows(verifier)
    _mock_current_commit(verifier, monkeypatch)
    evidence = tmp_path / "acceptance-evidence.json"
    automated_results = {
        acceptance_id: "passed"
        for acceptance_id, row in rows.items()
        if row.evidence_class == "automated-required"
    }
    _write_evidence(evidence, automated_results)

    verification = verifier.verify_current_commit(evidence, rows, root=ROOT)

    assert verification["results"] == automated_results
    assert verification["non_automated"]["live-required"]["blocked"] == [
        "live-21-prompt-tui"
    ]
    assert "live-21-prompt-tui" not in verification["results"]


def test_markdown_passed_status_is_not_current_commit_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    assert "rows" in inspect.signature(verifier.verify_current_commit).parameters
    rows = _classified_rows(verifier)
    _mock_current_commit(verifier, monkeypatch)
    evidence = tmp_path / "acceptance-evidence.json"
    _write_evidence(evidence, {})

    assert all(
        row.status == "passed"
        for row in rows.values()
        if row.evidence_class == "automated-required"
    )
    with pytest.raises(verifier.AcceptanceEvidenceError, match="incomplete"):
        verifier.verify_current_commit(evidence, rows, root=ROOT)


def test_record_automated_evidence_writes_only_bounded_automated_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    assert hasattr(verifier, "record_automated_evidence")
    rows = _classified_rows(verifier)
    _mock_current_commit(verifier, monkeypatch)
    evidence = tmp_path / "acceptance-evidence.json"

    payload = verifier.record_automated_evidence(evidence, rows, root=ROOT)

    assert json.loads(evidence.read_text(encoding="utf-8")) == payload
    assert payload["commit"] == "current-head"
    assert payload["results"] == {
        acceptance_id: "passed"
        for acceptance_id, row in rows.items()
        if row.evidence_class == "automated-required"
    }


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
