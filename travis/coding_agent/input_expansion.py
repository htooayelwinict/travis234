"""Operator-authored file and image input expansion."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from html import escape
from pathlib import Path
import re
from typing import Sequence

from travis.ai.types import ImageContent, TextContent
from travis.coding_agent.tools.truncate import format_size, truncate_head

MAX_INLINE_IMAGE_BASE64_BYTES = int(4.5 * 1024 * 1024)


class InputExpansionError(ValueError):
    """Raised before provider submission when an input reference is invalid."""


@dataclass(frozen=True)
class ExpandedInput:
    text: str
    content: tuple[TextContent | ImageContent, ...]
    referenced_paths: tuple[str, ...]


_REFERENCE_PATTERN = re.compile(
    r"(?<!\\)(?<!\S)@(?:\"((?:\\.|[^\"])*)\"|'((?:\\.|[^'])*)'|([^\s]+))"
)


def expand_user_input(
    text: str,
    *,
    cwd: str,
    images: Sequence[str],
) -> ExpandedInput:
    root = Path(cwd).expanduser().resolve()
    referenced_paths: list[str] = []
    image_blocks: list[ImageContent] = []

    def include_reference(match: re.Match[str]) -> str:
        raw_path = next(group for group in match.groups() if group is not None)
        raw_path = _unescape_quoted_path(raw_path)
        path = _resolve_operator_path(raw_path, root)
        _record_path(path, referenced_paths)
        data = _read_file(path)
        mime_type = _detect_image_mime_type(data)
        if mime_type is not None:
            image_blocks.append(_image_content(path, data, mime_type))
            return _image_reference(path)
        return _text_reference(path, data)

    expanded_text = _REFERENCE_PATTERN.sub(include_reference, text).replace(r"\@", "@")
    for image_path in images:
        path = _resolve_operator_path(str(image_path), root)
        data = _read_file(path)
        mime_type = _detect_image_mime_type(data)
        if mime_type is None:
            raise InputExpansionError(f"unsupported image format: {path}")
        _record_path(path, referenced_paths)
        image_blocks.append(_image_content(path, data, mime_type))
        expanded_text = _append_reference(expanded_text, _image_reference(path))

    return ExpandedInput(
        text=expanded_text,
        content=(TextContent(text=expanded_text), *image_blocks),
        referenced_paths=tuple(referenced_paths),
    )


def _resolve_operator_path(raw_path: str, cwd: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    explicitly_absolute = candidate.is_absolute() or raw_path == "~" or raw_path.startswith("~/")
    resolved = (candidate if candidate.is_absolute() else cwd / candidate).resolve()
    if not explicitly_absolute:
        try:
            resolved.relative_to(cwd)
        except ValueError as error:
            raise InputExpansionError(
                f"Relative input path is outside the working directory: {raw_path}"
            ) from error
    if not resolved.exists():
        raise InputExpansionError(f"Input path does not exist: {resolved}")
    if resolved.is_dir():
        raise InputExpansionError(f"Input path is a directory: {resolved}")
    if not resolved.is_file():
        raise InputExpansionError(f"Input path is not a regular file: {resolved}")
    return resolved


def _read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise InputExpansionError(f"Could not read input file {path}: {error}") from error


def _text_reference(path: Path, data: bytes) -> str:
    if b"\0" in data:
        raise InputExpansionError(f"Input file is binary and cannot be included as text: {path}")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InputExpansionError(
            f"Input file is binary or not valid UTF-8: {path}"
        ) from error
    truncation = truncate_head(content)
    body = truncation.content
    if truncation.truncated:
        notice = (
            f"[Truncated: showing {truncation.output_lines} of "
            f"{truncation.total_lines} lines ({format_size(truncation.max_bytes)} limit). "
            "Use the read tool for the remaining content.]"
        )
        body = f"{body}\n\n{notice}" if body else notice
    return f'<file name="{escape(str(path), quote=True)}">\n{body}\n</file>'


def _image_content(path: Path, data: bytes, mime_type: str) -> ImageContent:
    encoded = base64.b64encode(data).decode("ascii")
    if len(encoded.encode("ascii")) > MAX_INLINE_IMAGE_BASE64_BYTES:
        raise InputExpansionError(
            f"Image exceeds the inline image size limit ({format_size(MAX_INLINE_IMAGE_BASE64_BYTES)}): {path}"
        )
    return ImageContent(data=encoded, mime_type=mime_type)


def _image_reference(path: Path) -> str:
    return f'<file name="{escape(str(path), quote=True)}"></file>'


def _append_reference(text: str, reference: str) -> str:
    if not text:
        return reference
    separator = "" if text.endswith("\n") else "\n"
    return f"{text}{separator}{reference}"


def _record_path(path: Path, paths: list[str]) -> None:
    resolved = str(path)
    if resolved not in paths:
        paths.append(resolved)


def _unescape_quoted_path(value: str) -> str:
    return re.sub(r"\\([\\\"'])", r"\1", value)


def _detect_image_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


__all__ = [
    "ExpandedInput",
    "InputExpansionError",
    "MAX_INLINE_IMAGE_BASE64_BYTES",
    "expand_user_input",
]
