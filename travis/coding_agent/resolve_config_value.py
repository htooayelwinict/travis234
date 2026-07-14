"""Resolve environment and command-backed values from Travis configuration."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Mapping

_COMMAND_CACHE: dict[str, str | None] = {}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


def clear_config_value_cache() -> None:
    _COMMAND_CACHE.clear()


def is_command_config_value(value: str) -> bool:
    return value.startswith("!")


def get_config_value_env_var_names(value: str) -> list[str]:
    if is_command_config_value(value):
        return []
    names: list[str] = []
    for kind, part in _parse_template(value):
        if kind == "env" and part not in names:
            names.append(part)
    return names


def is_config_value_configured(
    value: str,
    env: Mapping[str, str] | None = None,
) -> bool:
    if is_command_config_value(value):
        return True
    return all(_env_value(name, env) for name in get_config_value_env_var_names(value))


def resolve_config_value(
    value: str,
    env: Mapping[str, str] | None = None,
    *,
    uncached: bool = False,
) -> str | None:
    if is_command_config_value(value):
        return _execute_command(value, uncached=uncached)
    output = ""
    for kind, part in _parse_template(value):
        if kind == "literal":
            output += part
            continue
        resolved = _env_value(part, env)
        if resolved is None:
            return None
        output += resolved
    return output


def resolve_config_value_or_throw(
    value: str,
    description: str,
    env: Mapping[str, str] | None = None,
) -> str:
    resolved = resolve_config_value(value, env, uncached=True)
    if resolved is not None:
        return resolved
    if is_command_config_value(value):
        raise RuntimeError(f"Failed to resolve {description} from command: {value[1:]}")
    missing = [name for name in get_config_value_env_var_names(value) if not _env_value(name, env)]
    if len(missing) == 1:
        raise RuntimeError(f"Failed to resolve {description} from environment variable: {missing[0]}")
    if missing:
        raise RuntimeError(f"Failed to resolve {description} from environment variables: {', '.join(missing)}")
    raise RuntimeError(f"Failed to resolve {description}")


def resolve_headers_or_throw(
    headers: object,
    description: str,
    env: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    if not isinstance(headers, dict):
        return None
    resolved = {
        str(name): resolve_config_value_or_throw(str(value), f'{description} header "{name}"', env)
        for name, value in headers.items()
    }
    return resolved or None


def _execute_command(value: str, *, uncached: bool) -> str | None:
    if not uncached and value in _COMMAND_CACHE:
        return _COMMAND_CACHE[value]
    try:
        arguments = shlex.split(value[1:])
        if not arguments:
            return None
        completed = subprocess.run(
            arguments,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        result = completed.stdout.strip() if completed.returncode == 0 else None
        result = result or None
    except (OSError, ValueError, subprocess.SubprocessError):
        result = None
    if not uncached:
        _COMMAND_CACHE[value] = result
    return result


def _env_value(name: str, env: Mapping[str, str] | None) -> str | None:
    value = env.get(name) if env is not None else None
    value = value or os.environ.get(name)
    return str(value) if value else None


def _parse_template(value: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    index = 0
    while index < len(value):
        dollar = value.find("$", index)
        if dollar < 0:
            _append_literal(parts, value[index:])
            break
        _append_literal(parts, value[index:dollar])
        marker = value[dollar + 1 : dollar + 2]
        if marker in {"$", "!"}:
            _append_literal(parts, marker)
            index = dollar + 2
            continue
        if marker == "{":
            end = value.find("}", dollar + 2)
            if end < 0:
                _append_literal(parts, "$")
                index = dollar + 1
                continue
            name = value[dollar + 2 : end]
            if _ENV_NAME.fullmatch(name):
                parts.append(("env", name))
            else:
                _append_literal(parts, value[dollar : end + 1])
            index = end + 1
            continue
        match = _ENV_PREFIX.match(value[dollar + 1 :])
        if match:
            parts.append(("env", match.group(0)))
            index = dollar + 1 + len(match.group(0))
            continue
        _append_literal(parts, "$")
        index = dollar + 1
    return parts


def _append_literal(parts: list[tuple[str, str]], value: str) -> None:
    if not value:
        return
    if parts and parts[-1][0] == "literal":
        parts[-1] = ("literal", parts[-1][1] + value)
    else:
        parts.append(("literal", value))


__all__ = [
    "clear_config_value_cache",
    "get_config_value_env_var_names",
    "is_command_config_value",
    "is_config_value_configured",
    "resolve_config_value",
    "resolve_config_value_or_throw",
    "resolve_headers_or_throw",
]
