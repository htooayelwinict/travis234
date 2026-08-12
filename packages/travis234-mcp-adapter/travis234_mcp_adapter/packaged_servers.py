"""Process-local registry for trusted, package-owned MCP servers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import os
from pathlib import Path
from types import MappingProxyType

from travis234_mcp_adapter.config import LoadedConfig, ServerConfig


@dataclass(frozen=True)
class PackagedServer:
    """An immutable stdio server executable owned by one installed package."""

    name: str
    package_root: Path
    command: Path
    args: tuple[str, ...] = ("mcp",)
    request_timeout_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.package_root.is_absolute() or not self.command.is_absolute():
            raise ValueError("Packaged MCP server paths must be absolute")
        root = self.package_root.resolve()
        command = self.command.resolve()
        try:
            command.relative_to(root)
        except ValueError as error:
            raise ValueError(
                "Packaged MCP server command must be inside package root"
            ) from error
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError("Packaged MCP server name must be non-empty and trimmed")
        if not command.is_file() or not os.access(command, os.X_OK):
            raise ValueError("Packaged MCP server command must be an executable file")
        if not isinstance(self.args, tuple) or not all(
            isinstance(value, str) for value in self.args
        ):
            raise ValueError("Packaged MCP server args must be a tuple of strings")
        if self.request_timeout_ms is not None and (
            isinstance(self.request_timeout_ms, bool)
            or not isinstance(self.request_timeout_ms, int)
            or self.request_timeout_ms <= 0
        ):
            raise ValueError(
                "Packaged MCP request timeout must be a positive integer"
            )
        object.__setattr__(self, "package_root", root)
        object.__setattr__(self, "command", command)


_REGISTRY: dict[str, PackagedServer] = {}


@dataclass(frozen=True)
class PackagedConfig:
    """A loaded config with package-owned servers applied."""

    config: LoadedConfig
    shadowed_configured_names: tuple[str, ...]


def register_packaged_server(server: PackagedServer) -> None:
    """Register a package-owned server, tolerating an identical repeat."""

    existing = _REGISTRY.get(server.name)
    if existing is None:
        _REGISTRY[server.name] = server
        return
    if existing != server:
        raise ValueError(f'Packaged MCP server "{server.name}" is already registered')


def get_packaged_servers() -> Mapping[str, PackagedServer]:
    """Return an immutable, sorted snapshot of registered servers."""

    return MappingProxyType(dict(sorted(_REGISTRY.items())))


def merge_packaged_servers(config: LoadedConfig) -> PackagedConfig:
    """Overlay registered package-owned servers without changing source files."""

    packaged = get_packaged_servers()
    servers = dict(config.servers)
    shadowed = tuple(sorted(set(servers).intersection(packaged)))
    for descriptor in packaged.values():
        servers[descriptor.name] = ServerConfig(
            name=descriptor.name,
            source_path=descriptor.command,
            command=str(descriptor.command),
            args=descriptor.args,
            request_timeout_ms=descriptor.request_timeout_ms,
        )
    return PackagedConfig(
        config=replace(config, servers=MappingProxyType(servers)),
        shadowed_configured_names=shadowed,
    )


__all__ = [
    "PackagedConfig",
    "PackagedServer",
    "get_packaged_servers",
    "merge_packaged_servers",
    "register_packaged_server",
]
