"""Output truncation. Port of pi/packages/coding-agent/src/core/tools/truncate.ts."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 51200
GREP_MAX_LINE_LENGTH = 500


@dataclass
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: str | None  # "lines" | "bytes" | None
    output_lines: int
    total_lines: int
    first_line_exceeds_limit: bool
    total_bytes: int = 0
    output_bytes: int = 0
    last_line_partial: bool = False
    max_lines: int = DEFAULT_MAX_LINES
    max_bytes: int = DEFAULT_MAX_BYTES


def truncation_to_details(truncation: TruncationResult) -> dict[str, str | bool | int | None]:
    return {
        "content": truncation.content,
        "truncated": truncation.truncated,
        "truncatedBy": truncation.truncated_by,
        "totalLines": truncation.total_lines,
        "totalBytes": truncation.total_bytes,
        "outputLines": truncation.output_lines,
        "outputBytes": truncation.output_bytes,
        "lastLinePartial": truncation.last_line_partial,
        "firstLineExceedsLimit": truncation.first_line_exceeds_limit,
        "maxLines": truncation.max_lines,
        "maxBytes": truncation.max_bytes,
    }


def _split_lines_for_counting(content: str) -> list[str]:
    if content == "":
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / (1024 * 1024):.1f}MB"


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def truncate_head(content: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> TruncationResult:
    """Keep the head of `content` within line and byte limits (pi semantics)."""
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)
    total_bytes = len(content.encode("utf-8"))

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            output_lines=total_lines,
            total_lines=total_lines,
            first_line_exceeds_limit=False,
            total_bytes=total_bytes,
            output_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    if lines and len(lines[0].encode("utf-8")) > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            output_lines=0,
            total_lines=total_lines,
            first_line_exceeds_limit=True,
            total_bytes=total_bytes,
            output_bytes=0,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    kept: list[str] = []
    byte_count = 0
    truncated_by: str | None = None
    for index, line in enumerate(lines):
        if index >= max_lines:
            truncated_by = "lines"
            break
        line_bytes = len(line.encode("utf-8")) + (1 if index > 0 else 0)
        if byte_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        kept.append(line)
        byte_count += line_bytes

    truncated = truncated_by is not None
    output = "\n".join(kept)
    return TruncationResult(
        content=output,
        truncated=truncated,
        truncated_by=truncated_by,
        output_lines=len(kept),
        total_lines=total_lines,
        first_line_exceeds_limit=False,
        total_bytes=total_bytes,
        output_bytes=len(output.encode("utf-8")),
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_tail(content: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> TruncationResult:
    """Keep the tail of `content` within line and byte limits (pi bash semantics)."""
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)
    total_bytes = len(content.encode("utf-8"))

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            output_lines=total_lines,
            total_lines=total_lines,
            first_line_exceeds_limit=False,
            total_bytes=total_bytes,
            output_bytes=total_bytes,
            last_line_partial=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    kept: list[str] = []
    byte_count = 0
    truncated_by: str | None = None
    last_line_partial = False

    for reverse_index, line in enumerate(reversed(lines)):
        if reverse_index >= max_lines:
            truncated_by = "lines"
            break
        line_bytes = len(line.encode("utf-8")) + (1 if kept else 0)
        if byte_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not kept:
                line_bytes_raw = line.encode("utf-8")
                start = max(0, len(line_bytes_raw) - max_bytes)
                while start < len(line_bytes_raw) and (line_bytes_raw[start] & 0xC0) == 0x80:
                    start += 1
                kept.append(line_bytes_raw[start:].decode("utf-8", errors="ignore"))
                last_line_partial = True
            break
        kept.append(line)
        byte_count += line_bytes

    kept.reverse()
    output = "\n".join(kept)
    return TruncationResult(
        content=output,
        truncated=truncated_by is not None,
        truncated_by=truncated_by,
        output_lines=len(kept),
        total_lines=total_lines,
        first_line_exceeds_limit=False,
        total_bytes=total_bytes,
        output_bytes=len(output.encode("utf-8")),
        last_line_partial=last_line_partial,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )
