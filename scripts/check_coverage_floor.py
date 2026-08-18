#!/usr/bin/env python3
"""Enforce independent statement and branch floors from coverage.py JSON."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


class CoverageDataError(RuntimeError):
    pass


def _percentage(
    totals: dict[str, object],
    *,
    covered_key: str,
    total_key: str,
    label: str,
) -> float:
    covered = totals.get(covered_key)
    total = totals.get(total_key)
    if (
        isinstance(covered, bool)
        or not isinstance(covered, int)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
    ):
        raise CoverageDataError(f"{label} coverage data is missing")
    if covered < 0 or covered > total:
        raise CoverageDataError(f"{label} coverage counts are invalid")
    return covered / total * 100.0


def load_percentages(path: str | Path) -> tuple[float, float]:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageDataError(f"coverage report is unreadable: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("totals"), dict):
        raise CoverageDataError("coverage report totals are missing")
    totals = payload["totals"]
    statements = _percentage(
        totals,
        covered_key="covered_lines",
        total_key="num_statements",
        label="statement",
    )
    branches = _percentage(
        totals,
        covered_key="covered_branches",
        total_key="num_branches",
        label="branch",
    )
    return statements, branches


def _floor(value: str) -> float:
    floor = float(value)
    if not math.isfinite(floor) or not 0.0 <= floor <= 100.0:
        raise argparse.ArgumentTypeError("coverage floor must be between 0 and 100")
    return floor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="coverage.py JSON report")
    parser.add_argument("--statements", type=_floor, required=True)
    parser.add_argument("--branches", type=_floor, required=True)
    args = parser.parse_args(argv)

    try:
        statements, branches = load_percentages(args.report)
    except CoverageDataError as error:
        print(error, file=sys.stderr)
        return 1

    failures: list[str] = []
    if statements < args.statements:
        failures.append(
            f"statement coverage {statements:.2f}% is below {args.statements:.2f}%"
        )
    if branches < args.branches:
        failures.append(f"branch coverage {branches:.2f}% is below {args.branches:.2f}%")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"statement coverage: {statements:.2f}% (floor {args.statements:.2f}%)")
    print(f"branch coverage: {branches:.2f}% (floor {args.branches:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
