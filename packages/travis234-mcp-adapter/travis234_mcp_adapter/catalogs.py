"""Bounded MCP resource and prompt catalog loading."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp.types import Prompt, Resource, ResourceTemplate

if TYPE_CHECKING:
    from travis.agent.types import AbortSignal
    from travis234_mcp_adapter.runtime import ConnectedServer


MAX_CATALOG_PAGES = 100
MAX_RESOURCE_PROMPT_ENTRIES = 5_000
MAX_SEARCH_RESULTS = 20


class McpProtocolError(RuntimeError):
    """Bounded adapter-authored protocol failure safe to return to the model."""


@dataclass(frozen=True)
class ResourceCatalog:
    generation: int
    resources: tuple[Resource, ...]
    templates: tuple[ResourceTemplate, ...]
    references: dict[str, str]


@dataclass(frozen=True)
class PromptCatalog:
    generation: int
    prompts: tuple[Prompt, ...]
    by_name: dict[str, Prompt]


async def load_resource_catalog(
    connected: ConnectedServer,
    signal: AbortSignal | None,
    *,
    generation: int,
) -> ResourceCatalog:
    resources: list[Resource] = []
    templates: list[ResourceTemplate] = []
    await _drain_resource_pages(
        connected,
        signal,
        resources,
        templates,
        templates_only=False,
    )
    await _drain_resource_pages(
        connected,
        signal,
        resources,
        templates,
        templates_only=True,
    )
    seen_uris: set[str] = set()
    seen_names: set[str] = set()
    references: dict[str, str] = {}
    for resource in resources:
        uri = str(resource.uri)
        if uri in seen_uris or resource.name in seen_names:
            raise McpProtocolError(
                "MCP resource catalog contains ambiguous duplicate entries"
            )
        seen_uris.add(uri)
        seen_names.add(resource.name)
        reference = _new_resource_reference(references)
        references[reference] = uri
    template_names: set[str] = set()
    for template in templates:
        if template.name in template_names or template.name in seen_names:
            raise McpProtocolError(
                "MCP resource catalog contains ambiguous duplicate entries"
            )
        template_names.add(template.name)
    return ResourceCatalog(
        generation=generation,
        resources=tuple(resources),
        templates=tuple(templates),
        references=references,
    )


async def _drain_resource_pages(
    connected: ConnectedServer,
    signal: AbortSignal | None,
    resources: list[Resource],
    templates: list[ResourceTemplate],
    *,
    templates_only: bool,
) -> None:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for page_number in range(1, MAX_CATALOG_PAGES + 1):
        if templates_only:
            page = await connected.list_resource_templates(signal, cursor=cursor)
            templates.extend(page.resource_templates)
        else:
            page = await connected.list_resources(signal, cursor=cursor)
            resources.extend(page.resources)
        if len(resources) + len(templates) > MAX_RESOURCE_PROMPT_ENTRIES:
            raise McpProtocolError(
                f"MCP resource catalog exceeded {MAX_RESOURCE_PROMPT_ENTRIES:,} entries"
            )
        next_cursor = page.next_cursor
        if next_cursor is None:
            return
        if next_cursor in seen_cursors:
            raise McpProtocolError(
                "MCP resource catalog returned a repeated pagination cursor"
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_number == MAX_CATALOG_PAGES:
            raise McpProtocolError(
                f"MCP resource catalog exceeded {MAX_CATALOG_PAGES} pages"
            )
    raise McpProtocolError("MCP resource catalog pagination did not terminate")


def _new_resource_reference(existing: dict[str, str]) -> str:
    for _attempt in range(128):
        candidate = f"mcp-resource-{secrets.token_hex(16)}"
        if candidate not in existing:
            return candidate
    raise McpProtocolError("MCP resource reference allocation failed")


async def load_prompt_catalog(
    connected: ConnectedServer,
    signal: AbortSignal | None,
    *,
    generation: int,
) -> PromptCatalog:
    prompts: list[Prompt] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for page_number in range(1, MAX_CATALOG_PAGES + 1):
        page = await connected.list_prompts(signal, cursor=cursor)
        prompts.extend(page.prompts)
        if len(prompts) > MAX_RESOURCE_PROMPT_ENTRIES:
            raise McpProtocolError(
                f"MCP prompt catalog exceeded {MAX_RESOURCE_PROMPT_ENTRIES:,} entries"
            )
        next_cursor = page.next_cursor
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            raise McpProtocolError(
                "MCP prompt catalog returned a repeated pagination cursor"
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_number == MAX_CATALOG_PAGES:
            raise McpProtocolError(
                f"MCP prompt catalog exceeded {MAX_CATALOG_PAGES} pages"
            )
    else:
        raise McpProtocolError("MCP prompt catalog pagination did not terminate")
    by_name: dict[str, Prompt] = {}
    for prompt in prompts:
        if prompt.name in by_name:
            raise McpProtocolError(
                "MCP prompt catalog contains ambiguous duplicate entries"
            )
        argument_names: set[str] = set()
        for argument in prompt.arguments or ():
            if argument.name in argument_names:
                raise McpProtocolError(
                    "MCP prompt catalog contains ambiguous duplicate arguments"
                )
            argument_names.add(argument.name)
        by_name[prompt.name] = prompt
    return PromptCatalog(generation, tuple(prompts), by_name)


__all__ = [
    "MAX_CATALOG_PAGES",
    "MAX_RESOURCE_PROMPT_ENTRIES",
    "MAX_SEARCH_RESULTS",
    "McpProtocolError",
    "PromptCatalog",
    "ResourceCatalog",
    "load_prompt_catalog",
    "load_resource_catalog",
]
