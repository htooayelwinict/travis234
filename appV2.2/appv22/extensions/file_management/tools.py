from __future__ import annotations

import difflib
import fnmatch
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from appv22.extensions.file_management.schemas import (
    COPY_FILE_OUTPUT_SCHEMA,
    DELETE_FILE_OUTPUT_SCHEMA,
    EDIT_FILE_OUTPUT_SCHEMA,
    FIND_FILES_OUTPUT_SCHEMA,
    GREP_OUTPUT_SCHEMA,
    MKDIR_OUTPUT_SCHEMA,
    READ_MANY_OUTPUT_SCHEMA,
    READ_RANGE_OUTPUT_SCHEMA,
    MOVE_FILE_OUTPUT_SCHEMA,
    READ_FILE_OUTPUT_SCHEMA,
    REPO_SNAPSHOT_OUTPUT_SCHEMA,
    SEARCH_TEXT_OUTPUT_SCHEMA,
    TREE_OUTPUT_SCHEMA,
    WRITE_FILE_OUTPUT_SCHEMA,
)
from appv22.tools.definitions import ToolDefinition

_PROTECTED_PATH_PARTS = {".git", ".env", "secrets", "assets"}
_DEFAULT_OBSERVE_EXCLUDES = (
    ".git",
    ".hg",
    ".svn",
    ".appv22-ui",
    ".playwright-mcp",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "qdrant_db",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    ".coverage",
)
_SNAPSHOT_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_SNAPSHOT_MAX_FILES = 600
_SNAPSHOT_MAX_DIRECTORIES = 300
_SNAPSHOT_MAX_PREVIEWS = 80
_SNAPSHOT_PREVIEW_BYTES = 4096
_SNAPSHOT_PREVIEW_CHARS = 700
_FIND_MAX_RESULTS = 500
_SEARCH_MAX_MATCHES = 120
_READ_MANY_MAX_FILES = 12
_READ_MANY_MAX_BYTES_PER_FILE = 24_000
_READ_MANY_MAX_TOTAL_BYTES = 80_000
_TREE_MAX_ENTRIES = 500
_READ_RANGE_MAX_LINES = 240
_READ_RANGE_MAX_CHARS = 40_000


def register_file_management_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            "file_management.repo_snapshot",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "include_globs": {"type": "array", "items": {"type": "string"}},
                    "max_files": {"type": "integer"},
                    "max_directories": {"type": "integer"},
                },
            },
            REPO_SNAPSHOT_OUTPUT_SCHEMA,
            "runtime_observed",
            "List workspace files and directories under an optional relative path. Excludes dependency/build caches by default and returns clipped text previews for useful text/code files.",
            freshness="turn",
            invalidated_by_mutation=True,
        ),
        repo_snapshot,
    )
    registry.register(
        ToolDefinition(
            "file_management.find_files",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "patterns": {"type": "array", "items": {"type": "string"}},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer"},
                },
            },
            FIND_FILES_OUTPUT_SCHEMA,
            "runtime_observed",
            "Find workspace files by glob/name patterns under an optional relative path. Use before reading many files in a code scan.",
            freshness="turn",
            invalidated_by_mutation=True,
        ),
        find_files,
    )
    registry.register(
        ToolDefinition(
            "file_management.search_text",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "max_matches": {"type": "integer"},
                },
                "required": ["query"],
            },
            SEARCH_TEXT_OUTPUT_SCHEMA,
            "runtime_observed",
            "Search text/code files by literal query under an optional relative path. Returns bounded path/line/snippet matches.",
            freshness="turn",
            invalidated_by_mutation=True,
        ),
        search_text,
    )
    registry.register(
        ToolDefinition(
            "file_management.read_many",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "max_bytes_per_file": {"type": "integer"},
                    "max_total_bytes": {"type": "integer"},
                },
                "required": ["paths"],
            },
            READ_MANY_OUTPUT_SCHEMA,
            "runtime_observed",
            "Read several exact workspace text files with strict per-file and total byte limits. Use after find_files/repo_snapshot selects important files.",
            invalidated_by_mutation=True,
        ),
        read_many,
    )
    registry.register(
        ToolDefinition(
            "file_management.tree",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "max_entries": {"type": "integer"},
                    "max_depth": {"type": "integer"},
                },
            },
            TREE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Return a compact directory tree under an optional relative path. Use before grep/read_range to understand code layout without flooding context.",
            freshness="turn",
            invalidated_by_mutation=True,
        ),
        tree,
    )
    registry.register(
        ToolDefinition(
            "file_management.grep",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "max_matches": {"type": "integer"},
                    "regex": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
            GREP_OUTPUT_SCHEMA,
            "runtime_observed",
            "Search text/code files by literal or regex pattern. Returns bounded path/line/snippet matches for code navigation.",
            freshness="turn",
            invalidated_by_mutation=True,
        ),
        grep,
    )
    registry.register(
        ToolDefinition(
            "file_management.read_range",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path", "start_line", "end_line"],
            },
            READ_RANGE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Read an exact bounded line range from one workspace text file. Prefer after grep/tree to inspect only relevant code slices.",
            invalidated_by_mutation=True,
        ),
        read_range,
    )
    registry.register(
        ToolDefinition(
            "file_management.read_file",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            READ_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Read exact text content from one workspace file by relative path.",
            invalidated_by_mutation=True,
        ),
        read_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.write_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
            WRITE_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Write complete text content to one workspace file by relative path. Creates parent directories.",
        ),
        write_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.edit_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {"type": "string"},
                                "newText": {"type": "string"},
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["path", "edits"],
            },
            EDIT_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Apply exact targeted replacements to one existing workspace text file. Use for existing-file edits after reading current content; old text must match exactly once.",
        ),
        edit_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.mkdir",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            MKDIR_OUTPUT_SCHEMA,
            "runtime_observed",
            "Create one workspace directory by relative path.",
        ),
        mkdir,
    )
    registry.register(
        ToolDefinition(
            "file_management.move_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["source", "destination"],
            },
            MOVE_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Move one workspace file from source to destination. Creates parent directories.",
        ),
        move_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.copy_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                    "preserve_source": {"type": "boolean"},
                },
                "required": ["source", "destination"],
            },
            COPY_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Copy one workspace file from source to destination. Creates parent directories.",
        ),
        copy_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.delete_file",
            "act",
            "high",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            DELETE_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Delete one workspace file by relative path.",
        ),
        delete_file,
    )


