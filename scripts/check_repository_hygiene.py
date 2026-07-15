#!/usr/bin/env python3
"""Measure dependency, compatibility, duplication, and test ownership debt."""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import re
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT_FIELDS = (
    "unused_dependencies",
    "camel_symbols",
    "duplicate_groups",
    "oversized_tests",
    "forbidden_compatibility",
    "reference_coupling",
    "distribution_leaks",
)
_CAMEL_SYMBOL = re.compile(r"^[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*$")
_DISTRIBUTION_NAME = re.compile(r"^[A-Za-z0-9_.-]+")
_DISTRIBUTION_IMPORT_ROOTS = {
    "google-auth": frozenset({"google"}),
    "httpx": frozenset({"httpx"}),
    "jsonschema": frozenset({"jsonschema"}),
    "langgraph": frozenset({"langgraph"}),
    "openrouter": frozenset({"openrouter"}),
    "psutil": frozenset({"psutil"}),
    "pydantic": frozenset({"pydantic"}),
    "pyyaml": frozenset({"yaml"}),
}
_COMPATIBILITY_MODULE_NAMES = frozenset({"compat.py", "compatibility.py"})
_FORBIDDEN_RUNTIME_SYMBOLS = frozenset({"_PROCESS_ARGUMENT_ALIASES", "_install_subagent_tool_aliases"})
_REFERENCE_IMPORT_ROOTS = frozenset({"pi", "hermes_agent", "appv231"})
_REFERENCE_DISTRIBUTION_MARKERS = ("pi/", "hermes-agent", "appv231", "PI_HERMES_TRAVIS_CROSS_CHECK_REPORT")


@dataclass(frozen=True)
class HygieneReport:
    unused_dependencies: tuple[str, ...]
    camel_symbols: tuple[str, ...]
    duplicate_groups: tuple[tuple[str, ...], ...]
    oversized_tests: tuple[str, ...]
    forbidden_compatibility: tuple[str, ...]
    reference_coupling: tuple[str, ...]
    distribution_leaks: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not any(dataclasses.astuple(self))


@dataclass(frozen=True)
class _FunctionBody:
    key: str
    location: str


def inspect_repository(root: Path = ROOT) -> HygieneReport:
    runtime_files = tuple(_python_files(root / "travis"))
    parsed_runtime = tuple((path, _parse(path)) for path in runtime_files)
    return HygieneReport(
        unused_dependencies=_unused_dependencies(root / "pyproject.toml", parsed_runtime),
        camel_symbols=_camel_symbols(root, parsed_runtime),
        duplicate_groups=_duplicate_groups(root, parsed_runtime),
        oversized_tests=_oversized_tests(root),
        forbidden_compatibility=_forbidden_compatibility(root, parsed_runtime),
        reference_coupling=_reference_coupling(root, parsed_runtime),
        distribution_leaks=_distribution_leaks(root),
    )


def _python_files(directory: Path) -> Iterator[Path]:
    if not directory.exists():
        return
    yield from sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _unused_dependencies(
    pyproject_path: Path,
    parsed_runtime: Sequence[tuple[Path, ast.Module]],
) -> tuple[str, ...]:
    metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    declared = {
        _normalize_distribution_name(match.group(0))
        for item in metadata.get("project", {}).get("dependencies", ())
        if (match := _DISTRIBUTION_NAME.match(item.strip())) is not None
    }
    imported_roots: set[str] = set()
    for _path, tree in parsed_runtime:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
    unused = []
    for distribution in sorted(declared):
        import_roots = _DISTRIBUTION_IMPORT_ROOTS.get(
            distribution,
            frozenset({distribution.replace("-", "_")}),
        )
        if imported_roots.isdisjoint(import_roots):
            unused.append(distribution)
    return tuple(unused)


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


