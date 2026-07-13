"""Hermes-style tool dispatch policy for appv23.

The core agent loop is a Pi port. This module keeps non-Pi dispatch safety
rules isolated at the tool-batch boundary, matching Hermes' pattern of deciding
whether a batch is safe to run concurrently before choosing sequential vs
parallel execution.
"""

from __future__ import annotations

import posixpath
from typing import Any

from appv23.agent.types import AgentTool

FILE_MUTATING_TOOL_NAMES = frozenset({"write", "edit", "write_file", "patch"})
PATH_SCOPED_TOOL_NAMES = frozenset({"read", "write", "edit", "read_file", "write_file", "patch"})
BATCH_UNSAFE_TOOL_NAMES = frozenset({"bash", "terminal", "execute_code"})
PARALLEL_SAFE_TOOL_NAMES = frozenset(
    {
        "grep",
        "find",
        "ls",
        "search_files",
        "session_search",
        "web_search",
        "web_extract",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
    }
)


def should_parallelize_tool_batch(tool_calls: list[Any], tools: list[AgentTool]) -> bool:
    """Return True only when a completed tool-call batch is safe in parallel."""

    if len(tool_calls) <= 1:
        return False
    if any(next((tool.execution_mode for tool in tools if tool.name == call.name), None) == "sequential" for call in tool_calls):
        return False

    tool_names = [str(getattr(call, "name", "")) for call in tool_calls]
    if any(name in BATCH_UNSAFE_TOOL_NAMES for name in tool_names):
        return False

    reserved_paths: list[tuple[str, ...]] = []
    for call in tool_calls:
        tool_name = str(getattr(call, "name", ""))
        if tool_name in PARALLEL_SAFE_TOOL_NAMES:
            continue
        if tool_name not in PATH_SCOPED_TOOL_NAMES:
            return False
        scope = _parallel_scope_path(getattr(call, "arguments", None))
        if scope is None:
            return False
        if any(_paths_overlap(scope, existing) for existing in reserved_paths):
            return False
        reserved_paths.append(scope)
    return True


def _parallel_scope_path(args: Any) -> tuple[str, ...] | None:
    if not isinstance(args, dict):
        return None
    raw_path = args.get("path") or args.get("file_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    normalized = posixpath.normpath(raw_path.replace("\\", "/"))
    if normalized in {"", "."}:
        return None
    return tuple(part for part in normalized.split("/") if part and part != ".")


def _paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        return False
    common_len = min(len(left), len(right))
    return left[:common_len] == right[:common_len]
