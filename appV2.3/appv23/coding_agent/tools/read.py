"""read tool. Port of pi/packages/coding-agent/src/core/tools/read.ts."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Callable

from appv23.agent.types import AgentTool, AgentToolResult
from appv23.ai.types import ImageContent, TextContent
from appv23.coding_agent.tools.path_utils import format_path_relative_to_cwd, resolve_read_path, resolve_to_cwd
from appv23.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
    truncation_to_details,
)
from appv23.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

ReadFile = Callable[[str], bytes]
AccessFile = Callable[[str], None]
DetectImageMimeType = Callable[[str], str | None]
ResizeImage = Callable[[bytes, str], "ReadImageResizeResult | None"]

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to read (relative or absolute)"},
        "offset": {"type": "number", "description": "Line number to start reading from (1-indexed)"},
        "limit": {"type": "number", "description": "Maximum number of lines to read"},
    },
    "required": ["path"],
}


@dataclass
class ReadOperations:
    """Pluggable read operations matching Pi's ReadOperations seam."""

    read_file: ReadFile
    access: AccessFile
    detect_image_mime_type: DetectImageMimeType | None = None


@dataclass
class ReadImageResizeResult:
    data: str
    mime_type: str
    was_resized: bool = False
    original_width: int | None = None
    original_height: int | None = None
    width: int | None = None
    height: int | None = None


def _default_access(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"File is not readable: {path}")


