"""write tool. Port of pi/packages/coding-agent/src/core/tools/write.ts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.file_mutation_queue import with_file_mutation_queue
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to write (relative or absolute)"},
        "content": {"type": "string", "description": "Full file content to write"},
    },
    "required": ["path", "content"],
}


@dataclass
class WriteOperations:
    mkdir: Callable[[str], None]
    write_file: Callable[[str, str], None]


def _default_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _default_write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


_DEFAULT_OPERATIONS = WriteOperations(mkdir=_default_mkdir, write_file=_default_write_file)


def _execute_write(
    cwd: str,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
    operations: WriteOperations = _DEFAULT_OPERATIONS,
):
    path = args["path"]
    content = args["content"]
    absolute_path = resolve_to_cwd(path, cwd)
    parent = os.path.dirname(absolute_path)

    def mutate() -> None:
        if signal and signal.aborted:
            raise RuntimeError("Operation aborted")
        operations.mkdir(parent)
        if signal and signal.aborted:
            raise RuntimeError("Operation aborted")
        operations.write_file(absolute_path, content)
        if signal and signal.aborted:
            raise RuntimeError("Operation aborted")

    with_file_mutation_queue(absolute_path, mutate)
    return AgentToolResult(
        content=[TextContent(text=f"Successfully wrote {len(content)} bytes to {path}")],
        details=None,
    )


def create_write_tool_definition(cwd: str, operations: WriteOperations | None = None) -> ToolDefinition:
    ops = operations or _DEFAULT_OPERATIONS
    return ToolDefinition(
        name="write",
        label="write",
        description=(
            "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. "
            "Automatically creates parent directories."
        ),
        parameters=WRITE_SCHEMA,
        prompt_snippet="Create or overwrite files",
        prompt_guidelines=["Use write only for new files or complete rewrites."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_write(
            cwd, tid, args, signal, on_update, ctx, ops
        ),
        render_call=lambda args, ctx=None: f"write {args.get('path', '')}",
    )


def create_write_tool(cwd: str, operations: WriteOperations | None = None) -> AgentTool:
    return wrap_tool_definition(create_write_tool_definition(cwd, operations), lambda: ToolContext(cwd=cwd))
