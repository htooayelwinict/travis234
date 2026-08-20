"""read tool."""

from __future__ import annotations

import base64
import codecs
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from travis.agent.types import AgentTool, AgentToolResult
from travis.ai.types import ImageContent, TextContent
from travis.coding_agent.artifacts import ARTIFACT_READ_BYTE_LIMIT, ArtifactRegistry
from travis.coding_agent.capabilities import CapabilityViolation, WorkspaceCapability
from travis.coding_agent.policy.context import workspace_path_context
from travis.coding_agent.tools.common import context_value as _ctx_value
from travis.coding_agent.tools.path_utils import format_path_relative_to_cwd, resolve_read_path, resolve_to_cwd
from travis.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    format_size,
    truncate_head,
    truncation_to_details,
)
from travis.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

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
        "byte_offset": {
            "type": "number",
            "minimum": 0,
            "description": "Byte offset to start reading from (0-indexed). Do not combine with offset/limit.",
        },
        "byte_limit": {
            "type": "number",
            "minimum": 1,
            "maximum": ARTIFACT_READ_BYTE_LIMIT,
            "description": "Maximum bytes to read. Do not combine with offset/limit.",
        },
    },
    "required": ["path"],
}


@dataclass
class ReadOperations:
    """Pluggable read operations matching the established ReadOperations seam."""

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


def _prepare_read_arguments(
    input_args: object,
    artifacts: ArtifactRegistry | None,
) -> object:
    if not isinstance(input_args, dict):
        return input_args
    line_mode = "offset" in input_args or "limit" in input_args
    byte_mode = "byte_offset" in input_args or "byte_limit" in input_args
    if not line_mode or not byte_mode:
        return input_args

    prepared = dict(input_args)
    path = prepared.get("path")
    is_artifact = (
        artifacts.is_readable_reference(path)
        if artifacts is not None and isinstance(path, str)
        else False
    )
    if is_artifact:
        prepared.pop("offset", None)
        prepared.pop("limit", None)
    else:
        prepared.pop("byte_offset", None)
        prepared.pop("byte_limit", None)
    return prepared


@dataclass(frozen=True)
class _ReadPagination:
    path: str
    offset: int | None
    limit: int | None
    byte_mode: bool
    byte_offset: int | None
    byte_limit: int | None
    is_artifact: bool


@dataclass(frozen=True)
class _ReadLineSelection:
    lines: list[str]
    selected: str
    start_line: int
    start_line_display: int
    user_limited_lines: int | None


@dataclass(frozen=True)
class _RenderedReadText:
    output: str
    details: dict[str, object] | None


def _normalize_read_byte_range(
    args: dict[str, object],
    byte_mode: bool,
) -> tuple[int | None, int | None]:
    byte_offset = _number_arg(args.get("byte_offset")) if byte_mode else None
    byte_limit = _number_arg(args.get("byte_limit")) if byte_mode else None
    if not byte_mode:
        return byte_offset, byte_limit
    byte_offset = 0 if byte_offset is None else byte_offset
    byte_limit = ARTIFACT_READ_BYTE_LIMIT if byte_limit is None else byte_limit
    if byte_offset < 0:
        raise ValueError("byte_offset must be non-negative")
    if byte_limit <= 0 or byte_limit > ARTIFACT_READ_BYTE_LIMIT:
        raise ValueError(f"byte_limit must be between 1 and {ARTIFACT_READ_BYTE_LIMIT}")
    return byte_offset, byte_limit


