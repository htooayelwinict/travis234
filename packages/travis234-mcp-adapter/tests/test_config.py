from __future__ import annotations

import json
from pathlib import Path

import pytest

from travis234_mcp_adapter.config import (
    ConfigError,
    ServerConfig,
    load_config,
    resolve_server,
)
from travis234_mcp_adapter import packaged_servers
from travis234_mcp_adapter.packaged_servers import (
    PackagedServer,
    merge_packaged_servers,
    register_packaged_server,
)

from conftest import ConfigTree


def _packaged_server(tmp_path: Path, name: str = "package-fixture") -> PackagedServer:
    root = tmp_path / "payload"
    command = root / "bin" / "fixture-server"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("binary", encoding="utf-8")
    command.chmod(0o755)
    return PackagedServer(
        name=name,
        package_root=root,
        command=command,
        args=("mcp",),
        request_timeout_ms=1_800_000,
    )


def test_project_source_replaces_global_only_when_trusted(config_tree: ConfigTree) -> None:
    config_tree.write_global_shared("shared", {"command": "global", "args": ["one"]})
    config_tree.write_global_travis("shared", {"command": "override", "args": ["two"]})
    config_tree.write_project_shared("shared", {"url": "https://project.test/mcp"})

    untrusted = load_config(config_tree.cwd, config_tree.home, False)
    trusted = load_config(config_tree.cwd, config_tree.home, True)

    assert untrusted.servers["shared"].command == "override"
    assert untrusted.ignored_project_sources == (config_tree.cwd / ".mcp.json",)
    assert trusted.servers["shared"].url == "https://project.test/mcp"
    assert trusted.servers["shared"].command is None


def test_travis_project_source_wins_without_reading_pi(config_tree: ConfigTree) -> None:
    config_tree.write_project_shared("shared", {"command": "shared"})
    winning = config_tree.write_project_travis("shared", {"command": "travis"})
    pi_file = config_tree.home / ".pi" / "agent" / "mcp.json"
    pi_file.parent.mkdir(parents=True)
    pi_file.write_text("not valid json", encoding="utf-8")

    loaded = load_config(config_tree.cwd, config_tree.home, True)

    assert loaded.servers["shared"].command == "travis"
    assert loaded.servers["shared"].source_path == winning
    assert pi_file not in loaded.sources


def test_shared_config_accepts_explicit_lazy_lifecycle(config_tree: ConfigTree) -> None:
    shared = config_tree.write_global_shared(
        "filesystem",
        {"command": "shared-server", "lifecycle": "lazy"},
    )
    config_tree.write_project_travis("fixture", {"command": "project-server"})

    loaded = load_config(config_tree.cwd, config_tree.home, True)

    assert loaded.servers["filesystem"].command == "shared-server"
    assert loaded.servers["filesystem"].source_path == shared
    assert loaded.servers["fixture"].command == "project-server"


def test_reconnect_defaults_disabled_and_accepts_bounded_settings(
    config_tree: ConfigTree,
) -> None:
    config_tree.write_global_shared("default", {"command": "default-server"})
    config_tree.write_global_travis(
        "configured",
        {
            "command": "configured-server",
            "reconnect": {
                "automatic": True,
                "maxAttempts": 3,
                "baseDelayMs": 500,
            },
        },
    )

    loaded = load_config(config_tree.cwd, config_tree.home, False)

    assert loaded.servers["default"].reconnect.automatic is False
    assert loaded.servers["default"].reconnect.max_attempts == 1
    assert loaded.servers["default"].reconnect.base_delay_ms == 100
    assert loaded.servers["configured"].reconnect.automatic is True
    assert loaded.servers["configured"].reconnect.max_attempts == 3
    assert loaded.servers["configured"].reconnect.base_delay_ms == 500


