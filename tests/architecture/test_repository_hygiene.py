from __future__ import annotations

from pathlib import Path

from scripts.check_repository_hygiene import inspect_repository


ROOT = Path(__file__).resolve().parents[2]


def test_repository_hygiene_is_clean() -> None:
    report = inspect_repository(ROOT)

    assert report.unused_dependencies == ()
    assert report.camel_symbols == ()
    assert report.duplicate_groups == ()
    assert report.oversized_tests == ()
    assert report.forbidden_compatibility == ()