def repo_snapshot(_args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    start_result = _observe_start(root, str(_args.get("path") or ""))
    if start_result.get("status") != "completed":
        return {
            "status": start_result["status"],
            "root": start_result.get("path", ""),
            "files": [],
            "directories": [],
            "text_previews": {},
            "errors": start_result.get("errors", []),
        }
    start = start_result["absolute_path"]
    scan_root = start_result["path"]
    exclude_patterns = _observe_excludes(_args)
    include_globs = _string_list(_args.get("include_globs"))
    max_files = _positive_int(_args.get("max_files"), _SNAPSHOT_MAX_FILES, maximum=_SNAPSHOT_MAX_FILES)
    max_directories = _positive_int(_args.get("max_directories"), _SNAPSHOT_MAX_DIRECTORIES, maximum=_SNAPSHOT_MAX_DIRECTORIES)
    files: list[str] = []
    directories: list[str] = []
    text_previews: dict[str, str] = {}
    errors: list[str] = []
    for path, relative in _iter_workspace_paths(root, start, exclude_patterns=exclude_patterns):
        if include_globs and path.is_file() and not _matches_any(relative, include_globs):
            continue
        try:
            is_file = path.is_file()
            is_dir = path.is_dir()
        except OSError:
            errors.append(f"snapshot_stat_error:{relative}")
            continue
        if is_file:
            if len(files) >= max_files:
                errors.append("snapshot_file_limit_reached")
                continue
            files.append(relative)
            preview = _safe_text_preview(relative, path, root=root)
            if preview is not None and len(text_previews) < _SNAPSHOT_MAX_PREVIEWS:
                text_previews[relative] = preview
        elif is_dir:
            if len(directories) >= max_directories:
                errors.append("snapshot_directory_limit_reached")
                continue
            directories.append(relative)
    return {
        "status": "completed",
        "root": scan_root,
        "files": sorted(files),
        "directories": sorted(directories),
        "text_previews": dict(sorted(text_previews.items())),
        "errors": sorted(set(errors)),
    }


def find_files(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    start_result = _observe_start(root, str(args.get("path") or ""))
    if start_result.get("status") != "completed":
        return {
            "status": start_result["status"],
            "root": start_result.get("path", ""),
            "matches": [],
            "errors": start_result.get("errors", []),
        }
    patterns = _string_list(args.get("patterns")) or ["*"]
    exclude_patterns = _observe_excludes(args)
    max_results = _positive_int(args.get("max_results"), _FIND_MAX_RESULTS, maximum=_FIND_MAX_RESULTS)
    matches: list[str] = []
    errors: list[str] = []
    for path, relative in _iter_workspace_paths(root, start_result["absolute_path"], exclude_patterns=exclude_patterns):
        if not path.is_file():
            continue
        if _matches_any(relative, patterns) or _matches_any(Path(relative).name, patterns):
            matches.append(relative)
            if len(matches) >= max_results:
                errors.append("find_files_limit_reached")
                break
    return {
        "status": "completed",
        "root": start_result["path"],
        "matches": sorted(matches),
        "errors": sorted(set(errors)),
    }


def search_text(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    query = str(args.get("query") or "")
    if not query:
        return {"status": "failed", "root": "", "matches": [], "errors": ["missing_query"]}
    start_result = _observe_start(root, str(args.get("path") or ""))
    if start_result.get("status") != "completed":
        return {
            "status": start_result["status"],
            "root": start_result.get("path", ""),
            "matches": [],
            "errors": start_result.get("errors", []),
        }
    glob = str(args.get("glob") or "*")
    exclude_patterns = _observe_excludes(args)
    max_matches = _positive_int(args.get("max_matches"), _SEARCH_MAX_MATCHES, maximum=_SEARCH_MAX_MATCHES)
    lowered_query = query.lower()
    matches: list[dict] = []
    errors: list[str] = []
    for path, relative in _iter_workspace_paths(root, start_result["absolute_path"], exclude_patterns=exclude_patterns):
        if not path.is_file() or not _matches_any(relative, (glob,)):
            continue
        if not _safe_text_file(path, max_bytes=1_000_000):
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if lowered_query in line.lower():
                        matches.append(
                            {
                                "path": relative,
                                "line": line_number,
                                "snippet": line.strip()[:300],
                            }
                        )
                        if len(matches) >= max_matches:
                            errors.append("search_text_limit_reached")
                            return {
                                "status": "completed",
                                "root": start_result["path"],
                                "matches": matches,
                                "errors": sorted(set(errors)),
                            }
        except OSError:
            errors.append(f"search_read_error:{relative}")
    return {
        "status": "completed",
        "root": start_result["path"],
        "matches": matches,
        "errors": sorted(set(errors)),
    }


def read_many(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    paths = _string_list(args.get("paths"))[:_READ_MANY_MAX_FILES]
    max_bytes_per_file = _positive_int(
        args.get("max_bytes_per_file"),
        _READ_MANY_MAX_BYTES_PER_FILE,
        maximum=_READ_MANY_MAX_BYTES_PER_FILE,
    )
    max_total_bytes = _positive_int(
        args.get("max_total_bytes"),
        _READ_MANY_MAX_TOTAL_BYTES,
        maximum=_READ_MANY_MAX_TOTAL_BYTES,
    )
    files: list[dict] = []
    errors: list[str] = []
    total_bytes = 0
    if len(_string_list(args.get("paths"))) > len(paths):
        errors.append("read_many_file_limit_reached")
    for requested_path in paths:
        canonical_relative = _canonical_relative_path(root, requested_path)
        if canonical_relative is None:
            errors.append(f"path_outside_root:{requested_path}")
            continue
        if _protected_read(canonical_relative):
            errors.append(f"protected_path:{canonical_relative}")
            continue
        path = root / canonical_relative
        if not path.is_file():
            errors.append(f"missing_file:{canonical_relative}")
            continue
        if path.suffix.lower() not in _SNAPSHOT_TEXT_SUFFIXES:
            errors.append(f"unsupported_text_file:{canonical_relative}")
            continue
        try:
            with path.open("rb") as handle:
                raw = handle.read(max_bytes_per_file + 1)
        except OSError:
            errors.append(f"read_error:{canonical_relative}")
            continue
        truncated = len(raw) > max_bytes_per_file
        clipped = raw[:max_bytes_per_file]
        if total_bytes + len(clipped) > max_total_bytes:
            errors.append("read_many_total_bytes_limit_reached")
            break
        try:
            content = clipped.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"decode_error:{canonical_relative}")
            continue
        total_bytes += len(clipped)
        files.append(
            {
                "path": canonical_relative,
                "content": content,
                "bytes_read": len(clipped),
                "line_count": _line_count(content),
                "truncated": truncated,
            }
        )
    return {"status": "completed", "files": files, "errors": sorted(set(errors))}


def tree(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    start_result = _observe_start(root, str(args.get("path") or ""))
    if start_result.get("status") != "completed":
        return {
            "status": start_result["status"],
            "root": start_result.get("path", ""),
            "entries": [],
            "errors": start_result.get("errors", []),
        }
    exclude_patterns = _observe_excludes(args)
    max_entries = _positive_int(args.get("max_entries"), _TREE_MAX_ENTRIES, maximum=_TREE_MAX_ENTRIES)
    max_depth = _positive_int(args.get("max_depth"), 8, maximum=20)
    base_depth = len(start_result["absolute_path"].relative_to(root).parts)
    entries: list[str] = []
    errors: list[str] = []
    for path, relative in _iter_workspace_paths(root, start_result["absolute_path"], exclude_patterns=exclude_patterns):
        depth = len(path.relative_to(root).parts) - base_depth
        if depth > max_depth:
            continue
        marker = "/" if path.is_dir() else ""
        indent = "  " * max(depth - 1, 0)
        entries.append(f"{indent}{Path(relative).name}{marker}")
        if len(entries) >= max_entries:
            errors.append("tree_entry_limit_reached")
            break
    return {
        "status": "completed",
        "root": start_result["path"],
        "entries": entries,
        "errors": sorted(set(errors)),
    }


def grep(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return {"status": "failed", "root": "", "matches": [], "errors": ["missing_pattern"]}
    start_result = _observe_start(root, str(args.get("path") or ""))
    if start_result.get("status") != "completed":
        return {
            "status": start_result["status"],
            "root": start_result.get("path", ""),
            "matches": [],
            "errors": start_result.get("errors", []),
        }
    glob = str(args.get("glob") or "*")
    exclude_patterns = _observe_excludes(args)
    max_matches = _positive_int(args.get("max_matches"), _SEARCH_MAX_MATCHES, maximum=_SEARCH_MAX_MATCHES)
    use_regex = bool(args.get("regex", False))
    compiled = None
    errors: list[str] = []
    if use_regex:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return {"status": "failed", "root": start_result["path"], "matches": [], "errors": [f"invalid_regex:{exc}"]}
    lowered_pattern = pattern.lower()
    matches: list[dict] = []
    for path, relative in _iter_workspace_paths(root, start_result["absolute_path"], exclude_patterns=exclude_patterns):
        if not path.is_file() or not _matches_any(relative, (glob,)):
            continue
        if not _safe_text_file(path, max_bytes=1_000_000):
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    matched = bool(compiled.search(line)) if compiled is not None else lowered_pattern in line.lower()
                    if not matched:
                        continue
                    matches.append({"path": relative, "line": line_number, "snippet": line.strip()[:300]})
                    if len(matches) >= max_matches:
                        errors.append("grep_match_limit_reached")
                        return {
                            "status": "completed",
                            "root": start_result["path"],
                            "matches": matches,
                            "errors": sorted(set(errors)),
                        }
        except OSError:
            errors.append(f"grep_read_error:{relative}")
    return {
        "status": "completed",
        "root": start_result["path"],
        "matches": matches,
        "errors": sorted(set(errors)),
    }


def read_range(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path") or "")
    start_line = _positive_int(args.get("start_line"), 1, maximum=1_000_000)
    end_line = _positive_int(args.get("end_line"), start_line, maximum=1_000_000)
    if end_line < start_line:
        return {
            "status": "failed",
            "path": relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": ["invalid_line_range"],
        }
    if end_line - start_line + 1 > _READ_RANGE_MAX_LINES:
        end_line = start_line + _READ_RANGE_MAX_LINES - 1
    canonical_relative = _canonical_relative_path(root, relative)
    if canonical_relative is None:
        return {
            "status": "denied",
            "path": relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"path_outside_root:{relative}"],
        }
    if _protected_read(canonical_relative):
        return {
            "status": "denied",
            "path": canonical_relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"protected_path:{canonical_relative}"],
        }
    path = root / canonical_relative
    if not path.is_file():
        return {
            "status": "failed",
            "path": canonical_relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"missing_file:{canonical_relative}"],
        }
    if path.suffix.lower() not in _SNAPSHOT_TEXT_SUFFIXES:
        return {
            "status": "failed",
            "path": canonical_relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"unsupported_text_file:{canonical_relative}"],
        }
    lines: list[str] = []
    errors: list[str] = []
    char_count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number < start_line:
                    continue
                if line_number > end_line:
                    break
                rendered = f"{line_number}: {line.rstrip()}"
                char_count += len(rendered) + 1
                if char_count > _READ_RANGE_MAX_CHARS:
                    errors.append("read_range_char_limit_reached")
                    break
                lines.append(rendered)
    except UnicodeDecodeError:
        return {
            "status": "failed",
            "path": canonical_relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"decode_error:{canonical_relative}"],
        }
    except OSError:
        return {
            "status": "failed",
            "path": canonical_relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"read_error:{canonical_relative}"],
        }
    if not lines:
        line_count = _file_line_count(path)
        return {
            "status": "failed",
            "path": canonical_relative,
            "start_line": start_line,
            "end_line": end_line,
            "content": "",
            "errors": [f"line_range_out_of_bounds:{canonical_relative}:{start_line}:{line_count}"],
        }
    return {
        "status": "completed",
        "path": canonical_relative,
        "start_line": start_line,
        "end_line": end_line,
        "content": "\n".join(lines),
        "errors": sorted(set(errors)),
    }


