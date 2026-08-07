from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from types import MappingProxyType


_SERVER_FIELDS = {
    "command",
    "args",
    "cwd",
    "env",
    "url",
    "headers",
    "requestTimeoutMs",
}
_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization"}
_SENSITIVE_ENV_MARKERS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "OAUTH", "CREDENTIAL")
_BRACED_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXACT_ENV = re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]*)\Z")


class ConfigError(ValueError):
    """A source-attributed MCP configuration error."""


@dataclass(frozen=True)
class ServerConfig:
    name: str
    source_path: Path
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    request_timeout_ms: int | None = None


@dataclass(frozen=True)
class LoadedConfig:
    servers: Mapping[str, ServerConfig]
    sources: tuple[Path, ...]
    ignored_project_sources: tuple[Path, ...]


@dataclass(frozen=True, repr=False)
class ResolvedServer:
    name: str
    source_path: Path
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    env: Mapping[str, str]
    url: str | None
    headers: Mapping[str, str]
    request_timeout_ms: int | None

    @property
    def transport(self) -> str:
        return "stdio" if self.command is not None else "streamable-http"

    def __repr__(self) -> str:
        return (
            "ResolvedServer("
            f"name={self.name!r}, source_path={self.source_path!r}, "
            f"transport={self.transport!r}, request_timeout_ms={self.request_timeout_ms!r})"
        )


def load_config(cwd: Path, home: Path, project_trusted: bool) -> LoadedConfig:
    resolved_cwd = Path(cwd).expanduser().resolve()
    resolved_home = Path(home).expanduser().resolve()
    global_sources = (
        resolved_home / ".config" / "mcp" / "mcp.json",
        resolved_home / ".travis234" / "agent" / "mcp.json",
    )
    project_sources = (
        resolved_cwd / ".mcp.json",
        resolved_cwd / ".travis234" / "mcp.json",
    )
    ignored = tuple(path for path in project_sources if path.is_file()) if not project_trusted else ()
    authorized = global_sources + (project_sources if project_trusted else ())
    servers: dict[str, ServerConfig] = {}
    loaded_sources: list[Path] = []

    for source_path in authorized:
        if not source_path.is_file():
            continue
        payload = _read_json(source_path)
        parsed = _parse_source(source_path, payload)
        servers.update(parsed)
        loaded_sources.append(source_path)

    return LoadedConfig(
        servers=MappingProxyType(servers),
        sources=tuple(loaded_sources),
        ignored_project_sources=ignored,
    )


def resolve_server(server: ServerConfig, environ: Mapping[str, str]) -> ResolvedServer:
    resolved_env: dict[str, str] = {}
    for key, value in server.env.items():
        if any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS) and not _contains_reference(value):
            raise _error(server.source_path, server.name, f"env.{key}", "must use an environment reference")
        resolved_env[key] = _resolve_value(value, environ, server, f"env.{key}")
    resolved_headers: dict[str, str] = {}
    for key, value in server.headers.items():
        if key.lower() in _SENSITIVE_HEADERS and not _contains_reference(value):
            raise _error(server.source_path, server.name, f"headers.{key}", "must use an environment reference")
        resolved_headers[key] = _resolve_value(value, environ, server, f"headers.{key}")
    return ResolvedServer(
        name=server.name,
        source_path=server.source_path,
        command=server.command,
        args=server.args,
        cwd=server.cwd,
        env=MappingProxyType(resolved_env),
        url=server.url,
        headers=MappingProxyType(resolved_headers),
        request_timeout_ms=server.request_timeout_ms,
    )


def _read_json(source_path: Path) -> object:
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"{source_path}: could not read UTF-8 JSON ({type(error).__name__})") from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{source_path}: expected valid JSON at line {error.lineno}, column {error.colno}") from error


