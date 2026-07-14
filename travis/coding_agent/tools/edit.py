"""edit tool."""

from __future__ import annotations

import json
import os
from pathlib import Path

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.capabilities import WorkspaceCapability
from travis.coding_agent.tools.atomic_file import atomic_replace_text
from travis.coding_agent.tools.common import context_value as _ctx_value
from travis.coding_agent.tools.common import file_content_metadata as _file_content_metadata
from travis.coding_agent.tools.common import render_error_result as _render_edit_result
from travis.coding_agent.tools.edit_diff import (
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    generate_unified_patch,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from travis.coding_agent.tools.file_mutation_queue import with_file_mutation_queue
from travis.coding_agent.tools.path_utils import render_tool_path, resolve_to_cwd
from travis.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to edit (relative or absolute)"},
        "edits": {
            "type": "array",
            "minItems": 1,
            "description": (
                "One or more targeted replacements. Each edit is matched against the original file, not incrementally. "
                "Do not include overlapping or nested edits. If two changes touch the same block or nearby lines, "
                "merge them into one edit instead."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "oldText": {
                        "type": "string",
                        "description": (
                            "Exact text for one targeted replacement. It must be unique in the original file and must "
                            "not overlap with any other edits[].oldText in the same call."
                        ),
                    },
                    "newText": {"type": "string", "description": "Replacement text for this targeted edit."},
                },
                "required": ["oldText", "newText"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["path", "edits"],
    "additionalProperties": False,
}


def prepare_edit_arguments(input_args):
    if not isinstance(input_args, dict):
        return input_args
    args = dict(input_args)
    if isinstance(args.get("edits"), str):
        try:
            parsed = json.loads(args["edits"])
            if isinstance(parsed, list):
                args["edits"] = parsed
        except json.JSONDecodeError:
            pass

    old_text = args.get("oldText")
    new_text = args.get("newText")
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return args

    edits = list(args["edits"]) if isinstance(args.get("edits"), list) else []
    edits.append({"oldText": old_text, "newText": new_text})
    args.pop("oldText", None)
    args.pop("newText", None)
    args["edits"] = edits
    return args


def _render_edit_call(args, ctx=None) -> str:
    return f"edit {render_tool_path((args or {}).get('file_path') or (args or {}).get('path'), _ctx_value(ctx, 'cwd', ''))}"


def _validate_edit_input(args) -> tuple[str, list[dict]]:
    path = args.get("path")
    edits = args.get("edits")
    if not isinstance(path, str) or not path:
        raise ValueError("Edit tool input is invalid. path must be a non-empty string.")
    if not isinstance(edits, list) or not edits:
        raise ValueError("Edit tool input is invalid. edits must contain at least one replacement.")
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(f"Edit tool input is invalid. edits[{index}] must be an object.")
        if not isinstance(edit.get("oldText"), str) or not isinstance(edit.get("newText"), str):
            raise ValueError(f"Edit tool input is invalid. edits[{index}] must contain oldText and newText strings.")
    return path, edits


def _execute_edit(
    cwd: str,
    workspace: WorkspaceCapability,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
):
    path, edits = _validate_edit_input(args)
    absolute_path = str(workspace.resolve(path, access="write"))
    result_details: dict = {}

    def mutate() -> None:
        nonlocal result_details
        if signal and signal.aborted:
            raise RuntimeError("Operation aborted")
        if not os.path.exists(absolute_path):
            raise FileNotFoundError(f"File not found: {path}")
        with open(absolute_path, "r", encoding="utf-8") as handle:
            raw_content = handle.read()
        if signal and signal.aborted:
            raise RuntimeError("Operation aborted")
        bom, content = strip_bom(raw_content)
        original_ending = detect_line_ending(content)
        normalized_content = normalize_to_lf(content)
        applied = apply_edits_to_normalized_content(normalized_content, edits, path)
        final_content = bom + restore_line_endings(applied.new_content, original_ending)
        diff_result = generate_diff_string(applied.base_content, applied.new_content)
        patch = generate_unified_patch(path, applied.base_content, applied.new_content)
        atomic_replace_text(Path(absolute_path), final_content)
        if signal and signal.aborted:
            raise RuntimeError("Operation aborted")
        result_details = {
            "path": absolute_path,
            "total_bytes": os.path.getsize(absolute_path),
            **_file_content_metadata(final_content),
            "diff": diff_result.diff,
            "patch": patch,
            "first_changed_line": diff_result.first_changed_line,
        }

    with_file_mutation_queue(absolute_path, mutate)
    return AgentToolResult(
        content=[TextContent(text=f"Successfully replaced {len(edits)} block(s) in {path}.")],
        details=result_details,
    )


def create_edit_tool_definition(
    cwd: str,
    workspace: WorkspaceCapability | None = None,
) -> ToolDefinition:
    workspace = workspace or WorkspaceCapability(Path(cwd))
    return ToolDefinition(
        name="edit",
        label="edit",
        description=(
            "Edit a single file using exact text replacement. Every edits[].oldText must match a unique, "
            "non-overlapping region of the original file. If two changes affect the same block or nearby lines, "
            "merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions "
            "just to connect distant changes."
        ),
        parameters=EDIT_SCHEMA,
        prompt_snippet="Make precise file edits with exact text replacement, including multiple disjoint edits in one call",
        prompt_guidelines=[
            "Use edit for precise changes (edits[].oldText must match exactly)",
            "When changing multiple separate locations in one file, use one edit call with multiple entries in edits[] instead of multiple edit calls",
            "Each edits[].oldText is matched against the original file, not after earlier edits are applied. Do not emit overlapping or nested edits. Merge nearby changes into one edit.",
            "Keep edits[].oldText as small as possible while still being unique in the file. Do not pad with large unchanged regions.",
        ],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_edit(
            cwd, workspace, tid, args, signal, on_update, ctx
        ),
        prepare_arguments=prepare_edit_arguments,
        render_call=_render_edit_call,
        render_result=_render_edit_result,
    )


def create_edit_tool(cwd: str, workspace: WorkspaceCapability | None = None) -> AgentTool:
    return wrap_tool_definition(
        create_edit_tool_definition(cwd, workspace),
        lambda: ToolContext(cwd=cwd),
    )