def _safe_text_preview(relative: str, path: Path, *, root: Path, max_chars: int = _SNAPSHOT_PREVIEW_CHARS) -> str | None:
    if _protected_path(relative) or path.is_symlink():
        return None
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    if not _safe_text_file(path, max_bytes=_SNAPSHOT_PREVIEW_BYTES):
        return None
    return _read_text_preview(path, max_chars=max_chars)


def _safe_text_file(path: Path, *, max_bytes: int) -> bool:
    if path.suffix.lower() not in _SNAPSHOT_TEXT_SUFFIXES:
        return False
    try:
        if path.stat().st_size > max_bytes:
            return False
    except OSError:
        return False
    return True


def _read_text_preview(path: Path, *, max_chars: int) -> str | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_SNAPSHOT_PREVIEW_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _SNAPSHOT_PREVIEW_BYTES:
        return None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return content[:max_chars]


def _file_line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _line in handle)
    except (OSError, UnicodeDecodeError):
        return 0


def _observe_start(root: Path, relative: str) -> dict:
    if _absolute(relative):
        return {"status": "denied", "path": relative, "errors": [f"absolute_path:path:{relative}"]}
    canonical_relative = _canonical_relative_path(root, relative or ".")
    if canonical_relative is None:
        return {"status": "denied", "path": relative, "errors": [f"path_outside_root:{relative}"]}
    if canonical_relative != "." and _protected_read(canonical_relative):
        return {"status": "denied", "path": canonical_relative, "errors": [f"protected_path:{canonical_relative}"]}
    absolute_path = root / canonical_relative
    if not absolute_path.exists():
        return {"status": "failed", "path": canonical_relative, "errors": [f"missing_path:{canonical_relative}"]}
    if not absolute_path.is_dir():
        return {"status": "failed", "path": canonical_relative, "errors": [f"observe_target_not_directory:{canonical_relative}"]}
    return {"status": "completed", "path": canonical_relative, "absolute_path": absolute_path, "errors": []}


