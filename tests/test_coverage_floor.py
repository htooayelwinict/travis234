from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_coverage_floor.py"


def _write_coverage(
    path: Path,
    *,
    covered_lines: int,
    num_statements: int,
    covered_branches: int | None,
    num_branches: int | None,
) -> None:
    totals = {
        "covered_lines": covered_lines,
        "num_statements": num_statements,
    }
    if covered_branches is not None:
        totals["covered_branches"] = covered_branches
    if num_branches is not None:
        totals["num_branches"] = num_branches
    path.write_text(json.dumps({"totals": totals}), encoding="utf-8")


def _run_checker(path: Path) -> subprocess.CompletedProcess[str]:
    assert CHECKER.is_file(), "coverage-floor checker is missing"
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            str(path),
            "--statements",
            "83.0",
            "--branches",
            "68.0",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("covered_lines", "covered_branches", "expected"),
    [
        (82, 100, "statement coverage 82.00% is below 83.00%"),
        (100, 67, "branch coverage 67.00% is below 68.00%"),
    ],
)
def test_checker_rejects_each_floor_independently(
    tmp_path: Path,
    covered_lines: int,
    covered_branches: int,
    expected: str,
) -> None:
    report = tmp_path / "coverage.json"
    _write_coverage(
        report,
        covered_lines=covered_lines,
        num_statements=100,
        covered_branches=covered_branches,
        num_branches=100,
    )

    completed = _run_checker(report)

    assert completed.returncode == 1
    assert expected in completed.stderr


def test_checker_rejects_missing_branch_data(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    _write_coverage(
        report,
        covered_lines=100,
        num_statements=100,
        covered_branches=None,
        num_branches=None,
    )

    completed = _run_checker(report)

    assert completed.returncode == 1
    assert "branch coverage data is missing" in completed.stderr


def test_checker_accepts_metrics_exactly_at_each_floor(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    _write_coverage(
        report,
        covered_lines=83,
        num_statements=100,
        covered_branches=68,
        num_branches=100,
    )

    completed = _run_checker(report)

    assert completed.returncode == 0, completed.stderr
    assert "statement coverage: 83.00% (floor 83.00%)" in completed.stdout
    assert "branch coverage: 68.00% (floor 68.00%)" in completed.stdout
