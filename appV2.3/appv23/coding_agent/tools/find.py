"""find tool. Port of pi/packages/coding-agent/src/core/tools/find.ts."""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable

from appv23.agent.types import AgentTool, AgentToolResult
from appv23.ai.types import TextContent
from appv23.coding_agent.tools.path_utils import is_ignored_by_gitignore, load_gitignore_rules, resolve_to_cwd
from appv23.coding_agent.tools.truncate import DEFAULT_MAX_BYTES, format_size, truncate_head, truncation_to_details
from appv23.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob pattern to match files, e.g. '*.ts', '**/*.json', or 'src/**/*.spec.ts'",
        },
        "path": {"type": "string", "description": "Directory to search in (default: current directory)"},
        "limit": {"type": "number", "description": "Maximum number of results (default: 1000)"},
    },
    "required": ["pattern"],
}

DEFAULT_LIMIT = 1000
_EXCLUDED_DIRS = {".git", "node_modules"}


@dataclass(frozen=True)
class FindOperations:
    exists: Callable[[str], bool]
    glob: Callable[[str, str, dict[str, Any]], list[str]]


def _check_aborted(signal) -> None:
    if signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")


def _to_posix_path(path: str) -> str:
    return path.replace(os.sep, "/")


def _matches_pattern(pattern: str, relative_path: str, filename: str) -> bool:
    pattern = pattern.replace("\\", "/")
    if "/" not in pattern:
        return fnmatch.fnmatchcase(filename, pattern)

    patterns = [pattern]
    if not pattern.startswith("/") and not pattern.startswith("**/") and pattern != "**":
        patterns.append(f"**/{pattern}")
    for candidate in patterns:
        if fnmatch.fnmatchcase(relative_path, candidate):
            return True
        if candidate.startswith("**/") and fnmatch.fnmatchcase(relative_path, candidate[3:]):
            return True
    return False


def _local_glob(pattern: str, cwd: str, options: dict[str, Any]) -> list[str]:
    limit = max(1, int(options.get("limit", DEFAULT_LIMIT)))
    results: list[str] = []
    ignore_cache: dict[str, list] = {}
    for dirpath, dirnames, filenames in os.walk(cwd):
        rules = load_gitignore_rules(cwd, dirpath, ignore_cache)
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
            full_path = os.path.join(dirpath, filename)
            if is_ignored_by_gitignore(full_path, False, rules):
                continue
            relative_path = _to_posix_path(os.path.relpath(full_path, cwd))
            if _matches_pattern(pattern, relative_path, filename):
                results.append(relative_path)
    return sorted(results, key=str.lower)[:limit]


_DEFAULT_OPERATIONS = FindOperations(exists=os.path.exists, glob=_local_glob)


def _execute_find(
    cwd: str,
    operations: FindOperations,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
):
    _check_aborted(signal)
    pattern = args["pattern"]
    root = resolve_to_cwd(args.get("path") or ".", cwd)
    limit = max(1, int(args.get("limit", args.get("max_results", DEFAULT_LIMIT))))
    if not operations.exists(root):
        raise FileNotFoundError(f"Path not found: {root}")
    _check_aborted(signal)

    raw_results = operations.glob(pattern, root, {"ignore": ["**/node_modules/**", "**/.git/**"], "limit": limit})
    _check_aborted(signal)
    if not raw_results:
        return AgentToolResult(content=[TextContent(text="No files found matching pattern")], details=None)

    relativized: list[str] = []
    for result in raw_results[:limit]:
        if os.path.isabs(result):
            rel = os.path.relpath(result, root)
        else:
            rel = result
        relativized.append(_to_posix_path(rel.rstrip(os.sep)))

    result_limit_reached = len(raw_results) >= limit
    raw_output = "\n".join(relativized)
    truncation = truncate_head(raw_output, max_lines=sys.maxsize)
    output = truncation.content
    details: dict[str, Any] = {}
    notices: list[str] = []
    if result_limit_reached:
        notices.append(f"{limit} results limit reached. Use limit={limit * 2} for more, or refine pattern")
        details["resultLimitReached"] = limit
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        details["truncation"] = truncation_to_details(truncation)
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return AgentToolResult(content=[TextContent(text=output)], details=details or None)


def create_find_tool_definition(cwd: str, operations: FindOperations | None = None) -> ToolDefinition:
    ops = operations or _DEFAULT_OPERATIONS
    return ToolDefinition(
        name="find",
        label="find",
        description=(
            f"Search for files by glob pattern. Returns matching file paths relative to the search directory. "
            f"Respects .gitignore. Output is truncated to {DEFAULT_LIMIT} results or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
        ),
        parameters=FIND_SCHEMA,
        prompt_snippet="Find files by glob pattern (respects .gitignore)",
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_find(
            cwd, ops, tid, args, signal, on_update, ctx
        ),
        render_call=lambda args, ctx=None: f"find {args.get('pattern', '')}",
    )


def create_find_tool(cwd: str, operations: FindOperations | None = None) -> AgentTool:
    return wrap_tool_definition(create_find_tool_definition(cwd, operations), lambda: ToolContext(cwd=cwd))