def _resolve_read_pagination(
    args: dict[str, object],
    path: str,
    artifacts: ArtifactRegistry | None,
) -> _ReadPagination:
    offset = _number_arg(args.get("offset"))
    limit = _number_arg(args.get("limit"))
    byte_mode = "byte_offset" in args or "byte_limit" in args
    line_mode = "offset" in args or "limit" in args
    if byte_mode and line_mode:
        raise ValueError("Cannot combine line pagination (offset/limit) with byte pagination (byte_offset/byte_limit)")
    is_artifact = artifacts.is_readable_reference(path) if artifacts is not None else False
    if is_artifact and line_mode:
        raise ValueError(
            f"Virtual artifacts require byte pagination. Retry read with path={path}, "
            f"byte_offset=0, byte_limit={ARTIFACT_READ_BYTE_LIMIT}; do not use offset/limit."
        )
    if is_artifact and not byte_mode:
        byte_mode = True
    byte_offset, byte_limit = _normalize_read_byte_range(args, byte_mode)
    return _ReadPagination(
        path=path,
        offset=offset,
        limit=limit,
        byte_mode=byte_mode,
        byte_offset=byte_offset,
        byte_limit=byte_limit,
        is_artifact=is_artifact,
    )


def _read_durable_artifact(
    artifacts: ArtifactRegistry | None,
    pagination: _ReadPagination,
) -> AgentToolResult | None:
    if artifacts is None or not pagination.is_artifact or not pagination.byte_mode:
        return None
    resource_resolution = artifacts.resolve_resource_read(
        pagination.path,
        byte_offset=pagination.byte_offset or 0,
        byte_limit=pagination.byte_limit or ARTIFACT_READ_BYTE_LIMIT,
    )
    if resource_resolution is not None:
        if not resource_resolution.available:
            raise ValueError(
                f"Artifact {pagination.path} is unavailable ({resource_resolution.error_code or 'unavailable'})"
            )
        assert pagination.byte_offset is not None
        return _render_durable_artifact_page(resource_resolution, pagination.byte_offset)
    return None


def _resolve_read_target(
    cwd: str,
    workspace: WorkspaceCapability,
    artifacts: ArtifactRegistry | None,
    pagination: _ReadPagination,
) -> str:
    artifact_path = (
        artifacts.resolve_read(pagination.path) if artifacts is not None and pagination.is_artifact else None
    )
    if artifact_path is not None:
        return str(artifact_path)
    try:
        absolute_path = str(workspace.resolve(pagination.path, access="read"))
        absolute_path = resolve_read_path(absolute_path, cwd)
        workspace.resolve(absolute_path, access="read")
        return absolute_path
    except CapabilityViolation:
        raise


def _image_note(
    mime_type: str,
    non_vision_note: str | None,
) -> str:
    note = f"Read image file [{mime_type}]"
    if non_vision_note:
        note += f"\n{non_vision_note}"
    return note


def _resized_image_result(
    resized: ReadImageResizeResult,
    non_vision_note: str | None,
) -> AgentToolResult:
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


def _read_image_result(
    operations: ReadOperations,
    absolute_path: str,
    mime_type: str,
    auto_resize_images: bool,
    image_resizer: ResizeImage,
    signal: object | None,
    ctx: ToolContext | None,
) -> AgentToolResult:
    data = operations.read_file(absolute_path)
    _check_aborted(signal)
    non_vision_note = _get_non_vision_image_note(ctx.model if ctx else None)
    if not auto_resize_images:
        return AgentToolResult(
            content=[
                TextContent(text=_image_note(mime_type, non_vision_note)),
                ImageContent(data=base64.b64encode(data).decode("ascii"), mime_type=mime_type),
            ],
            details=None,
        )
    resized = image_resizer(data, mime_type)
    _check_aborted(signal)
    if resized:
        return _resized_image_result(resized, non_vision_note)
    text = f"Read image file [{mime_type}]\n[Image omitted: could not be resized below the inline image size limit.]"
    if non_vision_note:
        text += f"\n{non_vision_note}"
    return AgentToolResult(content=[TextContent(text=text)], details=None)