class _CamelSymbolVisitor(ast.NodeVisitor):
    def __init__(self, *, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.scope: list[str] = []
        self.results: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor contract
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802 - ast visitor contract
        for alias in node.names:
            if alias.asname:
                self._record(alias.asname, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802 - ast visitor contract
        self.visit_Import(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor contract
        self._record(node.name, node.lineno)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast visitor contract
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - ast visitor contract
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            self._record_exported_names(node.value)
        for target in node.targets:
            self._record_target(target)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 - ast visitor contract
        self._record_target(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802 - ast visitor contract
        self._record_target(node.target)
        self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802 - ast visitor contract
        self._record_target(node.target)
        self.visit(node.value)

    def _record_target(self, node: ast.expr) -> None:
        if isinstance(node, ast.Name):
            self._record(node.id, node.lineno)
        elif isinstance(node, ast.Attribute):
            self._record(node.attr, node.lineno)
            self.visit(node.value)
        elif isinstance(node, (ast.List, ast.Tuple)):
            for element in node.elts:
                self._record_target(element)

    def _record(self, name: str, line: int) -> None:
        if not _CAMEL_SYMBOL.fullmatch(name):
            return
        relative = self.path.relative_to(self.root).as_posix()
        qualified = ".".join((*self.scope, name)) if self.scope else name
        self.results.add(f"{relative}:{line}:{qualified}")

    def _record_exported_names(self, value: ast.expr) -> None:
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                self._record(element.value, element.lineno)


def _camel_symbols(root: Path, parsed_runtime: Sequence[tuple[Path, ast.Module]]) -> tuple[str, ...]:
    results: set[str] = set()
    for path, tree in parsed_runtime:
        visitor = _CamelSymbolVisitor(path=path, root=root)
        visitor.visit(tree)
        results.update(visitor.results)
    return tuple(sorted(results))


class _FunctionBodyVisitor(ast.NodeVisitor):
    def __init__(self, *, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.scope: list[str] = []
        self.functions: list[_FunctionBody] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor contract
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor contract
        self._record(node)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast visitor contract
        self.visit_FunctionDef(node)

    def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        body = list(node.body)
        if body and _is_docstring(body[0]):
            body = body[1:]
        physical_lines = (node.end_lineno or node.lineno) - node.lineno + 1
        if not body or (len(body) < 3 and physical_lines < 4) or _is_stub_body(body):
            return
        normalized = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
        relative = self.path.relative_to(self.root).as_posix()
        qualified = ".".join((*self.scope, node.name)) if self.scope else node.name
        self.functions.append(_FunctionBody(normalized, f"{relative}:{node.lineno}:{qualified}"))


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_stub_body(body: Sequence[ast.stmt]) -> bool:
    if len(body) != 1:
        return False
    statement = body[0]
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return statement.value.value is Ellipsis
    return isinstance(statement, ast.Raise) and isinstance(statement.exc, ast.Call) and (
        isinstance(statement.exc.func, ast.Name) and statement.exc.func.id == "NotImplementedError"
    )


def _duplicate_groups(
    root: Path,
    parsed_runtime: Sequence[tuple[Path, ast.Module]],
) -> tuple[tuple[str, ...], ...]:
    groups: dict[str, list[str]] = defaultdict(list)
    for path, tree in parsed_runtime:
        visitor = _FunctionBodyVisitor(path=path, root=root)
        visitor.visit(tree)
        for function in visitor.functions:
            groups[function.key].append(function.location)
    duplicates = [tuple(sorted(locations)) for locations in groups.values() if len(locations) > 1]
    return tuple(sorted(duplicates))


def _oversized_tests(root: Path) -> tuple[str, ...]:
    oversized = []
    for path in _python_files(root / "tests"):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > 2_500:
            oversized.append(f"{path.relative_to(root).as_posix()}:{line_count}")
    return tuple(sorted(oversized))


def _forbidden_compatibility(
    root: Path,
    parsed_runtime: Sequence[tuple[Path, ast.Module]],
) -> tuple[str, ...]:
    results: set[str] = set()
    for path, tree in parsed_runtime:
        relative = path.relative_to(root).as_posix()
        if path.name in _COMPATIBILITY_MODULE_NAMES:
            results.add(f"{relative}:compatibility-only module")
        for node in ast.walk(tree):
            name = _defined_or_assigned_name(node)
            if name in _FORBIDDEN_RUNTIME_SYMBOLS:
                results.add(f"{relative}:{getattr(node, 'lineno', 1)}:{name}")
            if isinstance(node, ast.Call) and _is_legacy_run_tool_definition(node):
                results.add(f"{relative}:{node.lineno}:subagent run tool alias")
    return tuple(sorted(results))


def _reference_coupling(
    root: Path,
    parsed_runtime: Sequence[tuple[Path, ast.Module]],
) -> tuple[str, ...]:
    results: set[str] = set()
    for path, tree in parsed_runtime:
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            names: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = (node.module,)
            for name in names:
                if name.partition(".")[0] in _REFERENCE_IMPORT_ROOTS:
                    results.add(f"{relative}:{node.lineno}:{name}")
    return tuple(sorted(results))


def _distribution_leaks(root: Path) -> tuple[str, ...]:
    results: set[str] = set()
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package_find = metadata.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    if package_find.get("include") != ["travis*"]:
        results.add("pyproject.toml:setuptools package include is not restricted to travis*")
    package_data = metadata.get("tool", {}).get("setuptools", {}).get("package-data", {})
    for owner, patterns in package_data.items():
        for pattern in patterns:
            value = f"{owner}/{pattern}"
            if any(marker.lower() in value.lower() for marker in _REFERENCE_DISTRIBUTION_MARKERS):
                results.add(f"pyproject.toml:reference package data:{value}")

    npm_path = root / "packages" / "travis234-cli" / "package.json"
    npm = json.loads(npm_path.read_text(encoding="utf-8"))
    for entry in npm.get("files", ()):
        if any(marker.lower() in str(entry).lower() for marker in _REFERENCE_DISTRIBUTION_MARKERS):
            results.add(f"packages/travis234-cli/package.json:reference file:{entry}")
    return tuple(sorted(results))


def _defined_or_assigned_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _FORBIDDEN_RUNTIME_SYMBOLS:
                return target.id
    return None


def _is_legacy_run_tool_definition(node: ast.Call) -> bool:
    function_name = ""
    if isinstance(node.func, ast.Name):
        function_name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        function_name = node.func.attr
    if function_name != "ToolDefinition":
        return False
    return any(
        keyword.arg == "name"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "run"
        for keyword in node.keywords
    )


def _selected_report(report: HygieneReport, fields: Iterable[str]) -> dict[str, object]:
    return {field: getattr(report, field) for field in fields}


def _print_report(report: HygieneReport, fields: Sequence[str]) -> None:
    for field, value in _selected_report(report, fields).items():
        print(f"{field}: {len(value)}")
        for item in value:
            if isinstance(item, tuple):
                print("  - " + " | ".join(item))
            else:
                print(f"  - {item}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default=",".join(REPORT_FIELDS),
        help="Comma-separated report fields to evaluate.",
    )
    args = parser.parse_args(argv)
    fields = tuple(part.strip() for part in args.only.split(",") if part.strip())
    unknown = sorted(set(fields).difference(REPORT_FIELDS))
    if unknown:
        parser.error(f"unknown report fields: {', '.join(unknown)}")
    report = inspect_repository(ROOT)
    _print_report(report, fields)
    return 0 if not any(getattr(report, field) for field in fields) else 1


if __name__ == "__main__":
    sys.exit(main())
