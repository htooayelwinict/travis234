#!/usr/bin/env python3
"""Fail closed when Python owners exceed the repository complexity policy."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from radon.complexity import cc_rank, cc_visit
from radon.visitors import Class, Function

PROTECTED_PRODUCTION_EXCEPTIONS = frozenset({"travis/agent/agent_loop.py"})


@dataclass(frozen=True, slots=True)
class ComplexityRecord:
    name: str
    line: int
    complexity: int


class ComplexityAnalysisError(RuntimeError):
    """Raised when a source owner cannot produce a trustworthy report."""


def _function_blocks(blocks: Iterable[Function | Class]) -> Iterator[Function]:
    for block in blocks:
        if not isinstance(block, Function):
            continue
        yield block
        yield from _function_blocks(block.closures)


def _display_path(path: Path, source: Path) -> str:
    resolved = path.resolve()
    for base in (Path.cwd().resolve(), source.resolve().parent):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def analyze_python_tree(source: Path) -> dict[str, list[dict[str, object]]]:
    if not source.exists():
        raise ComplexityAnalysisError(f"source path does not exist: {source}")
    candidates = [source] if source.is_file() else sorted(source.rglob("*.py"))
    paths = [
        path
        for path in candidates
        if path.suffix == ".py" and path.is_file()
    ]
    if not paths:
        raise ComplexityAnalysisError(f"no Python files found under requested source: {source}")
    report: dict[str, list[dict[str, object]]] = {}
    for path in paths:
        display_path = _display_path(path, source)
        try:
            source_text = path.read_text(encoding="utf-8")
            blocks = cc_visit(source_text)
        except (OSError, SyntaxError) as error:
            raise ComplexityAnalysisError(f"{display_path}: {error}") from error
        report[display_path] = [
            {
                "name": block.fullname,
                "line": block.lineno,
                "complexity": block.complexity,
            }
            for block in _function_blocks(blocks)
        ]
    return report


def _parse_complexity_report(
    report: object,
) -> dict[str, tuple[ComplexityRecord, ...]] | None:
    if not isinstance(report, dict):
        return None
    parsed: dict[str, tuple[ComplexityRecord, ...]] = {}
    for path, raw_records in report.items():
        if not isinstance(path, str) or not isinstance(raw_records, list):
            return None
        records: list[ComplexityRecord] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, dict):
                return None
            name = raw_record.get("name")
            line = raw_record.get("line")
            complexity = raw_record.get("complexity")
            if (
                not isinstance(name, str)
                or not isinstance(line, int)
                or isinstance(line, bool)
                or not isinstance(complexity, int)
                or isinstance(complexity, bool)
            ):
                return None
            records.append(
                ComplexityRecord(
                    name=name,
                    line=line,
                    complexity=complexity,
                )
            )
        parsed[path] = tuple(records)
    return parsed


def evaluate_complexity_report(
    report: object,
    *,
    max_complexity: int,
    migrated_modules: frozenset[str] = frozenset(),
    exceptions: frozenset[str] = PROTECTED_PRODUCTION_EXCEPTIONS,
) -> list[str]:
    unexpected_exceptions = sorted(exceptions - PROTECTED_PRODUCTION_EXCEPTIONS)
    violations = [
        f"unsupported complexity exception: {path}"
        for path in unexpected_exceptions
    ]
    parsed = _parse_complexity_report(report)
    if parsed is None:
        return [*violations, "malformed complexity report"]

    for path in sorted(parsed):
        records = parsed[path]
        if path not in exceptions:
            violations.extend(
                f"{path}:{record.line}: {record.name} complexity "
                f"{record.complexity} exceeds {max_complexity}"
                for record in records
                if record.complexity > max_complexity
            )
        if path in migrated_modules and path not in exceptions and records:
            average = sum(record.complexity for record in records) / len(records)
            grade = cc_rank(average)
            if grade > "B":
                violations.append(
                    f"{path}: average complexity {average:.2f} grade {grade} exceeds B"
                )
    return violations


def _migrated_modules(config_path: Path) -> frozenset[str]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComplexityAnalysisError(f"{config_path}: {error}") from error
    if not isinstance(config, dict):
        raise ComplexityAnalysisError(f"{config_path}: expected a JSON object")
    included = config.get("include")
    if not isinstance(included, list) or not all(isinstance(path, str) for path in included):
        raise ComplexityAnalysisError(f"{config_path}: include must be a list of paths")
    return frozenset(
        path
        for path in included
        if path.startswith("travis/") and path.endswith(".py")
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--max-complexity", type=int, default=25)
    parser.add_argument(
        "--pyright-config",
        type=Path,
        default=Path("pyrightconfig.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_complexity < 1:
        print("max complexity must be at least 1")
        return 2
    try:
        report = analyze_python_tree(args.source)
        migrated_modules = _migrated_modules(args.pyright_config)
    except ComplexityAnalysisError as error:
        print(f"Python complexity check failed: {error}")
        return 1
    violations = evaluate_complexity_report(
        report,
        max_complexity=args.max_complexity,
        migrated_modules=migrated_modules,
    )
    if violations:
        print("Python complexity check failed:")
        for violation in violations:
            print(violation)
        return 1
    print("Python complexity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