def _observe_excludes(args: dict) -> tuple[str, ...]:
    return tuple(dict.fromkeys([*_DEFAULT_OBSERVE_EXCLUDES, *_string_list(args.get("exclude"))]))


def _string_list(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item)]


def _positive_int(value, default: int, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, maximum)


def _iter_workspace_paths(root: Path, start: Path, *, exclude_patterns: tuple[str, ...]):
    def walk(current: Path):
        relative_current = current.relative_to(root).as_posix()
        if relative_current == ".":
            relative_current = ""
        try:
            children = sorted(
                current.iterdir(),
                key=lambda child: (not child.is_dir(), child.name.lower(), child.name),
            )
        except OSError:
            return
        for candidate in children:
            relative = _join_relative(relative_current, candidate.name)
            if candidate.is_symlink() or _protected_path(relative) or _excluded(relative, exclude_patterns):
                continue
            yield candidate, relative
            if candidate.is_dir():
                yield from walk(candidate)

    yield from walk(start)


def _join_relative(parent: str, child: str) -> str:
    return child if not parent else f"{parent}/{child}"


def _excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    parts = [part for part in relative.split("/") if part]
    for pattern in patterns:
        normalized = pattern.strip().strip("/")
        if not normalized:
            continue
        if normalized in parts:
            return True
        if fnmatch.fnmatch(relative, normalized) or fnmatch.fnmatch(Path(relative).name, normalized):
            return True
    return False