def _render_read_byte_page(
    data: bytes,
    byte_offset: int | None,
    byte_limit: int | None,
) -> AgentToolResult:
    total_bytes = len(data)
    assert byte_offset is not None and byte_limit is not None
    if byte_offset > total_bytes:
        raise ValueError(f"byte_offset {byte_offset} is beyond end of file ({total_bytes} bytes total)")
    end_offset = min(total_bytes, byte_offset + byte_limit)
    output = data[byte_offset:end_offset].decode("utf-8", errors="replace")
    if end_offset < total_bytes:
        output += (
            f"\n\n[Showing bytes {byte_offset}-{end_offset - 1} of {total_bytes}. "
            f"Use byte_offset={end_offset} to continue.]"
        )
    else:
        output += f"\n\n[Showing bytes {byte_offset}-{end_offset - 1} of {total_bytes}. End of file.]"
    return AgentToolResult(
        content=[TextContent(text=output)],
        details={
            "byteRange": {
                "start": byte_offset,
                "endExclusive": end_offset,
                "totalBytes": total_bytes,
            }
        },
    )


def _select_read_lines(
    data: bytes,
    offset: int | None,
    limit: int | None,
) -> _ReadLineSelection:
    text_content = data.decode("utf-8", errors="replace")
    all_lines = text_content.split("\n")
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
    return _ReadLineSelection(
        lines=all_lines,
        selected=selected,
        start_line=start_line,
        start_line_display=start_line_display,
        user_limited_lines=user_limited_lines,
    )


def _render_truncated_read_lines(
    path: str,
    selection: _ReadLineSelection,
    truncation: TruncationResult,
) -> _RenderedReadText:
    if truncation.first_line_exceeds_limit:
        first_size = format_size(len(selection.lines[selection.start_line].encode("utf-8")))
        output = (
            f"[Line {selection.start_line_display} is {first_size}, exceeds "
            f"{format_size(DEFAULT_MAX_BYTES)} limit. Use bash: sed -n "
            f"'{selection.start_line_display}p' {path} | head -c {DEFAULT_MAX_BYTES}]"
        )
        return _RenderedReadText(output, {"truncation": truncation_to_details(truncation)})
    end_line_display = selection.start_line_display + truncation.output_lines - 1
    next_offset = end_line_display + 1
    output = truncation.content
    if truncation.truncated_by == "lines":
        output += (
            f"\n\n[Showing lines {selection.start_line_display}-{end_line_display} "
            f"of {len(selection.lines)}. Use offset={next_offset} to continue.]"
        )
    else:
        output += (
            f"\n\n[Showing lines {selection.start_line_display}-{end_line_display} "
            f"of {len(selection.lines)} ({format_size(DEFAULT_MAX_BYTES)} limit). "
            f"Use offset={next_offset} to continue.]"
        )
    return _RenderedReadText(output, {"truncation": truncation_to_details(truncation)})


def _render_read_lines(
    data: bytes,
    path: str,
    offset: int | None,
    limit: int | None,
) -> _RenderedReadText:
    selection = _select_read_lines(data, offset, limit)
    truncation = truncate_head(selection.selected)
    if truncation.first_line_exceeds_limit or truncation.truncated:
        return _render_truncated_read_lines(path, selection, truncation)
    if selection.user_limited_lines is not None and selection.start_line + selection.user_limited_lines < len(
        selection.lines
    ):
        remaining = len(selection.lines) - (selection.start_line + selection.user_limited_lines)
        next_offset = selection.start_line + selection.user_limited_lines + 1
        output = f"{truncation.content}\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"
        return _RenderedReadText(output, None)
    return _RenderedReadText(truncation.content, None)