def _default_read_file(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _default_resize_image(data: bytes, mime_type: str) -> ReadImageResizeResult | None:
    return ReadImageResizeResult(data=base64.b64encode(data).decode("ascii"), mime_type=mime_type)


def _check_aborted(signal) -> None:
    if getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")


def _get_non_vision_image_note(model) -> str | None:
    if not model or "image" in getattr(model, "input", []):
        return None
    return "[Current model does not support images. The image will be omitted from this request.]"


def _format_dimension_note(result: ReadImageResizeResult) -> str | None:
    if not result.was_resized:
        return None
    if not all([result.original_width, result.original_height, result.width, result.height]):
        return None
    scale = result.original_width / result.width
    return (
        f"[Image: original {result.original_width}x{result.original_height}, displayed at "
        f"{result.width}x{result.height}. Multiply coordinates by {scale:.2f} to map to original image.]"
    )


def _execute_read(
    cwd: str,
    operations: ReadOperations,
    auto_resize_images: bool,
    image_resizer: ResizeImage,
    tool_call_id,
    args,
    signal=None,
    on_update=None,
    ctx: ToolContext | None = None,
):
    _check_aborted(signal)
    path = args["path"]
    offset = _number_arg(args.get("offset"))
    limit = _number_arg(args.get("limit"))
    absolute_path = resolve_read_path(path, cwd)
    _check_aborted(signal)
    operations.access(absolute_path)
    _check_aborted(signal)
    mime_type = operations.detect_image_mime_type(absolute_path) if operations.detect_image_mime_type else None
    _check_aborted(signal)
    if mime_type:
        data = operations.read_file(absolute_path)
        _check_aborted(signal)
        non_vision_note = _get_non_vision_image_note(ctx.model if ctx else None)
        if auto_resize_images:
            resized = image_resizer(data, mime_type)
            _check_aborted(signal)
            if not resized:
                text = (
                    f"Read image file [{mime_type}]\n"
                    "[Image omitted: could not be resized below the inline image size limit.]"
                )
                if non_vision_note:
                    text += f"\n{non_vision_note}"
                return AgentToolResult(content=[TextContent(text=text)], details=None)
            note = f"Read image file [{resized.mime_type}]"
            dimension_note = _format_dimension_note(resized)
            if dimension_note:
                note += f"\n{dimension_note}"
            if non_vision_note:
                note += f"\n{non_vision_note}"
            return AgentToolResult(
                content=[
                    TextContent(text=note),
                    ImageContent(data=resized.data, mime_type=resized.mime_type),
                ],
                details=None,
            )
        note = f"Read image file [{mime_type}]"
        if non_vision_note:
            note += f"\n{non_vision_note}"
        return AgentToolResult(
            content=[
                TextContent(text=note),
                ImageContent(data=base64.b64encode(data).decode("ascii"), mime_type=mime_type),
            ],
            details=None,
        )
    text_content = operations.read_file(absolute_path).decode("utf-8", errors="replace")
    _check_aborted(signal)
    all_lines = text_content.split("\n")
    total_file_lines = len(all_lines)
    start_line = max(0, offset - 1) if offset else 0
    start_line_display = start_line + 1
    if start_line >= len(all_lines):
        raise ValueError(f"Offset {offset} is beyond end of file ({len(all_lines)} lines total)")

    user_limited_lines = None
    if limit is not None:
        end_line = min(start_line + limit, len(all_lines))
        selected = "\n".join(all_lines[start_line:end_line])
        user_limited_lines = end_line - start_line
    else:
        selected = "\n".join(all_lines[start_line:])

    truncation = truncate_head(selected)
    if truncation.first_line_exceeds_limit:
        first_size = format_size(len(all_lines[start_line].encode("utf-8")))
        output = (
            f"[Line {start_line_display} is {first_size}, exceeds {format_size(DEFAULT_MAX_BYTES)} limit. "
            f"Use bash: sed -n '{start_line_display}p' {path} | head -c {DEFAULT_MAX_BYTES}]"
        )
        details = {"truncation": truncation_to_details(truncation)}
    elif truncation.truncated:
        end_line_display = start_line_display + truncation.output_lines - 1
        next_offset = end_line_display + 1
        output = truncation.content
        if truncation.truncated_by == "lines":
            output += f"\n\n[Showing lines {start_line_display}-{end_line_display} of {total_file_lines}. Use offset={next_offset} to continue.]"
        else:
            output += (
                f"\n\n[Showing lines {start_line_display}-{end_line_display} of {total_file_lines} "
                f"({format_size(DEFAULT_MAX_BYTES)} limit). Use offset={next_offset} to continue.]"
            )
        details = {"truncation": truncation_to_details(truncation)}
    elif user_limited_lines is not None and start_line + user_limited_lines < len(all_lines):
        remaining = len(all_lines) - (start_line + user_limited_lines)
        next_offset = start_line + user_limited_lines + 1
        output = f"{truncation.content}\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"
        details = None
    else:
        output = truncation.content
        details = None

    _check_aborted(signal)
    return AgentToolResult(content=[TextContent(text=output)], details=details)


def _format_read_line_range(args) -> str:
    if not args or (args.get("offset") is None and args.get("limit") is None):
        return ""
    raw_offset = args.get("offset")
    raw_limit = args.get("limit")
    start_line = _number_arg(raw_offset) if raw_offset is not None else 1
    limit = _number_arg(raw_limit) if raw_limit is not None else None
    if start_line is None:
        return ""
    if raw_limit is not None:
        if limit is None:
            return ""
        end_line = start_line + limit - 1
        return f":{start_line}-{end_line}"
    return f":{start_line}" if raw_offset is not None else ""


def _number_arg(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _ctx_value(ctx, key: str, default=None):
    if isinstance(ctx, dict):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def _to_posix_path(path: str) -> str:
    return path.replace(os.sep, "/")


def _compact_read_classification(args, cwd: str) -> tuple[str, str] | None:
    if not args:
        return None
    raw_path = args.get("file_path") or args.get("path") or ""
    if not raw_path:
        return None
    absolute_path = resolve_to_cwd(raw_path, cwd)
    file_name = os.path.basename(absolute_path)
    if file_name == "SKILL.md":
        return ("skill", os.path.basename(os.path.dirname(absolute_path)) or file_name)
    label = _to_posix_path(format_path_relative_to_cwd(absolute_path, cwd))
    if label == "README.md" or label.startswith("docs/") or label.startswith("examples/"):
        return ("docs", label)
    if file_name in {"AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"}:
        return ("resource", label)
    return None


def _render_read_call(args, ctx=None) -> str:
    cwd = _ctx_value(ctx, "cwd", "")
    expanded = _ctx_value(ctx, "expanded", False)
    classification = None if expanded else _compact_read_classification(args, cwd)
    line_range = _format_read_line_range(args)
    if classification:
        kind, label = classification
        if kind == "skill":
            return f"[skill] {label}{line_range} (to expand)"
        if kind == "docs" and label.startswith("docs/"):
            return f"read {label}{line_range} (to expand)"
        return f"read {kind} {label}{line_range} (to expand)"
    path = (args or {}).get("file_path") or (args or {}).get("path") or ""
    display = format_path_relative_to_cwd(resolve_to_cwd(path, cwd), cwd) if cwd and path else path
    return f"read {display}{line_range}"


def _text_output(result: AgentToolResult) -> str:
    lines: list[str] = []
    for block in result.content:
        if getattr(block, "type", None) == "text":
            lines.append(block.text)
        elif getattr(block, "type", None) == "image":
            lines.append(f"[image: {block.mime_type}]")
    return "\n".join(lines)


def _render_read_result(result: AgentToolResult, options=None, ctx=None) -> str:
    expanded = _ctx_value(options, "expanded", False)
    is_error = _ctx_value(ctx, "is_error", False)
    if not expanded and not is_error:
        return ""
    output = _text_output(result)
    if not expanded:
        lines = output.split("\n")
        if len(lines) > 10:
            return "\n".join(lines[:10]) + f"\n... ({len(lines) - 10} more lines, to expand)"
    return output


def create_read_tool_definition(
    cwd: str,
    *,
    operations: ReadOperations | None = None,
    auto_resize_images: bool = True,
    image_resizer: ResizeImage | None = None,
) -> ToolDefinition:
    ops = operations or ReadOperations(
        read_file=_default_read_file,
        access=_default_access,
        detect_image_mime_type=_detect_supported_image_mime_type,
    )
    resize = image_resizer or _default_resize_image
    return ToolDefinition(
        name="read",
        label="read",
        description=(
            f"Read the contents of a file. Supports text files and images (jpg, png, gif, webp). Images are sent "
            f"as attachments. For text files, output is truncated to {DEFAULT_MAX_LINES} lines or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). Use offset/limit for large files. "
            "When you need the full file, continue with offset until complete."
        ),
        parameters=READ_SCHEMA,
        prompt_snippet="Read file contents",
        prompt_guidelines=["Use read to examine files instead of cat or sed."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_read(
            cwd, ops, auto_resize_images, resize, tid, args, signal, on_update, ctx
        ),
        render_call=_render_read_call,
        render_result=_render_read_result,
    )


def create_read_tool(
    cwd: str,
    *,
    operations: ReadOperations | None = None,
    auto_resize_images: bool = True,
    image_resizer: ResizeImage | None = None,
    model=None,
) -> AgentTool:
    return wrap_tool_definition(
        create_read_tool_definition(
            cwd,
            operations=operations,
            auto_resize_images=auto_resize_images,
            image_resizer=image_resizer,
        ),
        lambda: ToolContext(cwd=cwd, model=model),
    )


def _detect_supported_image_mime_type(path: str) -> str | None:
    with open(path, "rb") as handle:
        header = handle.read(16)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None