def _matches_any(value: str, patterns) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def read_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    if _absolute(relative):
        return {"status": "denied", "path": relative, "content": "", "errors": [f"absolute_path:path:{relative}"]}
    canonical_relative = _canonical_relative_path(root, relative)
    if canonical_relative is None:
        return {"status": "denied", "path": relative, "content": "", "errors": [f"path_outside_root:{relative}"]}
    if _protected_read(canonical_relative):
        return {
            "status": "denied",
            "path": canonical_relative,
            "content": "",
            "errors": [f"protected_path:{canonical_relative}"],
        }
    path = root / canonical_relative
    if not path.is_file():
        return {
            "status": "failed",
            "path": canonical_relative,
            "content": "",
            "errors": [f"missing_file:{canonical_relative}"],
        }
    content = path.read_text(encoding="utf-8")
    return {
        "status": "completed",
        "path": canonical_relative,
        "content": content,
        "line_count": _line_count(content),
    }


def _line_count(content: str) -> int:
    return len(content.splitlines())


def write_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))
    if _request_forbids_overwrite(context):
        overwrite = False
    if not isinstance(content, str):
        content = str(content)
    obsolete_error = _obsolete_identifier_error(content)
    if obsolete_error:
        cleaned_content = _remove_obsolete_identifier_lines(content)
        if cleaned_content != content and not _obsolete_identifier_error(cleaned_content):
            content = cleaned_content
        else:
            return {
                "status": "denied",
                "path": relative,
                "bytes_written": 0,
                "overwritten": False,
                "errors": [obsolete_error],
            }
    if _absolute(relative):
        return {
            "status": "denied",
            "path": relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"absolute_path:path:{relative}"],
        }
    canonical_relative = _canonical_relative_path(root, relative)
    if canonical_relative is None:
        return {
            "status": "denied",
            "path": relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"path_outside_root:{relative}"],
        }
    if _protected_mutation(canonical_relative):
        return {
            "status": "denied",
            "path": canonical_relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"protected_path:{canonical_relative}"],
        }
    path = root / canonical_relative
    if path.exists() and path.is_dir():
        return {
            "status": "failed",
            "path": canonical_relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"write_target_is_directory:{canonical_relative}"],
        }
    if path.exists() and not overwrite:
        suggested_path = _available_sibling_path(root, canonical_relative)
        return {
            "status": "denied",
            "path": canonical_relative,
            "bytes_written": 0,
            "overwritten": False,
            "suggested_path": suggested_path,
            "errors": [f"existing_file_requires_overwrite:{canonical_relative}"],
        }
    overwritten = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "status": "completed",
        "path": canonical_relative,
        "bytes_written": len(content.encode("utf-8")),
        "overwritten": overwritten,
        "errors": [],
    }