def _execute_read(
    cwd: str,
    workspace: WorkspaceCapability,
    artifacts: ArtifactRegistry | None,
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
    pagination = _resolve_read_pagination(args, path, artifacts)
    durable_result = _read_durable_artifact(artifacts, pagination)
    if durable_result is not None:
        return durable_result

    absolute_path = _resolve_read_target(cwd, workspace, artifacts, pagination)
    _check_aborted(signal)
    operations.access(absolute_path)
    _check_aborted(signal)
    mime_type = operations.detect_image_mime_type(absolute_path) if operations.detect_image_mime_type else None
    _check_aborted(signal)
    if mime_type:
        return _read_image_result(
            operations,
            absolute_path,
            mime_type,
            auto_resize_images,
            image_resizer,
            signal,
            ctx,
        )
    data = operations.read_file(absolute_path)
    _check_aborted(signal)
    if pagination.byte_mode:
        return _render_read_byte_page(
            data,
            pagination.byte_offset,
            pagination.byte_limit,
        )

    rendered = _render_read_lines(data, path, pagination.offset, pagination.limit)
    _check_aborted(signal)
    return AgentToolResult(
        content=[TextContent(text=rendered.output)],
        details=rendered.details,
    )


def _render_durable_artifact_page(resolution, byte_offset: int) -> AgentToolResult:
    total_bytes = resolution.total_bytes
    assert total_bytes is not None
    data = resolution.content
    if byte_offset > 0 and data and 0x80 <= data[0] <= 0xBF:
        raise ValueError("byte_offset must align to a UTF-8 boundary")

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    output = decoder.decode(data, final=False)
    buffered, _ = decoder.getstate()
    consumed = len(data) - len(buffered)
    if consumed == 0 and byte_offset < total_bytes:
        raise ValueError("byte_limit is too small to reach the next UTF-8 boundary")
    end_offset = byte_offset + consumed
    if end_offset < total_bytes:
        output += (
            f"\n\n[Showing bytes {byte_offset}-{end_offset - 1} of {total_bytes}. "
            f"Use byte_offset={end_offset} to continue.]"
        )
    else:
        output += (
            f"\n\n[Showing bytes {byte_offset}-{end_offset - 1} of {total_bytes}. End of file.]"
        )
    return AgentToolResult(
        content=[TextContent(text=output)],
        details={
            "byteRange": {
                "start": byte_offset,
                "endExclusive": end_offset,
                "totalBytes": total_bytes,
            }
        },
    )


def _format_read_line_range(args) -> str:
    if args and (args.get("byte_offset") is not None or args.get("byte_limit") is not None):
        byte_offset = _number_arg(args.get("byte_offset")) or 0
        byte_limit = _number_arg(args.get("byte_limit"))
        if byte_limit is None:
            return f" bytes@{byte_offset}"
        return f" bytes@{byte_offset}+{byte_limit}"
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
    workspace: WorkspaceCapability | None = None,
    artifacts: ArtifactRegistry | None = None,
) -> ToolDefinition:
    workspace = workspace or WorkspaceCapability(Path(cwd))
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
            "Use byte_offset/byte_limit for artifacts or oversized single lines. "
            "When you need the full file, continue with the matching offset until complete."
        ),
        parameters=READ_SCHEMA,
        prompt_snippet="Read file contents",
        prompt_guidelines=["Use read to examine files instead of cat or sed."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_read(
            cwd, workspace, artifacts, ops, auto_resize_images, resize, tid, args, signal, on_update, ctx
        ),
        prepare_arguments=lambda args: _prepare_read_arguments(args, artifacts),
        render_call=_render_read_call,
        render_result=_render_read_result,
        effects=frozenset({"read"}),
        policy_context=workspace_path_context(cwd, "read"),
    )


def create_read_tool(
    cwd: str,
    *,
    operations: ReadOperations | None = None,
    auto_resize_images: bool = True,
    image_resizer: ResizeImage | None = None,
    model=None,
    workspace: WorkspaceCapability | None = None,
    artifacts: ArtifactRegistry | None = None,
) -> AgentTool:
    return wrap_tool_definition(
        create_read_tool_definition(
            cwd,
            operations=operations,
            auto_resize_images=auto_resize_images,
            image_resizer=image_resizer,
            workspace=workspace,
            artifacts=artifacts,
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
