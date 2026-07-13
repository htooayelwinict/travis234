"""Path helpers. Port of pi tools/path-utils.ts (subset)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase

_UNICODE_SPACES = re.compile(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]")
_NARROW_NO_BREAK_SPACE = "\u202f"


@dataclass(frozen=True)
class GitIgnoreRule:
    base_path: str
    pattern: str
    negated: bool = False
    directory_only: bool = False


def _try_macos_screenshot_path(file_path: str) -> str:
    return re.sub(r" (AM|PM)\.", rf"{_NARROW_NO_BREAK_SPACE}\1.", file_path, flags=re.IGNORECASE)


def _try_curly_quote_variant(file_path: str) -> str:
    return file_path.replace("'", "\u2019")


def _file_exists(file_path: str) -> bool:
    return os.path.exists(file_path)


def _normalize_unicode_nfd(file_path: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFD", file_path)


def _to_posix_path(path: str) -> str:
    return path.replace(os.sep, "/")


def expand_path(path: str) -> str:
    normalized = _UNICODE_SPACES.sub(" ", path)
    if normalized.startswith("@"):
        normalized = normalized[1:]
    if normalized == "~" or normalized.startswith("~/"):
        return os.path.expanduser(normalized)
    return normalized


def resolve_to_cwd(path: str, cwd: str) -> str:
    expanded = expand_path(path)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(expand_path(cwd), expanded))


def resolve_read_path(path: str, cwd: str) -> str:
    resolved = resolve_to_cwd(path, cwd)
    if _file_exists(resolved):
        return resolved

    am_pm_variant = _try_macos_screenshot_path(resolved)
    if am_pm_variant != resolved and _file_exists(am_pm_variant):
        return am_pm_variant

    nfd_variant = _normalize_unicode_nfd(resolved)
    if nfd_variant != resolved and _file_exists(nfd_variant):
        return nfd_variant

    curly_variant = _try_curly_quote_variant(resolved)
    if curly_variant != resolved and _file_exists(curly_variant):
        return curly_variant

    nfd_curly_variant = _try_curly_quote_variant(nfd_variant)
    if nfd_curly_variant != resolved and _file_exists(nfd_curly_variant):
        return nfd_curly_variant

    return resolved


def load_gitignore_rules(root: str, dirpath: str, cache: dict[str, list[GitIgnoreRule]]) -> list[GitIgnoreRule]:
    root = os.path.abspath(root)
    dirpath = os.path.abspath(dirpath)
    try:
        relative_dir = os.path.relpath(dirpath, root)
    except ValueError:
        return []

    directories = [root]
    if relative_dir != "." and not relative_dir.startswith(".."):
        current = root
        for part in relative_dir.split(os.sep):
            current = os.path.join(current, part)
            directories.append(current)

    rules: list[GitIgnoreRule] = []
    for directory in directories:
        if directory not in cache:
            cache[directory] = _read_gitignore_rules(directory)
        rules.extend(cache[directory])
    return rules


def _read_gitignore_rules(directory: str) -> list[GitIgnoreRule]:
    ignore_path = os.path.join(directory, ".gitignore")
    try:
        with open(ignore_path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []

    rules: list[GitIgnoreRule] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        directory_only = line.endswith("/")
        line = line.strip("/")
        if line:
            rules.append(GitIgnoreRule(base_path=directory, pattern=line, negated=negated, directory_only=directory_only))
    return rules


def is_ignored_by_gitignore(path: str, is_directory: bool, rules: list[GitIgnoreRule]) -> bool:
    ignored = False
    for rule in rules:
        try:
            relative = os.path.relpath(path, rule.base_path)
        except ValueError:
            continue
        if relative == "." or relative == ".." or relative.startswith(".." + os.sep):
            continue
        relative = _to_posix_path(relative)
        if _gitignore_rule_matches(rule, relative, os.path.basename(path), is_directory):
            ignored = not rule.negated
    return ignored


def _gitignore_rule_matches(rule: GitIgnoreRule, relative_path: str, name: str, is_directory: bool) -> bool:
    if rule.directory_only and not is_directory:
        return False
    pattern = rule.pattern.replace("\\", "/")
    if "/" not in pattern:
        return fnmatchcase(name, pattern) or fnmatchcase(relative_path, f"**/{pattern}")
    if fnmatchcase(relative_path, pattern):
        return True
    if not pattern.startswith("**/") and fnmatchcase(relative_path, f"**/{pattern}"):
        return True
    if pattern.startswith("**/") and fnmatchcase(relative_path, pattern[3:]):
        return True
    return False


def format_path_relative_to_cwd(path: str, cwd: str) -> str:
    try:
        rel = os.path.relpath(path, cwd)
    except ValueError:
        return path
    if rel == "." or (rel != ".." and not rel.startswith(".." + os.sep) and not os.path.isabs(rel)):
        return rel.replace(os.sep, "/")
    return path


def shorten_path(path: str) -> str:
    home = os.path.expanduser("~")
    if home and path.startswith(home):
        return "~" + path[len(home) :]
    return path


def render_tool_path(path: str | None, cwd: str, *, empty_fallback: str | None = None) -> str:
    if path is None:
        return ""
    value = path or empty_fallback or ""
    if not value:
        return "..."
    if cwd:
        resolved = resolve_to_cwd(value, cwd)
        display = format_path_relative_to_cwd(resolved, cwd)
    else:
        display = expand_path(value)
    return shorten_path(display)