def edit_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    denied = _validate_single_mutation_path(root, relative)
    if denied:
        return _edit_file_result("denied", relative, 0, [denied])
    canonical_relative = _canonical_relative_path(root, relative)
    assert canonical_relative is not None
    path = root / canonical_relative
    if not path.exists():
        return _edit_file_result("failed", canonical_relative, 0, [f"missing_file:{canonical_relative}"])
    if not path.is_file():
        return _edit_file_result("denied", canonical_relative, 0, [f"edit_target_not_file:{canonical_relative}"])
    if not _safe_text_file(path, max_bytes=1_000_000):
        return _edit_file_result("failed", canonical_relative, 0, [f"unsupported_text_file:{canonical_relative}"])
    normalized_edits, errors = _normalize_edits(args.get("edits"))
    if errors:
        return _edit_file_result("denied", canonical_relative, 0, errors)
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _edit_file_result("failed", canonical_relative, 0, [f"decode_error:{canonical_relative}"])
    except OSError:
        return _edit_file_result("failed", canonical_relative, 0, [f"read_error:{canonical_relative}"])

    spans: list[tuple[int, int]] = []
    for index, edit in enumerate(normalized_edits):
        old_text = edit["old_text"]
        matches = [match.start() for match in re.finditer(re.escape(old_text), original)]
        if not matches:
            return _edit_file_result("denied", canonical_relative, 0, [f"old_text_not_found:{index}"])
        if len(matches) > 1:
            return _edit_file_result("denied", canonical_relative, 0, [f"old_text_not_unique:{index}"])
        spans.append((matches[0], matches[0] + len(old_text)))
    if _overlapping_spans(spans):
        return _edit_file_result("denied", canonical_relative, 0, ["overlapping_edits"])

    updated = original
    for edit in normalized_edits:
        updated = updated.replace(edit["old_text"], edit["new_text"], 1)
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return _edit_file_result("failed", canonical_relative, 0, [f"write_error:{canonical_relative}"])

    return {
        "status": "completed",
        "path": canonical_relative,
        "edits_applied": len(normalized_edits),
        "bytes_written": len(updated.encode("utf-8")),
        "first_changed_line": _first_changed_line(original, updated),
        "diff": _unified_text_diff(canonical_relative, original, updated),
        "errors": [],
    }


