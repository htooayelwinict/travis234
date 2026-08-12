from __future__ import annotations

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
from travis234_mcp_adapter.results import convert_call_result


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
