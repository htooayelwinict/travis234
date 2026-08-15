from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent as McpImageContent,
    Prompt,
    PromptMessage,
    Resource,
    ResourceTemplate,
    ResourceLink,
    TextContent as McpTextContent,
    TextResourceContents,
)
from travis.agent.types import AgentToolResult
from travis.ai.types import ImageContent, TextContent
from travis234_mcp_adapter.output_guard import OutputGuard, SpillRegistry
from travis234_mcp_adapter.catalogs import (
    MAX_SEARCH_RESULTS,
    McpProtocolError,
    PromptCatalog,
    ResourceCatalog,
)

if TYPE_CHECKING:
    from travis.agent.types import AbortSignal
    from travis234_mcp_adapter.runtime import ConnectedServer


MAX_RAW_RESOURCE_PROMPT_BYTES = 8 * 1024 * 1024


def convert_call_result(result: CallToolResult, spills: SpillRegistry) -> AgentToolResult:
    converted: list[TextContent | ImageContent] = []
    source_texts: list[str] = []
    for block in result.content:
        if isinstance(block, McpTextContent):
            source_texts.append(block.text)
            converted.append(TextContent(text=block.text))
        elif isinstance(block, McpImageContent):
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


def resource_list_result(
    server: str,
    catalog: ResourceCatalog,
    query: str | None,
    spills: SpillRegistry,
) -> AgentToolResult:
    reference_by_uri = {uri: reference for reference, uri in catalog.references.items()}
    resources = list(catalog.resources)
    templates = list(catalog.templates)
    if query is not None:
        needle = query.casefold()
        resources = [
            item
            for item in resources
            if needle
            in " ".join(
                (item.name, str(item.title or ""), str(item.description or ""))
            ).casefold()
        ][:MAX_SEARCH_RESULTS]
        templates = [
            item
            for item in templates
            if needle
            in " ".join(
                (item.name, str(item.title or ""), str(item.description or ""))
            ).casefold()
        ][: max(0, MAX_SEARCH_RESULTS - len(resources))]
    lines = [
        f'MCP resources on "{server}" '
        f"({len(catalog.resources)} resources, {len(catalog.templates)} templates)"
    ]
    lines.extend(
        f"- {reference_by_uri[str(item.uri)]} {item.name}: {_resource_description(item)}"
        for item in resources
    )
    lines.extend(
        f"- template {item.name}: {_resource_description(item)}"
        for item in templates
    )
    return _guarded_result(
        "\n".join(lines),
        operation="resources.list",
        server=server,
        spills=spills,
        marker_fields={
            "resourceCount": len(catalog.resources),
            "templateCount": len(catalog.templates),
        },
    )


def _resource_description(resource: Resource | ResourceTemplate) -> str:
    description = " ".join(str(resource.description or "No description").split())
    mime = str(resource.mime_type or "unknown MIME type")
    size = getattr(resource, "size", None)
    size_text = f", {size} bytes" if isinstance(size, int) and size >= 0 else ""
    bounded = description[:240] + ("…" if len(description) > 240 else "")
    return f"{bounded} ({mime}{size_text})"


async def resource_read_result(
    connected: ConnectedServer,
    server: str,
    catalog: ResourceCatalog,
    reference: str,
    signal: AbortSignal | None,
    spills: SpillRegistry,
) -> AgentToolResult:
    uri = catalog.references[reference]
    response = await connected.read_resource(uri, signal)
    raw_bytes = 0
    text_blocks: list[str] = []
    blob_paths: list[str] = []
    for content in response.contents:
        mime = str(content.mime_type or "unknown MIME type")
        if isinstance(content, TextResourceContents):
            raw_bytes += len(content.text.encode("utf-8"))
            text_blocks.append(
                "\n".join(
                    (
                        "[Untrusted MCP resource data]",
                        f"reference={reference} mime={mime}",
                        content.text,
                        "[/Untrusted MCP resource data]",
                    )
                )
            )
        elif isinstance(content, BlobResourceContents):
            try:
                decoded = base64.b64decode(content.blob, validate=True)
            except (TypeError, ValueError) as error:
                raise McpProtocolError(
                    "MCP resource returned invalid blob data"
                ) from error
            raw_bytes += len(decoded)
            path = spills.write_bytes(decoded)
            blob_paths.append(str(path))
            text_blocks.append(
                "\n".join(
                    (
                        "[Untrusted MCP resource data]",
                        f"reference={reference} mime={mime} bytes={len(decoded)}",
                        f"Binary content retained as {path.name}.",
                        "[/Untrusted MCP resource data]",
                    )
                )
            )
        else:
            raise McpProtocolError(
                f"MCP resource returned unsupported content ({type(content).__name__})"
            )
        if raw_bytes > MAX_RAW_RESOURCE_PROMPT_BYTES:
            for path in blob_paths:
                Path(path).unlink(missing_ok=True)
            raise McpProtocolError("MCP resource response exceeded 8 MiB")
    result = _guarded_result(
        "\n\n".join(text_blocks) or "MCP resource returned no content.",
        operation="resources.read",
        server=server,
        spills=spills,
        marker_fields={"resource": reference, "rawBytes": raw_bytes},
    )
    _attach_blob_spills(result, blob_paths)
    return result