def mkdir(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    denied = _validate_single_mutation_path(root, relative)
    if denied:
        return {"status": "denied", "path": relative, "created": False, "errors": [denied]}
    canonical_relative = _canonical_relative_path(root, relative)
    assert canonical_relative is not None
    path = root / canonical_relative
    if path.exists() and not path.is_dir():
        return {"status": "failed", "path": canonical_relative, "created": False, "errors": [f"path_is_file:{canonical_relative}"]}
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    return {"status": "completed", "path": canonical_relative, "created": created, "errors": []}


def move_file(args: dict, context: dict) -> dict:
    return _copy_or_move_file(args, context, operation="move")


def copy_file(args: dict, context: dict) -> dict:
    if args.get("preserve_source") is not True:
        source = str(args.get("source", ""))
        destination = str(args.get("destination", ""))
        return _file_transfer_result(
            "denied",
            source,
            destination,
            False,
            ["copy_requires_preserve_source:true"],
        )
    return _copy_or_move_file(args, context, operation="copy")


def delete_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    denied = _validate_single_mutation_path(root, relative)
    if denied:
        return {"status": "denied", "path": relative, "deleted": False, "errors": [denied]}
    canonical_relative = _canonical_relative_path(root, relative)
    assert canonical_relative is not None
    path = root / canonical_relative
    if not path.exists():
        return {"status": "failed", "path": canonical_relative, "deleted": False, "errors": [f"missing_path:{canonical_relative}"]}
    if not path.is_file():
        return {"status": "denied", "path": canonical_relative, "deleted": False, "errors": [f"delete_target_not_file:{canonical_relative}"]}
    path.unlink()
    return {"status": "completed", "path": canonical_relative, "deleted": True, "errors": []}


def _copy_or_move_file(args: dict, context: dict, *, operation: str) -> dict:
    root = Path(context["root_path"]).resolve()
    source = str(args.get("source", ""))
    destination = str(args.get("destination", ""))
    overwrite = bool(args.get("overwrite", False))
    if _request_forbids_overwrite(context):
        overwrite = False
    source_error = _validate_single_mutation_path(root, source)
    if source_error:
        return _file_transfer_result("denied", source, destination, False, [f"source:{source_error}"])
    destination_error = _validate_single_mutation_path(root, destination)
    if destination_error:
        return _file_transfer_result("denied", source, destination, False, [f"destination:{destination_error}"])
    canonical_source = _canonical_relative_path(root, source)
    canonical_destination = _canonical_relative_path(root, destination)
    assert canonical_source is not None
    assert canonical_destination is not None
    source_path = root / canonical_source
    destination_path = root / canonical_destination
    if not source_path.exists():
        return _file_transfer_result("failed", canonical_source, canonical_destination, False, [f"missing_source:{canonical_source}"])
    if not source_path.is_file():
        return _file_transfer_result("denied", canonical_source, canonical_destination, False, [f"source_not_file:{canonical_source}"])
    if destination_path.exists() and destination_path.is_dir():
        return _file_transfer_result("failed", canonical_source, canonical_destination, False, [f"destination_is_directory:{canonical_destination}"])
    if destination_path.exists() and not overwrite:
        result = _file_transfer_result(
            "denied",
            canonical_source,
            canonical_destination,
            False,
            [f"existing_file_requires_overwrite:{canonical_destination}"],
        )
        result["suggested_path"] = _available_sibling_path(root, canonical_destination)
        return result
    overwritten = destination_path.exists()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if operation == "move":
        shutil.move(str(source_path), str(destination_path))
    else:
        shutil.copy2(source_path, destination_path)
    return _file_transfer_result("completed", canonical_source, canonical_destination, overwritten, [])


def _file_transfer_result(status: str, source: str, destination: str, overwritten: bool, errors: list[str]) -> dict:
    return {
        "status": status,
        "source": source,
        "destination": destination,
        "overwritten": overwritten,
        "errors": errors,
    }


def _edit_file_result(status: str, path: str, edits_applied: int, errors: list[str]) -> dict:
    return {
        "status": status,
        "path": path,
        "edits_applied": edits_applied,
        "bytes_written": 0,
        "first_changed_line": 0,
        "diff": "",
        "errors": errors,
    }


def _normalize_edits(value) -> tuple[list[dict[str, str]], list[str]]:
    if not isinstance(value, list) or not value:
        return [], ["missing_edits"]
    normalized: list[dict[str, str]] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"invalid_edit:{index}")
            continue
        old_text = item.get("oldText", item.get("old_text"))
        new_text = item.get("newText", item.get("new_text"))
        if not isinstance(old_text, str) or old_text == "":
            errors.append(f"missing_old_text:{index}")
            continue
        if not isinstance(new_text, str):
            errors.append(f"missing_new_text:{index}")
            continue
        normalized.append({"old_text": old_text, "new_text": new_text})
    return normalized, errors


