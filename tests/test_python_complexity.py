"""Contract tests for the repository Python complexity gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_python_complexity import (
    ComplexityAnalysisError,
    PROTECTED_PRODUCTION_EXCEPTIONS,
    analyze_python_tree,
    evaluate_complexity_report,
    main,
)

PROTECTED_LOOP = "travis/agent/agent_loop.py"


def _report(*, path: str, complexity: int) -> dict[str, object]:
    return {
        path: [
            {
                "name": "owner",
                "line": 10,
                "complexity": complexity,
            }
        ]
    }


def test_report_rejects_functions_above_threshold_and_accepts_boundary() -> None:
    assert evaluate_complexity_report(
        _report(path="travis/owner.py", complexity=25),
        max_complexity=25,
    ) == []
    assert evaluate_complexity_report(
        _report(path="travis/owner.py", complexity=26),
        max_complexity=25,
    ) == ["travis/owner.py:10: owner complexity 26 exceeds 25"]


def test_only_protected_loop_can_bypass_function_threshold() -> None:
    assert frozenset({PROTECTED_LOOP}) == PROTECTED_PRODUCTION_EXCEPTIONS
    assert evaluate_complexity_report(
        _report(path=PROTECTED_LOOP, complexity=99),
        max_complexity=25,
    ) == []
    assert evaluate_complexity_report(
        {},
        max_complexity=25,
        exceptions=frozenset({PROTECTED_LOOP, "travis/new_exception.py"}),
    ) == ["unsupported complexity exception: travis/new_exception.py"]


def test_migrated_module_average_may_not_exceed_grade_b() -> None:
    report = {
        "travis/migrated.py": [
            {"name": "first", "line": 1, "complexity": 11},
            {"name": "second", "line": 20, "complexity": 11},
        ],
        "travis/legacy.py": [
            {"name": "legacy", "line": 1, "complexity": 11},
        ],
    }

    assert evaluate_complexity_report(
        report,
        max_complexity=25,
        migrated_modules=frozenset({"travis/migrated.py"}),
    ) == ["travis/migrated.py: average complexity 11.00 grade C exceeds B"]


@pytest.mark.parametrize(
    "report",
    (
        None,
        [],
        {"travis/bad.py": "not-a-block-list"},
        {"travis/bad.py": [{"name": "missing fields"}]},
        {"travis/bad.py": [{"name": "bad", "line": 1, "complexity": "high"}]},
    ),
)
def test_malformed_reports_fail_closed(report: object) -> None:
    assert evaluate_complexity_report(report, max_complexity=25) == [
        "malformed complexity report"
    ]


def test_cli_analyzes_python_source_with_radon(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "owner.py").write_text(
        "def owner(value: bool) -> int:\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )

    assert main([str(source_root), "--max-complexity", "25"]) == 0
    assert capsys.readouterr().out == "Python complexity check passed.\n"


def test_analyzer_measures_class_methods(tmp_path: Path) -> None:
    source = tmp_path / "owner.py"
    source.write_text(
        "class Owner:\n"
        "    def method(self, enabled: bool) -> int:\n"
        "        if enabled:\n"
        "            return 1\n"
        "        return 0\n",
        encoding="utf-8",
    )

    report = analyze_python_tree(source)

    assert report["owner.py"] == [
        {"name": "Owner.method", "line": 2, "complexity": 2}
    ]


def test_analyzer_measures_nested_functions_and_closures(tmp_path: Path) -> None:
    source = tmp_path / "owner.py"
    source.write_text(
        "def outer(enabled: bool) -> object:\n"
        "    def inner() -> int:\n"
        "        if enabled:\n"
        "            return 1\n"
        "        return 0\n"
        "    return inner\n",
        encoding="utf-8",
    )

    report = analyze_python_tree(source)

    assert report["owner.py"] == [
        {"name": "outer", "line": 1, "complexity": 1},
        {"name": "inner", "line": 2, "complexity": 2},
    ]


def test_analyzer_rejects_requested_tree_without_python_files(tmp_path: Path) -> None:
    with pytest.raises(ComplexityAnalysisError, match="no Python files found"):
        analyze_python_tree(tmp_path)
