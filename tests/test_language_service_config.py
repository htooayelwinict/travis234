from __future__ import annotations

import json
from pathlib import Path

import pytest

from travis.coding_agent.language_services.config import (
    SettingsValidationError,
    parse_language_servers,
    select_server_config,
)
from travis.coding_agent.language_services.types import LanguageServiceLimits
from travis.coding_agent.settings_manager import InMemorySettingsStorage, SettingsManager


def _server(name: str = "python", **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": name,
        "command": "fixture-lsp",
        "args": ["--stdio"],
        "languages": ["python"],
        "extensions": {"PY": "python"},
        "rootMarkers": ["pyproject.toml"],
        "initializationOptions": {"analysis": {"mode": "openFilesOnly"}},
    }
    value.update(overrides)
    return value


def _settings_with_scopes(global_value: object, project_value: object, *, trusted: bool) -> SettingsManager:
    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _current: json.dumps({"languageServers": global_value}))
    storage.with_lock("project", lambda _current: json.dumps({"languageServers": project_value}))
    return SettingsManager.from_storage(storage, {"projectTrusted": trusted})


def test_untrusted_project_server_command_is_ignored() -> None:
    settings = _settings_with_scopes([], [_server(command="project-lsp")], trusted=False)

    assert settings.get_language_server_configs() == []


def test_trusted_project_replaces_global_server_atomically() -> None:
    settings = _settings_with_scopes(
        [_server(command="global-lsp", args=["--global"])],
        [_server(command="project-lsp", args=["--project"])],
        trusted=True,
    )

    [config] = settings.get_language_server_configs()
    assert config.command == "project-lsp"
    assert config.args == ("--project",)


def test_invalid_project_entry_does_not_hide_valid_global_server() -> None:
    settings = _settings_with_scopes([_server(command="global-lsp")], [_server(command="bad shell --stdio")], trusted=True)

    [config] = settings.get_language_server_configs()
    assert config.command == "global-lsp"
    errors = settings.drain_errors()
    assert len(errors) == 1
    assert errors[0]["scope"] == "project"
    assert "languageServers" in str(errors[0]["error"])


def test_config_rejects_shell_command_string() -> None:
    with pytest.raises(SettingsValidationError, match="single executable"):
        parse_language_servers([_server(command="pyright --stdio")])


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"name": ""}, "name"),
        ({"command": "./relative/server"}, "command"),
        ({"args": "--stdio"}, "args"),
        ({"args": ["--stdio", 7]}, "args"),
        ({"languages": []}, "languages"),
        ({"languages": ["python", "python"]}, "languages"),
        ({"extensions": {".py": "typescript"}}, "extensions"),
        ({"rootMarkers": ["../escape"]}, "rootMarkers"),
        ({"initializationOptions": {"apiToken": "secret"}}, "sensitive"),
        ({"initializationOptions": {"nested": {"password": "secret"}}}, "sensitive"),
        ({"initializationOptions": {"bad": object()}}, "JSON"),
        ({"unknown": True}, "unknown"),
    ],
)
def test_config_rejects_invalid_fields(override: dict[str, object], message: str) -> None:
    with pytest.raises(SettingsValidationError, match=message):
        parse_language_servers([_server(**override)])


def test_config_accepts_bare_and_absolute_executables_and_normalizes_extensions(tmp_path: Path) -> None:
    configs = parse_language_servers(
        [
            _server("bare", command="fixture-lsp", extensions={"PY": "python"}),
            _server("absolute", command=str(tmp_path / "fixture-lsp"), extensions={".PyI": "python"}),
        ]
    )

    assert configs[0].extensions == {".py": "python"}
    assert configs[1].extensions == {".pyi": "python"}


def test_config_rejects_duplicate_names() -> None:
    with pytest.raises(SettingsValidationError, match="duplicate"):
        parse_language_servers([_server("same"), _server("same")])


def test_language_service_limits_are_exact() -> None:
    assert LanguageServiceLimits() == LanguageServiceLimits(
        max_active_servers=3,
        startup_timeout_seconds=10.0,
        request_timeout_seconds=20.0,
        max_restarts=2,
        restart_window_seconds=60.0,
        max_frame_bytes=2 * 1024 * 1024,
        max_inline_output_bytes=256 * 1024,
        max_apply_original_bytes=64 * 1024 * 1024,
        token_ttl_seconds=10 * 60.0,
        max_preview_tokens=32,
        max_action_tokens=32,
    )


def test_server_selection_prefers_nearest_root_then_order_then_name(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "packages" / "app"
    nested.mkdir(parents=True)
    (workspace / "root.marker").write_text("", encoding="utf-8")
    (nested / "nested.marker").write_text("", encoding="utf-8")
    source = nested / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    configs = parse_language_servers(
        [
            _server("ordered-z", rootMarkers=["root.marker"]),
            _server("nearest", rootMarkers=["nested.marker"]),
            _server("ordered-a", rootMarkers=["root.marker"]),
        ]
    )

    selected, root = select_server_config(configs, source, workspace)

    assert selected.name == "nearest"
    assert root == nested


def test_server_selection_uses_configuration_order_before_name(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    configs = parse_language_servers([_server("z-first"), _server("a-second")])

    selected, root = select_server_config(configs, source, tmp_path)

    assert selected.name == "z-first"
    assert root == tmp_path


def test_server_selection_resolves_relative_source_against_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("pass\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    selected, root = select_server_config(
        parse_language_servers([_server("python")]),
        "main.py",
        workspace,
    )

    assert selected.name == "python"
    assert root == workspace
