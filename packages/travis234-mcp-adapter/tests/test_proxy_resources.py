from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import (
    BlobResourceContents,
    ListResourceTemplatesResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextResourceContents,
)

from travis234_mcp_adapter.config import LoadedConfig, ServerConfig
from travis234_mcp_adapter.catalogs import (
    MAX_CATALOG_PAGES,
    MAX_RESOURCE_PROMPT_ENTRIES,
    load_resource_catalog,
)
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.proxy_tool import (
    dispatch_proxy,
)


def _resource(name: str, uri: str, description: str = "") -> Resource:
    return Resource(
        name=name,
        uri=uri,
        description=description,
        mimeType="text/plain",
    )


class FakeConnected:
    def __init__(
        self,
        *,
        resource_pages=None,
        template_pages=None,
        read_result: ReadResourceResult | None = None,
    ) -> None:
        self.resource_pages = resource_pages or {
            None: ListResourcesResult(
                resources=[
                    _resource(
                        "private-manual",
                        "https://user:password@example.test/manual?token=hidden",
                        "Private manual",
                    )
                ]
            )
        }
        self.template_pages = template_pages or {
            None: ListResourceTemplatesResult(resourceTemplates=[])
        }
        self.read_result = read_result or ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri="https://user:password@example.test/manual?token=hidden",
                    mimeType="text/plain",
                    text="server supplied instructions",
                )
            ]
        )
        self.resource_cursors: list[str | None] = []
        self.template_cursors: list[str | None] = []
        self.read_uris: list[str] = []

    async def list_resources(self, _signal, cursor=None):
        self.resource_cursors.append(cursor)
        source = self.resource_pages
        return source(cursor) if callable(source) else source[cursor]

    async def list_resource_templates(self, _signal, cursor=None):
        self.template_cursors.append(cursor)
        source = self.template_pages
        return source(cursor) if callable(source) else source[cursor]

    async def read_resource(self, uri, _signal):
        self.read_uris.append(uri)
        return self.read_result


class FakeRuntime:
    def __init__(self, connected_by_name: dict[str, FakeConnected]) -> None:
        self.connected_by_name = connected_by_name
        self.connects: list[str] = []

    async def connect(self, name, _signal):
        self.connects.append(name)
        return self.connected_by_name[name]

    def is_connected(self, name: str) -> bool:
        return name in self.connects


def _state(tmp_path: Path, connected_by_name=None):
    connections = connected_by_name or {"docs": FakeConnected()}
    servers = {
        name: ServerConfig(
            name=name,
            source_path=Path(f"/fixture/{name}.json"),
            command="fixture",
        )
        for name in connections
    }
    return SimpleNamespace(
        config=LoadedConfig(servers=servers, sources=(), ignored_project_sources=()),
        config_error=None,
        runtime=FakeRuntime(connections),
        catalogs={},
        resource_catalogs={},
        prompt_catalogs={},
        spills=SpillRegistry(tmp_path),
        generation=7,
        shadowed_configured_names=(),
    )


@pytest.mark.anyio
async def test_resource_listing_uses_opaque_generation_scoped_references(
    tmp_path: Path,
) -> None:
    connected = FakeConnected(
        resource_pages={
            None: ListResourcesResult(
                resources=[
                    _resource(
                        "manual",
                        "https://user:password@example.test/manual?token=hidden",
                        "Private manual",
                    )
                ],
                nextCursor="next",
            ),
            "next": ListResourcesResult(
                resources=[_resource("guide", "file:///private/guide.txt")]
            ),
        },
        template_pages={
            None: ListResourceTemplatesResult(
                resourceTemplates=[
                    ResourceTemplate(
                        name="issue",
                        uriTemplate="https://user:secret@example.test/issues/{id}?token=hidden",
                        description="Issue template",
                        mimeType="text/plain",
                    )
                ]
            )
        },
    )
    state = _state(tmp_path, {"docs": connected})

    result = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    rendered = result.content[0].text
    references = re.findall(r"mcp-resource-[0-9a-f]{32}", rendered)

    assert len(references) == 2
    assert len(set(references)) == 2
    assert connected.resource_cursors == [None, "next"]
    assert connected.template_cursors == [None]
    assert "manual" in rendered and "guide" in rendered
    assert "template issue" in rendered
    assert "user:password" not in rendered
    assert "token=hidden" not in rendered
    assert "file:///private" not in rendered
    assert result.details["travis234Mcp"]["operation"] == "resources.list"


