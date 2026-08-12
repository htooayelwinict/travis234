from __future__ import annotations

import base64
from pathlib import Path

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
from travis.ai.types import ImageContent, TextContent
from travis234_mcp_adapter.output_guard import MAX_INLINE_BYTES, SpillRegistry
from travis234_mcp_adapter.results import (
    MAX_IMAGE_BLOCKS,
    MAX_IMAGE_BYTES,
    MAX_RESULT_IMAGE_BYTES,
    convert_call_result,
)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def test_converts_text_image_resources_audio_and_blob(tmp_path: Path) -> None:
    result = CallToolResult(
        content=[
            McpTextContent(type="text", text="plain"),
            McpImageContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
            ResourceLink(name="manual", uri="https://example.test/manual", mimeType="text/html"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="file:///note.txt",
                    mimeType="text/plain",
                    text="embedded text",
                ),
            ),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri="file:///blob.bin",
                    mimeType="application/octet-stream",
                    blob="YWJj",
                ),
            ),
            AudioContent(type="audio", data="YWJjZA==", mimeType="audio/wav"),
        ]
    )

    converted = convert_call_result(result, SpillRegistry(tmp_path))

    assert isinstance(converted.content[0], TextContent)
    assert converted.content[0].text == "plain"
    assert isinstance(converted.content[1], ImageContent)
    assert converted.content[1].data == "aW1hZ2U="
    assert converted.content[1].mime_type == "image/png"
    text = "\n".join(block.text for block in converted.content if isinstance(block, TextContent))
    assert "manual" in text and "https://example.test/manual" in text
    assert "embedded text" in text and "file:///note.txt" in text
    assert "application/octet-stream" in text and "3 bytes" in text
    assert "audio/wav" in text and "4 bytes" in text
    assert converted.details["travis234Mcp"]["isError"] is False


def test_structured_content_is_synthesized_only_without_equivalent_text(tmp_path: Path) -> None:
    spills = SpillRegistry(tmp_path)
    synthesized = convert_call_result(
        CallToolResult(content=[], structuredContent={"count": 2}),
        spills,
    )
    equivalent = convert_call_result(
        CallToolResult(
            content=[McpTextContent(type="text", text='{"count": 2}')],
            structuredContent={"count": 2},
        ),
        spills,
    )

    assert [block.text for block in synthesized.content] == ['{"count":2}']
    assert [block.text for block in equivalent.content] == ['{"count": 2}']


def test_is_error_is_preserved_only_in_adapter_details(tmp_path: Path) -> None:
    converted = convert_call_result(
        CallToolResult(
            content=[McpTextContent(type="text", text="remote failure")],
            isError=True,
        ),
        SpillRegistry(tmp_path),
    )

    assert converted.details == {
        "travis234Mcp": {
            "operation": "call",
            "isError": True,
            "hasStructuredContent": False,
            "spilled": False,
        }
    }


def test_aggregate_text_guard_cannot_be_bypassed_by_many_blocks(tmp_path: Path) -> None:
    spills = SpillRegistry(tmp_path)
    result = CallToolResult(
        content=[
            McpTextContent(type="text", text="x" * (MAX_INLINE_BYTES // 2)),
            McpImageContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
            McpTextContent(type="text", text="y" * (MAX_INLINE_BYTES // 2 + 10)),
        ]
    )

    converted = convert_call_result(result, spills)

    text_blocks = [block for block in converted.content if isinstance(block, TextContent)]
    image_blocks = [block for block in converted.content if isinstance(block, ImageContent)]
    assert len(text_blocks) == 1
    assert len(image_blocks) == 1
    assert converted.details["travis234Mcp"]["spilled"] is True
    spill_path = Path(converted.details["travis234Mcp"]["spillPath"])
    assert spill_path.is_file()
    assert len(spill_path.read_text(encoding="utf-8")) > MAX_INLINE_BYTES


def test_result_limits_image_count_and_aggregate_decoded_bytes(tmp_path: Path) -> None:
    image = McpImageContent(
        type="image",
        data=_b64(b"x" * (3 * 1024 * 1024)),
        mimeType="image/png",
    )
    result = convert_call_result(CallToolResult(content=[image] * 9), SpillRegistry(tmp_path))

    images = [item for item in result.content if isinstance(item, ImageContent)]
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert len(images) == 6
    assert "image limit" in text
    assert result.details["travis234Mcp"]["acceptedImages"] == 6
    assert result.details["travis234Mcp"]["rejectedImages"] == 3


def test_result_rejects_one_oversized_image_without_exposing_data(tmp_path: Path) -> None:
    encoded = _b64(b"s" * (MAX_IMAGE_BYTES + 1))
    result = convert_call_result(
        CallToolResult(
            content=[McpImageContent(type="image", data=encoded, mimeType="image/png")]
        ),
        SpillRegistry(tmp_path),
    )

    assert not any(isinstance(item, ImageContent) for item in result.content)
    assert "10 MiB" in result.content[0].text
    assert encoded[:100] not in result.content[0].text


def test_result_rejects_malformed_base64_and_unsupported_image_mime(tmp_path: Path) -> None:
    result = convert_call_result(
        CallToolResult(
            content=[
                McpImageContent(type="image", data="not base64!", mimeType="image/png"),
                McpImageContent(type="image", data=_b64(b"image"), mimeType="image/svg+xml"),
            ]
        ),
        SpillRegistry(tmp_path),
    )

    assert not any(isinstance(item, ImageContent) for item in result.content)
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert "malformed base64" in text
    assert "unsupported MIME type" in text
    assert "not base64" not in text


def test_result_accepts_exact_image_count_and_aggregate_boundaries(tmp_path: Path) -> None:
    small = McpImageContent(type="image", data=_b64(b"x"), mimeType="image/gif")
    count_result = convert_call_result(
        CallToolResult(content=[small] * MAX_IMAGE_BLOCKS),
        SpillRegistry(tmp_path),
    )
    ten_mib = McpImageContent(
        type="image",
        data=_b64(b"x" * MAX_IMAGE_BYTES),
        mimeType="image/webp",
    )
    aggregate_result = convert_call_result(
        CallToolResult(content=[ten_mib, ten_mib]),
        SpillRegistry(tmp_path),
    )

    assert len([item for item in count_result.content if isinstance(item, ImageContent)]) == 8
    assert len([item for item in aggregate_result.content if isinstance(item, ImageContent)]) == 2
    assert aggregate_result.details["travis234Mcp"]["imageBytes"] == MAX_RESULT_IMAGE_BYTES


def test_result_preserves_text_around_accepted_and_rejected_images(tmp_path: Path) -> None:
    result = convert_call_result(
        CallToolResult(
            content=[
                McpTextContent(type="text", text="before"),
                McpImageContent(type="image", data=_b64(b"image"), mimeType="image/jpeg"),
                McpImageContent(type="image", data=_b64(b"vector"), mimeType="image/svg+xml"),
                McpTextContent(type="text", text="after"),
            ],
            structuredContent={"ok": True},
        ),
        SpillRegistry(tmp_path),
    )

    assert len([item for item in result.content if isinstance(item, ImageContent)]) == 1
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert "before" in text
    assert "unsupported MIME type" in text
    assert "after" in text
    assert '{"ok":true}' in text
