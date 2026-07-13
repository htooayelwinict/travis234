"""ls tool."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.capabilities import WorkspaceCapability
from travis.coding_agent.tools.path_utils import render_tool_path, resolve_to_cwd
from travis.coding_agent.tools.truncate import DEFAULT_MAX_BYTES, format_size, truncate_head, truncation_to_details
from travis.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

LS_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Directory to list (default cwd)"},
        "limit": {"type": "number", "description": "Maximum number of entries to return (default: 500)"},
    },
    "required": [],
}

DEFAULT_LIMIT = 500


@dataclass(frozen=True)
class LsOperations:
    exists: Callable[[str], bool]
    is_directory: Callable[[str], bool]
    readdir: Callable[[str], list[str]]


_DEFAULT_OPERATIONS = LsOperations(exists=os.path.exists, is_directory=os.path.isdir, readdir=os.listdir)


def _ctx_value(ctx, key: str, default=None):
    if isinstance(ctx, dict):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def _render_ls_call(args, ctx=None) -> str:
    return f"ls {render_tool_path((args or {}).get('path'), _ctx_value(ctx, 'cwd', ''), empty_fallback='.')}"


def _check_aborted(signal) -> None:
    if signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")


def _execute_ls(
    cwd: str,
    workspace: WorkspaceCapability,
    operations: LsOperations,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
):
    _check_aborted(signal)
    root = str(workspace.resolve(args.get("path") or ".", access="read"))
    limit = max(1, int(args.get("limit", DEFAULT_LIMIT)))
    if not operations.exists(root):
        raise FileNotFoundError(f"Path not found: {root}")
    if not operations.is_directory(root):
        raise NotADirectoryError(f"Not a directory: {root}")

    try:
        entries = operations.readdir(root)
    except OSError as error:
        raise OSError(f"Cannot read directory: {error}") from error

    entries = sorted(entries, key=str.lower)
    results: list[str] = []
    entry_limit_reached = False
    for entry in entries:
        _check_aborted(signal)
        if len(results) >= limit:
            entry_limit_reached = True
            break
        full = os.path.join(root, entry)
        try:
            suffix = "/" if operations.is_directory(full) else ""
        except OSError:
            continue
        results.append(f"{entry}{suffix}")

    if not results:
        return AgentToolResult(content=[TextContent(text="(empty directory)")], details=None)

    raw_output = "\n".join(results)
    truncation = truncate_head(raw_output, max_lines=sys.maxsize)
    output = truncation.content
    details: dict[str, Any] = {}
    notices: list[str] = []
    if entry_limit_reached:
        notices.append(f"{limit} entries limit reached. Use limit={limit * 2} for more")
        details["entryLimitReached"] = limit
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        details["truncation"] = truncation_to_details(truncation)
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return AgentToolResult(content=[TextContent(text=output)], details=details or None)


def create_ls_tool_definition(
    cwd: str,
    operations: LsOperations | None = None,
    workspace: WorkspaceCapability | None = None,
) -> ToolDefinition:
    ops = operations or _DEFAULT_OPERATIONS
    workspace = workspace or WorkspaceCapability(Path(cwd))
    return ToolDefinition(
        name="ls",
        label="ls",
        description=(
            f"List directory contents. Returns entries sorted alphabetically, with '/' suffix for directories. "
            f"Includes dotfiles. Output is truncated to {DEFAULT_LIMIT} entries or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
        ),
        parameters=LS_SCHEMA,
        prompt_snippet="List directory contents",
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_ls(
            cwd, workspace, ops, tid, args, signal, on_update, ctx
        ),
        render_call=_render_ls_call,
    )


def create_ls_tool(
    cwd: str,
    operations: LsOperations | None = None,
    workspace: WorkspaceCapability | None = None,
) -> AgentTool:
    return wrap_tool_definition(
        create_ls_tool_definition(cwd, operations, workspace),
        lambda: ToolContext(cwd=cwd),
    )
