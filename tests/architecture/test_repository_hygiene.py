from __future__ import annotations

import importlib
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


def test_public_package_exports_are_unique_and_resolvable() -> None:
    for module_name in ("travis.ai", "travis.coding_agent", "travis.tui"):
        module = importlib.import_module(module_name)
        exported = module.__all__

        assert len(exported) == len(set(exported)), module_name
        assert [name for name in exported if not hasattr(module, name)] == [], module_name