def _parse_source(source_path: Path, payload: object) -> dict[str, ServerConfig]:
    if not isinstance(payload, dict):
        raise ConfigError(f"{source_path}: root must be an object containing mcpServers")
    unknown_top_level = set(payload) - {"mcpServers"}
    if unknown_top_level:
        name = sorted(str(value) for value in unknown_top_level)[0]
        raise ConfigError(f"{source_path}: unknown top-level field {name!r}")
    raw_servers = payload.get("mcpServers")
    if not isinstance(raw_servers, dict):
        raise ConfigError(f"{source_path}: mcpServers must be an object")
    parsed: dict[str, ServerConfig] = {}
    for name, value in raw_servers.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{source_path}: mcpServers names must be non-empty strings")
        parsed[name] = _parse_server(source_path, name, value)
    return parsed


def _parse_server(source_path: Path, name: str, value: object) -> ServerConfig:
    if not isinstance(value, dict):
        raise _error(source_path, name, None, "must be an object")
    unknown = set(value) - _SERVER_FIELDS
    if unknown:
        field_name = sorted(str(item) for item in unknown)[0]
        raise _error(source_path, name, field_name, "is an unknown field")

    command = value.get("command")
    url = value.get("url")
    has_command = isinstance(command, str) and bool(command.strip())
    has_url = isinstance(url, str) and bool(url.strip())
    if has_command == has_url:
        raise _error(source_path, name, None, "must specify exactly one non-empty command or url")
    if command is not None and not has_command:
        raise _error(source_path, name, "command", "must be a non-empty string")
    if url is not None and not has_url:
        raise _error(source_path, name, "url", "must be a non-empty string")

    args = _string_sequence(source_path, name, "args", value.get("args", []))
    cwd = value.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise _error(source_path, name, "cwd", "must be a string")
    env = _string_mapping(source_path, name, "env", value.get("env", {}))
    headers = _string_mapping(source_path, name, "headers", value.get("headers", {}))
    timeout = value.get("requestTimeoutMs")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int)):
        raise _error(source_path, name, "requestTimeoutMs", "must be an integer")
    if has_command and headers:
        raise _error(source_path, name, "headers", "is only valid for url servers")
    if has_url and (args or cwd is not None or env):
        raise _error(source_path, name, None, "args, cwd, and env are only valid for command servers")

    return ServerConfig(
        name=name,
        source_path=source_path,
        command=command if has_command else None,
        args=args,
        cwd=cwd,
        env=MappingProxyType(env),
        url=url if has_url else None,
        headers=MappingProxyType(headers),
        request_timeout_ms=timeout,
    )


def _string_sequence(source_path: Path, server: str, field_name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _error(source_path, server, field_name, "must be an array of strings")
    return tuple(value)


def _string_mapping(source_path: Path, server: str, field_name: str, value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise _error(source_path, server, field_name, "must be an object with string values")
    return dict(value)


def _contains_reference(value: str) -> bool:
    return _EXACT_ENV.fullmatch(value) is not None or _BRACED_ENV.search(value) is not None


def _resolve_value(
    value: str,
    environ: Mapping[str, str],
    server: ServerConfig,
    field_name: str,
) -> str:
    exact = _EXACT_ENV.fullmatch(value)
    if exact is not None:
        return _environment_value(exact.group(1), environ, server, field_name)

    def replace(match: re.Match[str]) -> str:
        return _environment_value(match.group(1), environ, server, field_name)

    return _BRACED_ENV.sub(replace, value)


def _environment_value(
    name: str,
    environ: Mapping[str, str],
    server: ServerConfig,
    field_name: str,
) -> str:
    value = environ.get(name)
    if not value:
        raise _error(server.source_path, server.name, field_name, f"requires non-empty environment variable {name}")
    return value


def _error(source_path: Path, server: str, field_name: str | None, message: str) -> ConfigError:
    location = f"mcpServers.{server}"
    if field_name is not None:
        location = f"{location}.{field_name}"
    return ConfigError(f"{source_path}: {location} {message}")
