from __future__ import annotations

import base64
import json

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent as McpImageContent,
    ResourceLink,
    TextContent as McpTextContent,
    TextResourceContents,
)
from travis.agent.types import AgentToolResult
from travis.ai.types import ImageContent, TextContent
from travis234_mcp_adapter.output_guard import OutputGuard, SpillRegistry


MAX_IMAGE_BLOCKS = 8
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_RESULT_IMAGE_BYTES = 20 * 1024 * 1024
SUPPORTED_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


def convert_call_result(result: CallToolResult, spills: SpillRegistry) -> AgentToolResult:
    converted: list[TextContent | ImageContent] = []
    source_texts: list[str] = []
    image_blocks = 0
    accepted_images = 0
    rejected_images = 0
    aggregate_image_bytes = 0
    aggregate_overflow = 0
    for block in result.content:
        if isinstance(block, McpTextContent):
            source_texts.append(block.text)
            converted.append(TextContent(text=block.text))
        elif isinstance(block, McpImageContent):
            image_blocks += 1
            if block.mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
                rejected_images += 1
                converted.append(
                    TextContent(text=f"[MCP image rejected: unsupported MIME type {block.mime_type}]")
                )
                continue
            try:
                decoded = base64.b64decode(block.data, validate=True)
            except (ValueError, TypeError):
                rejected_images += 1
                converted.append(TextContent(text="[MCP image rejected: malformed base64 data]"))
                continue
            image_size = len(decoded)
            if image_size > MAX_IMAGE_BYTES:
                rejected_images += 1
                converted.append(TextContent(text="[MCP image rejected: decoded size exceeds 10 MiB]"))
                continue
            if (
                accepted_images >= MAX_IMAGE_BLOCKS
                or aggregate_image_bytes + image_size > MAX_RESULT_IMAGE_BYTES
            ):
                rejected_images += 1
                aggregate_overflow += 1
                continue
            accepted_images += 1
            aggregate_image_bytes += image_size
            converted.append(ImageContent(data=block.data, mime_type=block.mime_type))
        elif isinstance(block, AudioContent):
            converted.append(
                TextContent(
                    text=f"[MCP audio: {block.mime_type}, {_decoded_size(block.data)} bytes]"
                )
            )
        elif isinstance(block, ResourceLink):
            mime = f", {block.mime_type}" if block.mime_type else ""
            converted.append(
                TextContent(text=f"[MCP resource link: {block.name}{mime}] {block.uri}")
            )
        elif isinstance(block, EmbeddedResource):
            resource = block.resource
            mime = resource.mime_type or "unknown MIME type"
            if isinstance(resource, TextResourceContents):
                converted.append(
                    TextContent(text=f"[MCP resource: {resource.uri} ({mime})]\n{resource.text}")
                )
            elif isinstance(resource, BlobResourceContents):
                converted.append(
                    TextContent(
                        text=(
                            f"[MCP embedded binary resource: {resource.uri} ({mime}), "
                            f"{_decoded_size(resource.blob)} bytes]"
                        )
                    )
                )
            else:
                converted.append(TextContent(text=f"[Unsupported MCP resource: {type(resource).__name__}]"))
        else:
            converted.append(TextContent(text=f"[Unsupported MCP content: {type(block).__name__}]"))

    if aggregate_overflow:
        converted.append(
            TextContent(
                text=(
                    f"[MCP image limit reached: {aggregate_overflow} additional image "
                    f"block{'s' if aggregate_overflow != 1 else ''} omitted]"
                )
            )
        )

    if result.structured_content is not None and not _has_equivalent_text(
        source_texts,
        result.structured_content,
    ):
        converted.append(
            TextContent(
                text=json.dumps(
                    result.structured_content,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        )
    if not converted:
        converted.append(TextContent(text="MCP tool returned no content."))

    text = "\n".join(block.text for block in converted if isinstance(block, TextContent))
    guarded = OutputGuard(spills).guard(text)
    if guarded.spill_path is not None:
        replacement: list[TextContent | ImageContent] = []
        inserted = False
        for block in converted:
            if isinstance(block, TextContent):
                if not inserted:
                    replacement.append(TextContent(text=guarded.text))
                    inserted = True
                continue
            replacement.append(block)
        converted = replacement

    marker: dict[str, object] = {
        "operation": "call",
        "isError": bool(result.is_error),
        "hasStructuredContent": result.structured_content is not None,
        "spilled": guarded.spill_path is not None,
    }
    if guarded.spill_path is not None:
        marker["spillPath"] = str(guarded.spill_path)
        marker["truncatedBy"] = guarded.truncated_by
    if image_blocks:
        marker["acceptedImages"] = accepted_images
        marker["rejectedImages"] = rejected_images
        marker["imageBytes"] = aggregate_image_bytes
    return AgentToolResult(
        content=converted,
        details={"travis234Mcp": marker},
    )


def _has_equivalent_text(texts: list[str], structured: object) -> bool:
    for text in texts:
        try:
            if json.loads(text) == structured:
                return True
        except (json.JSONDecodeError, TypeError):
            continue
    return False


def _decoded_size(value: str) -> int:
    try:
        return len(base64.b64decode(value, validate=True))
    except (ValueError, TypeError):
        return len(value.encode("utf-8"))