def _overlapping_spans(spans: list[tuple[int, int]]) -> bool:
    ordered = sorted(spans)
    return any(current_start < previous_end for (_, previous_end), (current_start, _) in zip(ordered, ordered[1:]))


def _first_changed_line(original: str, updated: str) -> int:
    original_lines = original.splitlines()
    updated_lines = updated.splitlines()
    for index, (old_line, new_line) in enumerate(zip(original_lines, updated_lines), start=1):
        if old_line != new_line:
            return index
    return min(len(original_lines), len(updated_lines)) + 1 if original != updated else 0


def _unified_text_diff(path: str, original: str, updated: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _protected_read(path: str) -> bool:
    return _protected_path(path)


def _protected_mutation(path: str) -> bool:
    return _protected_path(path)


def _protected_path(path: str) -> bool:
    parts = [part.lower() for part in path.replace("\\", "/").lstrip("/").split("/") if part]
    return any(part in _PROTECTED_PATH_PARTS for part in parts)


def _validate_single_mutation_path(root: Path, path: str) -> str:
    if _absolute(path):
        return f"absolute_path:path:{path}"
    canonical_relative = _canonical_relative_path(root, path)
    if canonical_relative is None:
        return f"path_outside_root:{path}"
    if _protected_mutation(canonical_relative):
        return f"protected_path:{canonical_relative}"
    return ""


def _absolute(path: str) -> bool:
    return Path(path).is_absolute()


def _canonical_relative_path(root: Path, path: str) -> str | None:
    if not path or "\x00" in path:
        return None
    candidate = (root / path).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


def _available_sibling_path(root: Path, relative: str) -> str:
    path = Path(relative)
    parent = path.parent.as_posix()
    stem = path.stem or "file"
    suffix = path.suffix
    for index in range(1, 100):
        candidate_name = f"{stem}-{index}{suffix}"
        candidate = candidate_name if parent == "." else f"{parent}/{candidate_name}"
        if not (root / candidate).exists():
            return candidate
    return f"{parent}/{stem}-{uuid4().hex[:8]}{suffix}" if parent != "." else f"{stem}-{uuid4().hex[:8]}{suffix}"


def _request_forbids_overwrite(context: dict) -> bool:
    request = context.get("request") if isinstance(context.get("request"), dict) else {}
    goal = str(request.get("active_user_request") or request.get("user_goal", "")).lower()
    return any(
        marker in goal
        for marker in (
            "do not overwrite",
            "don't overwrite",
            "dont overwrite",
            "no overwrite",
            "without overwriting",
            "not overwrite",
        )
    )


def _obsolete_identifier_error(content: str) -> str:
    risky_lines = _obsolete_identifier_risky_lines(content)
    if not risky_lines:
        return ""
    code_like_identifiers = re.findall(
        r"\b[A-Z][A-Z0-9]+-[0-9]{2,}-[A-Z0-9]+\b",
        "\n".join(risky_lines),
    )
    if not code_like_identifiers:
        return ""
    unique_identifiers = sorted(set(code_like_identifiers))
    return "obsolete_identifier_leak:" + ",".join(unique_identifiers[:8])


def _remove_obsolete_identifier_lines(content: str) -> str:
    kept_lines: list[str] = []
    in_obsolete_section = False
    for line in content.splitlines():
        marker_line = _has_obsolete_marker(line)
        if marker_line:
            in_obsolete_section = True
        elif line.startswith("#"):
            in_obsolete_section = False
        elif not line.strip():
            in_obsolete_section = False
        has_identifier = re.search(r"\b[A-Z][A-Z0-9]+-[0-9]{2,}-[A-Z0-9]+\b", line)
        if has_identifier and (marker_line or in_obsolete_section):
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    return f"{cleaned}\n" if cleaned else content


def _obsolete_identifier_risky_lines(content: str) -> list[str]:
    risky_lines: list[str] = []
    in_obsolete_section = False
    for line in content.splitlines():
        if _has_obsolete_marker(line):
            in_obsolete_section = True
            risky_lines.append(line)
            continue
        if line.startswith("#") or not line.strip():
            in_obsolete_section = False
            continue
        if in_obsolete_section:
            risky_lines.append(line)
    return risky_lines


def _has_obsolete_marker(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in ("obsolete", "do not use", "excluded", "fake", "stale"))
