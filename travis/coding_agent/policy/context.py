"""Allowlisted context builders for tool approval requests."""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Callable


def workspace_path_context(
    cwd: str,
    action: str,
    *,
    argument: str = "path",
) -> Callable[[dict[str, object]], dict[str, str]]:
    workspace = Path(cwd).expanduser().resolve()

    def build(arguments: dict[str, object]) -> dict[str, str]:
        raw_target = arguments.get(argument, ".")
        if not isinstance(raw_target, str) or not raw_target:
            return {"action": action}
        if raw_target.startswith("artifact-"):
            return {"action": action, "target": raw_target}
        target = Path(raw_target).expanduser()
        if not target.is_absolute():
            target = workspace / target
        try:
            relative = target.resolve(strict=False).relative_to(workspace)
            rendered = relative.as_posix() or "."
        except ValueError:
            rendered = "<outside-workspace>"
        return {"action": action, "target": rendered}

    return build


def shell_policy_context(arguments: dict[str, object]) -> dict[str, str]:
    command = arguments.get("command")
    if not isinstance(command, str):
        command = ""
    try:
        words = shlex.split(command)
    except ValueError:
        words = []
    executable = Path(words[0]).name if words else "<unknown>"
    fingerprint = hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()[:16]
    return {
        "action": "execute",
        "executable": executable,
        "commandFingerprint": fingerprint,
    }


def action_policy_context(arguments: dict[str, object]) -> dict[str, str]:
    action = arguments.get("action")
    return {"action": action if isinstance(action, str) and action else "inspect"}


def fixed_action_context(action: str) -> Callable[[dict[str, object]], dict[str, str]]:
    def build(_arguments: dict[str, object]) -> dict[str, str]:
        return {"action": action}

    return build


def subagent_policy_context(arguments: dict[str, object]) -> dict[str, str]:
    context = {"action": "spawn"}
    role = arguments.get("role")
    backend = arguments.get("backend", "internal")
    if isinstance(role, str) and role:
        context["role"] = role
    if isinstance(backend, str) and backend:
        context["backend"] = backend
    return context