@pytest.mark.parametrize(
    ("reconnect", "message"),
    [
        (None, "reconnect"),
        ({"unknown": True}, "unknown"),
        ({"automatic": 1}, "automatic"),
        ({"maxAttempts": True}, "maxAttempts"),
        ({"maxAttempts": 0}, "maxAttempts"),
        ({"maxAttempts": 4}, "maxAttempts"),
        ({"baseDelayMs": True}, "baseDelayMs"),
        ({"baseDelayMs": 99}, "baseDelayMs"),
        ({"baseDelayMs": 501}, "baseDelayMs"),
    ],
)
def test_reconnect_configuration_is_strict(
    config_tree: ConfigTree,
    reconnect: object,
    message: str,
) -> None:
    path = config_tree.write_global_shared(
        "fixture",
        {"command": "fixture-server", "reconnect": reconnect},
    )

    with pytest.raises(ConfigError, match=message) as caught:
        load_config(config_tree.cwd, config_tree.home, False)

    assert str(path) in str(caught.value)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-json", "valid JSON"),
        (json.dumps({"mcpServers": []}), "mcpServers"),
        (json.dumps({"mcpServers": {"x": {"transport": "sse"}}}), "unknown field"),
        (
            json.dumps({"mcpServers": {"x": {"command": "a", "lifecycle": "eager"}}}),
            "only supports lazy",
        ),
        (json.dumps({"mcpServers": {"x": {"command": "a", "url": "https://x"}}}), "exactly one"),
        (json.dumps({"mcpServers": {"x": {"command": "a", "args": [1]}}}), "args"),
        (json.dumps({"mcpServers": {"x": {"url": "https://x", "headers": {"X": 1}}}}), "headers"),
        (json.dumps({"mcpServers": {"x": {"command": "a", "requestTimeoutMs": True}}}), "requestTimeoutMs"),
    ],
)
def test_invalid_authorized_file_disables_config(
    config_tree: ConfigTree,
    payload: str,
    message: str,
) -> None:
    config_tree.write_global_shared("healthy", {"command": "healthy"})
    invalid = config_tree.cwd / ".mcp.json"
    invalid.write_text(payload, encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(config_tree.cwd, config_tree.home, True)

    rendered = str(caught.value)
    assert str(invalid) in rendered
    assert message in rendered
    assert "healthy" not in rendered


@pytest.mark.parametrize("template", ["${SERVICE_TOKEN}", "$env:SERVICE_TOKEN"])
def test_secret_resolves_only_at_connection_time(template: str, tmp_path: Path) -> None:
    configured = ServerConfig(
        name="fixture",
        source_path=tmp_path / "mcp.json",
        command="fixture-server",
        env={"SERVICE_TOKEN": template},
    )

    resolved = resolve_server(configured, {"SERVICE_TOKEN": "secret-value"})

    assert configured.env == {"SERVICE_TOKEN": template}
    assert resolved.env == {"SERVICE_TOKEN": "secret-value"}
    assert "secret-value" not in repr(resolved)


def test_environment_expansion_is_non_recursive_and_limited_to_values(tmp_path: Path) -> None:
    configured = ServerConfig(
        name="fixture",
        source_path=tmp_path / "mcp.json",
        command="${COMMAND}",
        args=("$env:ARG",),
        cwd="${WORKDIR}",
        env={"COMBINED": "prefix-${FIRST}-suffix", "CHAINED": "${CHAINED}"},
    )

    resolved = resolve_server(
        configured,
        {
            "COMMAND": "wrong-command",
            "ARG": "wrong-arg",
            "WORKDIR": "/wrong/path",
            "FIRST": "one",
            "CHAINED": "${SECOND}",
            "SECOND": "two",
        },
    )

    assert resolved.command == "${COMMAND}"
    assert resolved.args == ("$env:ARG",)
    assert resolved.cwd == "${WORKDIR}"
    assert resolved.env == {"COMBINED": "prefix-one-suffix", "CHAINED": "${SECOND}"}


def test_missing_secret_names_key_without_leaking_other_values(tmp_path: Path) -> None:
    configured = ServerConfig(
        name="remote",
        source_path=tmp_path / "mcp.json",
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer ${REMOTE_TOKEN}"},
    )

    with pytest.raises(ConfigError, match="REMOTE_TOKEN") as caught:
        resolve_server(configured, {"OTHER": "other-secret"})

    assert "other-secret" not in str(caught.value)


@pytest.mark.parametrize("header", ["Authorization", "Cookie", "Proxy-Authorization"])
def test_sensitive_http_headers_reject_literal_values(tmp_path: Path, header: str) -> None:
    configured = ServerConfig(
        name="remote",
        source_path=tmp_path / "mcp.json",
        url="https://example.test/mcp",
        headers={header: "literal-secret"},
    )

    with pytest.raises(ConfigError, match="environment reference") as caught:
        resolve_server(configured, {})

    assert "literal-secret" not in str(caught.value)


def test_sensitive_stdio_environment_rejects_literal_but_allows_non_secret(tmp_path: Path) -> None:
    configured = ServerConfig(
        name="fixture",
        source_path=tmp_path / "mcp.json",
        command="fixture-server",
        env={"SERVICE_TOKEN": "literal-secret", "LOG_LEVEL": "debug"},
    )

    with pytest.raises(ConfigError, match="SERVICE_TOKEN.*environment reference") as caught:
        resolve_server(configured, {})

    assert "literal-secret" not in str(caught.value)


def test_packaged_server_shadows_same_named_file_config_without_rewriting(
    config_tree: ConfigTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})
    configured = config_tree.write_global_travis(
        "package-fixture",
        {"command": "/tmp/external-fixture"},
    )
    loaded = load_config(config_tree.cwd, config_tree.home, False)
    descriptor = _packaged_server(config_tree.home)
    register_packaged_server(descriptor)

    merged = merge_packaged_servers(loaded)

    assert merged.config.servers["package-fixture"].command == str(descriptor.command)
    assert merged.config.servers["package-fixture"].args == ("mcp",)
    assert merged.config.servers["package-fixture"].request_timeout_ms == 1_800_000
    assert merged.shadowed_configured_names == ("package-fixture",)
    assert merged.config.sources == loaded.sources
    assert "external-fixture" in configured.read_text(encoding="utf-8")


def test_packaged_server_preserves_unrelated_config_and_trust_metadata(
    config_tree: ConfigTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})
    config_tree.write_global_shared("filesystem", {"command": "filesystem-server"})
    config_tree.write_project_shared("ignored", {"command": "project-server"})
    loaded = load_config(config_tree.cwd, config_tree.home, False)
    register_packaged_server(_packaged_server(config_tree.home))

    merged = merge_packaged_servers(loaded)

    assert tuple(merged.config.servers) == ("filesystem", "package-fixture")
    assert merged.config.servers["filesystem"] == loaded.servers["filesystem"]
    assert merged.config.ignored_project_sources == loaded.ignored_project_sources
    assert merged.shadowed_configured_names == ()