@pytest.mark.anyio
async def test_resource_reference_collision_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = iter(["a" * 32, "a" * 32, "b" * 32])
    monkeypatch.setattr(
        "travis234_mcp_adapter.catalogs.secrets.token_hex",
        lambda _size: next(tokens),
    )
    connected = FakeConnected(
        resource_pages={
            None: ListResourcesResult(
                resources=[
                    _resource("one", "file:///one"),
                    _resource("two", "file:///two"),
                ]
            )
        }
    )
    state = _state(tmp_path, {"docs": connected})

    result = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )

    assert "mcp-resource-" + "a" * 32 in result.content[0].text
    assert "mcp-resource-" + "b" * 32 in result.content[0].text


@pytest.mark.anyio
async def test_resource_read_resolves_only_internal_reference_and_labels_content(
    tmp_path: Path,
) -> None:
    connected = FakeConnected()
    state = _state(tmp_path, {"docs": connected})
    listed = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    reference = re.search(r"mcp-resource-[0-9a-f]{32}", listed.content[0].text).group()

    result = await dispatch_proxy(
        state,
        {
            "server": "docs",
            "operation": "resources.read",
            "resource": reference,
        },
        None,
    )
    rendered = result.content[0].text

    assert connected.read_uris == [
        "https://user:password@example.test/manual?token=hidden"
    ]
    assert rendered.count("[Untrusted MCP resource data]") == 1
    assert "server supplied instructions" in rendered
    assert "user:password" not in rendered
    assert "token=hidden" not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.anyio
async def test_resource_reference_is_rejected_for_other_server_or_generation(
    tmp_path: Path,
) -> None:
    docs = FakeConnected()
    other = FakeConnected()
    state = _state(tmp_path, {"docs": docs, "other": other})
    listed = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    reference = re.search(r"mcp-resource-[0-9a-f]{32}", listed.content[0].text).group()

    foreign = await dispatch_proxy(
        state,
        {"server": "other", "operation": "resources.read", "resource": reference},
        None,
    )
    state.generation += 1
    stale = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.read", "resource": reference},
        None,
    )

    assert foreign.details["travis234Mcp"]["isError"] is True
    assert stale.details["travis234Mcp"]["isError"] is True
    assert docs.read_uris == []
    assert other.read_uris == []


@pytest.mark.anyio
async def test_resource_pagination_rejects_repetition_page_and_entry_bounds() -> None:
    repeated = FakeConnected(
        resource_pages={
            None: ListResourcesResult(resources=[], nextCursor="same"),
            "same": ListResourcesResult(resources=[], nextCursor="same"),
        }
    )
    with pytest.raises(RuntimeError, match="repeated pagination cursor"):
        await load_resource_catalog(repeated, None, generation=1)

    def endless(cursor):
        page = 0 if cursor is None else int(cursor)
        return ListResourcesResult(resources=[], nextCursor=str(page + 1))

    with pytest.raises(RuntimeError, match=f"{MAX_CATALOG_PAGES} pages"):
        await load_resource_catalog(FakeConnected(resource_pages=endless), None, generation=1)

    too_many = FakeConnected(
        resource_pages={
            None: ListResourcesResult(
                resources=[
                    _resource(f"resource-{index}", f"file:///{index}")
                    for index in range(MAX_RESOURCE_PROMPT_ENTRIES + 1)
                ]
            )
        }
    )
    with pytest.raises(RuntimeError, match=f"{MAX_RESOURCE_PROMPT_ENTRIES:,} entries"):
        await load_resource_catalog(too_many, None, generation=1)


