"""Ignore-aware filesystem discovery for coding-agent resources."""

from __future__ import annotations

import fnmatch
from pathlib import Path

_IGNORE_FILES = (".gitignore", ".ignore", ".fdignore")
_ALWAYS_IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


def collect_resource_files(path: Path, resource_type: str) -> list[Path]:
    """Collect resource files, honoring ignore files for directory roots."""

    if not path.exists():
        return []
    if path.is_file():
        return [path.resolve()] if _is_resource_file(path, resource_type) else []

    if resource_type == "extensions":
        for entry_name in ("__init__.py", "index.py"):
            entry = path / entry_name
            if entry.is_file():
                return [entry.resolve()]
    if resource_type == "skills":
        skill_file = path / "SKILL.md"
        if skill_file.is_file():
            return [skill_file.resolve()]

    patterns = _read_patterns(path)
    discovered: list[Path] = []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = child.relative_to(path).as_posix()
            if child.is_dir():
                if child.name in _ALWAYS_IGNORED_DIRS or _is_ignored(relative, True, patterns):
                    continue
                if resource_type == "skills" and (child / "SKILL.md").is_file():
                    skill_file = child / "SKILL.md"
                    if not _is_ignored(
                        skill_file.relative_to(path).as_posix(),
                        False,
                        patterns,
                    ):
                        discovered.append(skill_file.resolve())
                    continue
                visit(child)
                continue
            if child.name in _IGNORE_FILES or _is_ignored(relative, False, patterns):
                continue
            if _is_resource_file(child, resource_type):
                discovered.append(child.resolve())

    visit(path)
    return discovered


def _is_resource_file(path: Path, resource_type: str) -> bool:
    if resource_type == "extensions":
        return path.suffix == ".py"
    if resource_type in {"skills", "prompts"}:
        return path.suffix == ".md"
    if resource_type == "themes":
        return path.suffix == ".json"
    return False


def _read_patterns(root: Path) -> tuple[str, ...]:
    patterns: list[str] = []
    for filename in _IGNORE_FILES:
        ignore_file = root / filename
        if not ignore_file.is_file():
            continue
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        patterns.extend(
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
    return tuple(patterns)


def _is_ignored(relative: str, is_dir: bool, patterns: tuple[str, ...]) -> bool:
    ignored = False
    relative = relative.lstrip("./")
    for raw_pattern in patterns:
        negate = raw_pattern.startswith("!")
        pattern = raw_pattern[1:] if negate else raw_pattern
        directory_only = pattern.endswith("/")
        pattern = pattern.rstrip("/").lstrip("/")
        if not pattern or (directory_only and not is_dir):
            continue
        if "/" in pattern:
            matched = fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(
                relative,
                f"{pattern}/**",
            )
        else:
            matched = any(fnmatch.fnmatch(part, pattern) for part in relative.split("/"))
        if matched:
            ignored = not negate
    return ignored


__all__ = ["collect_resource_files"]