def prompt_list_result(
    server: str,
    catalog: PromptCatalog,
    query: str | None,
    spills: SpillRegistry,
) -> AgentToolResult:
    prompts = list(catalog.prompts)
    if query is not None:
        needle = query.casefold()
        prompts = [
            prompt
            for prompt in prompts
            if needle
            in " ".join(
                (prompt.name, str(prompt.title or ""), str(prompt.description or ""))
            ).casefold()
        ][:MAX_SEARCH_RESULTS]
    lines = [f'MCP prompts on "{server}" ({len(catalog.prompts)} prompts)']
    for prompt in prompts:
        arguments = ", ".join(
            f"{argument.name}({'required' if argument.required else 'optional'})"
            for argument in (prompt.arguments or ())
        ) or "none"
        lines.append(
            f"- {prompt.name}: {_prompt_description(prompt)}; arguments={arguments}"
        )
    return _guarded_result(
        "\n".join(lines),
        operation="prompts.list",
        server=server,
        spills=spills,
        marker_fields={"promptCount": len(catalog.prompts)},
    )


def _prompt_description(prompt: Prompt) -> str:
    description = " ".join(str(prompt.description or "No description").split())
    return description[:240] + ("…" if len(description) > 240 else "")


async def prompt_get_result(
    connected: ConnectedServer,
    server: str,
    catalog: PromptCatalog,
    name: str,
    arguments: dict[str, object],
    signal: AbortSignal | None,
    spills: SpillRegistry,
) -> AgentToolResult:
    prompt = catalog.by_name.get(name)
    if prompt is None:
        return _mcp_result(
            f'Unknown MCP prompt "{name}" on "{server}"; list prompts first.',
            operation="prompts.get",
            server=server,
            is_error=True,
        )
    definitions = {argument.name: argument for argument in (prompt.arguments or ())}
    if set(arguments).difference(definitions):
        return _mcp_result(
            "Unknown argument for MCP prompt; list prompts for accepted names.",
            operation="prompts.get",
            server=server,
            is_error=True,
        )
    missing = sorted(
        argument.name
        for argument in definitions.values()
        if argument.required and argument.name not in arguments
    )
    if missing:
        return _mcp_result(
            "Missing required MCP prompt argument; list prompts for required names.",
            operation="prompts.get",
            server=server,
            is_error=True,
        )
    response = await connected.get_prompt(
        name,
        {key: str(value) for key, value in arguments.items()},
        signal,
    )
    if len(response.messages) > 100:
        raise McpProtocolError("MCP prompt response exceeded 100 messages")
    raw_bytes = 0
    blocks: list[str] = []
    blob_paths: list[str] = []
    for index, message in enumerate(response.messages):
        rendered, size, paths = _prompt_message_data(message, spills)
        raw_bytes += size
        blob_paths.extend(paths)
        if raw_bytes > MAX_RAW_RESOURCE_PROMPT_BYTES:
            for path in blob_paths:
                Path(path).unlink(missing_ok=True)
            raise McpProtocolError("MCP prompt response exceeded 8 MiB")
        blocks.append(
            "\n".join(
                (
                    "[Untrusted MCP prompt data]",
                    f"message={index + 1} role={message.role}",
                    rendered,
                    "[/Untrusted MCP prompt data]",
                )
            )
        )
    result = _guarded_result(
        "\n\n".join(blocks) or "MCP prompt returned no messages.",
        operation="prompts.get",
        server=server,
        spills=spills,
        marker_fields={
            "prompt": name,
            "messageCount": len(response.messages),
            "rawBytes": raw_bytes,
        },
    )
    _attach_blob_spills(result, blob_paths)
    return result


def _prompt_message_data(
    message: PromptMessage,
    spills: SpillRegistry,
) -> tuple[str, int, list[str]]:
    content = message.content
    if isinstance(content, McpTextContent):
        return content.text, len(content.text.encode("utf-8")), []
    if isinstance(content, EmbeddedResource):
        resource = content.resource
        mime = str(resource.mime_type or "unknown MIME type")
        if isinstance(resource, TextResourceContents):
            return (
                f"embedded mime={mime}\n{resource.text}",
                len(resource.text.encode("utf-8")),
                [],
            )
        if isinstance(resource, BlobResourceContents):
            try:
                decoded = base64.b64decode(resource.blob, validate=True)
            except (TypeError, ValueError) as error:
                raise McpProtocolError(
                    "MCP prompt returned invalid embedded blob data"
                ) from error
            path = spills.write_bytes(decoded)
            return (
                f"embedded mime={mime} bytes={len(decoded)} retained={path.name}",
                len(decoded),
                [str(path)],
            )
    raise McpProtocolError(
        f"MCP prompt returned unsupported content ({type(content).__name__})"
    )


def _guarded_result(
    text: str,
    *,
    operation: str,
    server: str,
    spills: SpillRegistry,
    marker_fields: dict[str, object] | None = None,
) -> AgentToolResult:
    guarded = OutputGuard(spills).guard(text)
    fields = dict(marker_fields or {})
    fields["spilled"] = guarded.spill_path is not None
    if guarded.spill_path is not None:
        fields["spillPath"] = str(guarded.spill_path)
        fields["truncatedBy"] = guarded.truncated_by
    return _mcp_result(
        guarded.text,
        operation=operation,
        server=server,
        is_error=False,
        marker_fields=fields,
    )


def _attach_blob_spills(result: AgentToolResult, paths: list[str]) -> None:
    if not paths:
        return
    marker = result.details["travis234Mcp"]
    marker["spilled"] = True
    marker["spillPath"] = paths[0]
    if len(paths) > 1:
        marker["spillPaths"] = paths


def _mcp_result(
    text: str,
    *,
    operation: str,
    server: str,
    is_error: bool,
    marker_fields: dict[str, object] | None = None,
) -> AgentToolResult:
    marker: dict[str, object] = {
        "operation": operation,
        "server": server,
        "isError": is_error,
    }
    if marker_fields:
        marker.update(marker_fields)
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"travis234Mcp": marker},
    )


__all__ = [
    "convert_call_result",
    "prompt_get_result",
    "prompt_list_result",
    "resource_list_result",
    "resource_read_result",
]
