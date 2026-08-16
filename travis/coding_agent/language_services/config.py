"""Strict parsing and deterministic selection for configured language servers."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Iterable

from travis.coding_agent.language_services.types import LanguageServerConfig

_ALLOWED_KEYS = frozenset(
    {
        "name",
        "command",
        "args",
        "languages",
        "extensions",
        "rootMarkers",
        "initializationOptions",
    }
)
_SENSITIVE_KEY = re.compile(
    r"(?:token|key|secret|password|passwd|auth|cookie|credential)",
    re.IGNORECASE,
)


class SettingsValidationError(ValueError):
    """A language-server setting failed strict validation."""


def _require_nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsValidationError(f"languageServers {field} must be a non-blank string")
    if "\x00" in value:
        raise SettingsValidationError(f"languageServers {field} contains a NUL byte")
    return value.strip()


def _normalize_extension(value: object) -> str:
    extension = _require_nonblank(value, "extensions key").lower()
    if "/" in extension or "\\" in extension or extension in {".", ".."}:
        raise SettingsValidationError("languageServers extensions must be file suffixes")
    return extension if extension.startswith(".") else f".{extension}"


def _validate_json(value: object, *, path: tuple[str, ...] = ()) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise SettingsValidationError("languageServers initializationOptions must contain JSON values") from error
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, path=(*path, str(index)))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SettingsValidationError("languageServers initializationOptions must use string JSON keys")
            if _SENSITIVE_KEY.search(key):
                dotted = ".".join((*path, key))
                raise SettingsValidationError(
                    f"languageServers initializationOptions contains sensitive key {dotted!r}"
                )
            _validate_json(item, path=(*path, key))
        return
    raise SettingsValidationError("languageServers initializationOptions must contain JSON values")


def _parse_one(raw: object) -> LanguageServerConfig:
    if not isinstance(raw, dict):
        raise SettingsValidationError("languageServers entries must be objects")
    unknown = sorted(set(raw).difference(_ALLOWED_KEYS))
    if unknown:
        raise SettingsValidationError(f"languageServers entry has unknown keys: {', '.join(unknown)}")

    name = _require_nonblank(raw.get("name"), "name")
    command = _require_nonblank(raw.get("command"), "command")
    command_path = Path(command).expanduser()
    if not command_path.is_absolute() and (Path(command).name != command or any(char.isspace() for char in command)):
        raise SettingsValidationError("languageServers command must be one bare or absolute single executable")

    raw_args = raw.get("args", [])
    if not isinstance(raw_args, list) or any(not isinstance(arg, str) or "\x00" in arg for arg in raw_args):
        raise SettingsValidationError("languageServers args must be a string list")

    raw_languages = raw.get("languages")
    if (
        not isinstance(raw_languages, list)
        or not raw_languages
        or any(not isinstance(language, str) or not language.strip() for language in raw_languages)
    ):
        raise SettingsValidationError("languageServers languages must be a non-empty string list")
    languages = tuple(language.strip() for language in raw_languages)
    if len(set(languages)) != len(languages):
        raise SettingsValidationError("languageServers languages must not contain duplicates")

    raw_extensions = raw.get("extensions")
    if not isinstance(raw_extensions, dict) or not raw_extensions:
        raise SettingsValidationError("languageServers extensions must be a non-empty object")
    extensions: dict[str, str] = {}
    for suffix, language in raw_extensions.items():
        normalized = _normalize_extension(suffix)
        if not isinstance(language, str) or language not in languages:
            raise SettingsValidationError("languageServers extensions must map to a declared language")
        if normalized in extensions:
            raise SettingsValidationError("languageServers extensions normalize to a duplicate suffix")
        extensions[normalized] = language

    raw_markers = raw.get("rootMarkers", [])
    if not isinstance(raw_markers, list) or any(not isinstance(marker, str) for marker in raw_markers):
        raise SettingsValidationError("languageServers rootMarkers must be a string list")
    markers: list[str] = []
    for marker in raw_markers:
        normalized_marker = marker.strip()
        marker_path = Path(normalized_marker)
        if (
            not normalized_marker
            or marker_path.is_absolute()
            or ".." in marker_path.parts
            or "\x00" in normalized_marker
        ):
            raise SettingsValidationError("languageServers rootMarkers must stay within the workspace")
        markers.append(normalized_marker)
    if len(set(markers)) != len(markers):
        raise SettingsValidationError("languageServers rootMarkers must not contain duplicates")

    initialization_options = raw.get("initializationOptions", {})
    if not isinstance(initialization_options, dict):
        raise SettingsValidationError("languageServers initializationOptions must be a JSON object")
    _validate_json(initialization_options)
    return LanguageServerConfig(
        name=name,
        command=str(command_path) if command_path.is_absolute() else command,
        args=tuple(raw_args),
        languages=languages,
        extensions=extensions,
        root_markers=tuple(markers),
        initialization_options=copy.deepcopy(initialization_options),
    )


def parse_language_servers(raw: object) -> list[LanguageServerConfig]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SettingsValidationError("languageServers must be a list")
    configs: list[LanguageServerConfig] = []
    names: set[str] = set()
    for item in raw:
        config = _parse_one(item)
        if config.name in names:
            raise SettingsValidationError(f"languageServers contains duplicate name {config.name!r}")
        names.add(config.name)
        configs.append(config)
    return configs


def parse_language_server_entries(raw: object) -> tuple[list[LanguageServerConfig], list[SettingsValidationError]]:
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [SettingsValidationError("languageServers must be a list")]
    configs: list[LanguageServerConfig] = []
    errors: list[SettingsValidationError] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        try:
            config = _parse_one(item)
            if config.name in names:
                raise SettingsValidationError(f"languageServers contains duplicate name {config.name!r}")
            names.add(config.name)
            configs.append(config)
        except SettingsValidationError as error:
            errors.append(SettingsValidationError(f"Invalid languageServers[{index}]: {error}"))
    return configs, errors


def _root_for(config: LanguageServerConfig, source: Path, workspace: Path) -> Path:
    cursor = source.parent
    while True:
        if any((cursor / marker).exists() for marker in config.root_markers):
            return cursor
        if cursor == workspace:
            return workspace
        cursor = cursor.parent


def select_server_config(
    configs: Iterable[LanguageServerConfig],
    path: str | Path,
    workspace: str | Path,
) -> tuple[LanguageServerConfig, Path]:
    workspace_path = Path(workspace).expanduser().resolve()
    raw_source = Path(path).expanduser()
    source = (raw_source if raw_source.is_absolute() else workspace_path / raw_source).resolve()
    if source != workspace_path and workspace_path not in source.parents:
        raise ValueError("language-service path escapes the workspace")
    suffix = source.suffix.lower()
    candidates: list[tuple[int, int, str, LanguageServerConfig, Path]] = []
    for index, config in enumerate(configs):
        if suffix not in config.extensions:
            continue
        root = _root_for(config, source, workspace_path)
        candidates.append((-len(root.parts), index, config.name, config, root))
    if not candidates:
        raise LookupError(f"no configured language server matches {suffix or '<no suffix>'}")
    _depth, _index, _name, selected, root = min(candidates)
    return selected, root


__all__ = [
    "SettingsValidationError",
    "parse_language_server_entries",
    "parse_language_servers",
    "select_server_config",
]
