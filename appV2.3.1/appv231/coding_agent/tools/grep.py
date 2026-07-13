"""grep tool. Port of pi/packages/coding-agent/src/core/tools/grep.ts."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from appv231.agent.types import AgentTool, AgentToolResult
from appv231.ai.types import TextContent
from appv231.coding_agent.capabilities import WorkspaceCapability
from appv231.coding_agent.tools.path_utils import is_ignored_by_gitignore, load_gitignore_rules, resolve_to_cwd
from appv231.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
    truncation_to_details,
)
from appv231.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

GREP_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Search pattern (regex or literal string)"},
        "path": {"type": "string", "description": "Directory or file to search (default: current directory)"},
        "glob": {"type": "string", "description": "Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'"},
        "ignoreCase": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
        "literal": {"type": "boolean", "description": "Treat pattern as literal string instead of regex (default: false)"},
        "context": {"type": "number", "description": "Number of lines to show before and after each match (default: 0)"},
        "limit": {"type": "number", "description": "Maximum number of matches to return (default: 100)"},
    },
    "required": ["pattern"],
}

DEFAULT_LIMIT = 100
_EXCLUDED_DIRS = {".git", "node_modules"}


@dataclass(frozen=True)
class GrepOperations:
    is_directory: Callable[[str], bool]
    read_file: Callable[[str], str]


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


_DEFAULT_OPERATIONS = GrepOperations(is_directory=os.path.isdir, read_file=_read_file)


def _check_aborted(signal) -> None:
    if signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")


def _to_posix_path(path: str) -> str:
    return path.replace(os.sep, "/")


def _glob_matches(glob_pattern: str | None, relative_path: str, filename: str) -> bool:
    if not glob_pattern:
        return True
    pattern = glob_pattern.replace("\\", "/")
    if "/" not in pattern:
        return re.fullmatch(fnmatch_to_regex(pattern), filename) is not None

    patterns = [pattern]
    if not pattern.startswith("/") and not pattern.startswith("**/") and pattern != "**":
        patterns.append(f"**/{pattern}")
    for candidate in patterns:
        regex = fnmatch_to_regex(candidate)
        if re.fullmatch(regex, relative_path):
            return True
        if candidate.startswith("**/") and re.fullmatch(fnmatch_to_regex(candidate[3:]), relative_path):
            return True
    return False


def fnmatch_to_regex(pattern: str) -> str:
    import fnmatch

    return fnmatch.translate(pattern)


def _iter_files(search_path: str, is_directory: bool, glob_pattern: str | None) -> list[str]:
    if not is_directory:
        return [search_path] if _glob_matches(glob_pattern, os.path.basename(search_path), os.path.basename(search_path)) else []

    files: list[str] = []
    ignore_cache: dict[str, list] = {}
    for dirpath, dirnames, filenames in os.walk(search_path):
        rules = load_gitignore_rules(search_path, dirpath, ignore_cache)
        dirnames[:] = sorted(
            (
                d
                for d in dirnames
                if d not in _EXCLUDED_DIRS
                and not is_ignored_by_gitignore(os.path.join(dirpath, d), True, rules)
            ),
            key=str.lower,
        )
        for filename in sorted(filenames, key=str.lower):
            file_path = os.path.join(dirpath, filename)
            if is_ignored_by_gitignore(file_path, False, rules):
                continue
            relative_path = _to_posix_path(os.path.relpath(file_path, search_path))
            if _glob_matches(glob_pattern, relative_path, filename):
                files.append(file_path)
    return sorted(files, key=lambda path: _to_posix_path(os.path.relpath(path, search_path)).lower())


def _line_matches(line: str, pattern: str, regex: re.Pattern[str] | None, literal: bool, ignore_case: bool) -> bool:
    if literal:
        if ignore_case:
            return pattern.lower() in line.lower()
        return pattern in line
    return regex.search(line) is not None if regex else False


def _execute_grep(
    cwd: str,
    workspace: WorkspaceCapability,
    operations: GrepOperations,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
):
    _check_aborted(signal)
    search_path = str(workspace.resolve(args.get("path") or ".", access="read"))
    if not os.path.exists(search_path):
        raise FileNotFoundError(f"Path not found: {search_path}")

    is_directory = operations.is_directory(search_path)
    context_value = max(0, int(args.get("context") or 0))
    limit = max(1, int(args.get("limit", args.get("max_results", DEFAULT_LIMIT))))
    pattern = args["pattern"]
    ignore_case = bool(args.get("ignoreCase"))
    literal = bool(args.get("literal"))
    regex = None if literal else re.compile(pattern, re.IGNORECASE if ignore_case else 0)

    def format_path(file_path: str) -> str:
        if is_directory:
            relative = os.path.relpath(file_path, search_path)
            if relative and not relative.startswith(".."):
                return _to_posix_path(relative)
        return os.path.basename(file_path)

    files = _iter_files(search_path, is_directory, args.get("glob"))
    output_lines: list[str] = []
    match_count = 0
    match_limit_reached = False
    lines_truncated = False

    for file_path in files:
        _check_aborted(signal)
        try:
            content = operations.read_file(file_path)
        except OSError:
            continue
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, line in enumerate(lines, start=1):
            _check_aborted(signal)
            if not _line_matches(line, pattern, regex, literal, ignore_case):
                continue
            match_count += 1
            relative_path = format_path(file_path)
            start = max(1, index - context_value) if context_value > 0 else index
            end = min(len(lines), index + context_value) if context_value > 0 else index
            for current in range(start, end + 1):
                sanitized = lines[current - 1].replace("\r", "")
                truncated_text, was_truncated = truncate_line(sanitized)
                if was_truncated:
                    lines_truncated = True
                if current == index:
                    output_lines.append(f"{relative_path}:{current}: {truncated_text}")
                else:
                    output_lines.append(f"{relative_path}-{current}- {truncated_text}")
            if match_count >= limit:
                match_limit_reached = True
                break
        if match_limit_reached:
            break

    if match_count == 0:
        return AgentToolResult(content=[TextContent(text="No matches found")], details=None)

    raw_output = "\n".join(output_lines)
    truncation = truncate_head(raw_output, max_lines=sys.maxsize)
    output = truncation.content
    details: dict[str, Any] = {}
    notices: list[str] = []
    if match_limit_reached:
        notices.append(f"{limit} matches limit reached. Use limit={limit * 2} for more, or refine pattern")
        details["matchLimitReached"] = limit
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        details["truncation"] = truncation_to_details(truncation)
    if lines_truncated:
        notices.append(f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. Use read tool to see full lines")
        details["linesTruncated"] = True
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return AgentToolResult(content=[TextContent(text=output)], details=details or None)


def create_grep_tool_definition(
    cwd: str,
    operations: GrepOperations | None = None,
    workspace: WorkspaceCapability | None = None,
) -> ToolDefinition:
    ops = operations or _DEFAULT_OPERATIONS
    workspace = workspace or WorkspaceCapability(Path(cwd))
    return ToolDefinition(
        name="grep",
        label="grep",
        description=(
            f"Search file contents for a pattern. Returns matching lines with file paths and line numbers. "
            f"Respects .gitignore. Output is truncated to {DEFAULT_LIMIT} matches or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). Long lines are truncated to "
            f"{GREP_MAX_LINE_LENGTH} chars."
        ),
        parameters=GREP_SCHEMA,
        prompt_snippet="Search file contents for patterns (respects .gitignore)",
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_grep(
            cwd, workspace, ops, tid, args, signal, on_update, ctx
        ),
        render_call=lambda args, ctx=None: f"grep {args.get('pattern', '')}",
    )


def create_grep_tool(
    cwd: str,
    operations: GrepOperations | None = None,
    workspace: WorkspaceCapability | None = None,
) -> AgentTool:
    return wrap_tool_definition(
        create_grep_tool_definition(cwd, operations, workspace),
        lambda: ToolContext(cwd=cwd),
    )