@pytest.mark.anyio
async def test_duplicate_resource_uri_is_rejected_as_ambiguous(tmp_path: Path) -> None:
    connected = FakeConnected(
        resource_pages={
            None: ListResourcesResult(
                resources=[
                    _resource("one", "file:///same"),
                    _resource("two", "file:///same"),
                ]
            )
        }
    )
    state = _state(tmp_path, {"docs": connected})

    result = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )

    assert result.details["travis234Mcp"]["isError"] is True
    assert "ambiguous" in result.content[0].text.lower()


@pytest.mark.anyio
async def test_blob_resource_spills_securely_and_cleanup_removes_owned_file(
    tmp_path: Path,
) -> None:
    blob = base64.b64encode(b"binary-private-value").decode("ascii")
    connected = FakeConnected(
        read_result=ReadResourceResult(
            contents=[
                BlobResourceContents(
                    uri="file:///private.bin",
                    mimeType="application/octet-stream",
                    blob=blob,
                )
            ]
        )
    )
    state = _state(tmp_path, {"docs": connected})
    listed = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    reference = re.search(r"mcp-resource-[0-9a-f]{32}", listed.content[0].text).group()

    result = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.read", "resource": reference},
        None,
    )
    marker = result.details["travis234Mcp"]
    spill_path = Path(marker["spillPath"])

    assert marker["spilled"] is True
    assert spill_path.read_bytes() == b"binary-private-value"
    assert str(spill_path) not in result.content[0].text
    state.spills.cleanup()
    assert not spill_path.exists()


@pytest.mark.anyio
async def test_resource_response_has_raw_eight_mib_boundary(tmp_path: Path) -> None:
    connected = FakeConnected(
        read_result=ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri="file:///large",
                    text="x" * (8 * 1024 * 1024 + 1),
                )
            ]
        )
    )
    state = _state(tmp_path, {"docs": connected})
    listed = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    reference = re.search(r"mcp-resource-[0-9a-f]{32}", listed.content[0].text).group()

    result = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.read", "resource": reference},
        None,
    )

    assert result.details["travis234Mcp"]["isError"] is True
    assert "8 MiB" in result.content[0].text
    assert "x" * 100 not in result.content[0].text


@pytest.mark.anyio
async def test_large_resource_catalog_spills_without_rendering_host_path(
    tmp_path: Path,
) -> None:
    connected = FakeConnected(
        resource_pages={
            None: ListResourcesResult(
                resources=[
                    _resource(
                        f"resource-{index}",
                        f"file:///{index}",
                        "description-" + "x" * 220,
                    )
                    for index in range(300)
                ]
            )
        }
    )
    state = _state(tmp_path, {"docs": connected})

    result = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    marker = result.details["travis234Mcp"]
    spill_path = Path(marker["spillPath"])

    assert marker["spilled"] is True
    assert spill_path.is_file()
    assert str(spill_path) not in result.content[0].text
    assert str(tmp_path) not in result.content[0].text
    state.spills.cleanup()
    assert not spill_path.exists()


@pytest.mark.anyio
async def test_resource_read_cancellation_and_stale_completion_are_not_returned(
    tmp_path: Path,
) -> None:
    release = asyncio.Event()

    class Delayed(FakeConnected):
        async def read_resource(self, uri, signal):
            await release.wait()
            return await super().read_resource(uri, signal)

    connected = Delayed()
    state = _state(tmp_path, {"docs": connected})
    listed = await dispatch_proxy(
        state,
        {"server": "docs", "operation": "resources.list"},
        None,
    )
    reference = re.search(r"mcp-resource-[0-9a-f]{32}", listed.content[0].text).group()
    call = asyncio.create_task(
        dispatch_proxy(
            state,
            {"server": "docs", "operation": "resources.read", "resource": reference},
            None,
        )
    )
    await asyncio.sleep(0)
    state.generation += 1
    release.set()

    with pytest.raises(RuntimeError, match="generation"):
        await call
