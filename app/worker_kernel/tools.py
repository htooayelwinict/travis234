"""Permission-gated worker tools.

Workers ask for named tools with JSON arguments. This module owns the boundary
between agent decisions and local side effects.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas import ArtifactPayload, Task


IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class WorkerToolError(Exception):
    code = "tool_error"
    retryable = True
    issue_type = "instance_failure"


class ToolPermissionError(WorkerToolError):
    code = "tool_permission_denied"


class ToolUnavailableError(WorkerToolError):
    code = "tool_unavailable"
    retryable = False
    issue_type = "plan_failure"


class ToolExecutionError(WorkerToolError):
    code = "tool_execution_error"


@dataclass(frozen=True)
class WorkerToolConfig:
    root_path: Path
    timeout_seconds: float = 15.0
    max_file_bytes: int = 200_000


class WorkerToolbox:
    def __init__(self, config: WorkerToolConfig) -> None:
        self._config = config
        self._root = config.root_path.resolve()

    def available_tools(self, task: Task) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        if task.permissions.read_files:
            tools.extend(
                [
                    _tool_spec("list_dir", "List direct children under a repository path.", "read_files", {"path": "string"}),
                    _tool_spec("read_file", "Read a UTF-8 text file under the repository root.", "read_files", {"path": "string"}),
                    _tool_spec("file_search", "Find repository files by glob pattern.", "read_files", {"path": "string", "pattern": "string"}),
                    _tool_spec("text_search", "Search repository text using a literal or regex pattern.", "read_files", {"path": "string", "pattern": "string"}),
                    _tool_spec("json_query", "Read a JSON file and return a dotted path value.", "read_files", {"path": "string", "query": "string"}),
                    _tool_spec("git_status", "Return git status --short.", "read_files", {}),
                    _tool_spec("git_diff", "Return git diff for the repo or one path.", "read_files", {"path": "string"}),
                ]
            )
        if task.permissions.write_files:
            tools.extend(
                [
                    _tool_spec("write_file", "Write a full file inside approved write scope.", "write_files", {"path": "string", "content": "string"}),
                    _tool_spec("replace_in_file", "Replace one exact text occurrence inside approved write scope.", "write_files", {"path": "string", "old": "string", "new": "string"}),
                ]
            )
        if task.permissions.run_commands:
            tools.append(_tool_spec("run_readonly_command", "Run an allowlisted readonly verification command.", "run_commands", {"command": "string_or_string_array"}))
        if task.permissions.web_research:
            tools.extend(
                [
                    _tool_spec("web_search", "Search the web using the configured provider.", "web_research", {"query": "string"}),
                    _tool_spec("web_fetch", "Fetch a known HTTP(S) URL.", "web_research", {"url": "string"}),
                ]
            )
        return tools

    def execute(self, *, task: Task, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = arguments or {}
        if tool_name == "list_dir":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._list_dir(arguments)
        if tool_name == "read_file":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._read_file(arguments)
        if tool_name == "file_search":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._file_search(arguments)
        if tool_name == "text_search":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._text_search(arguments)
        if tool_name == "json_query":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._json_query(arguments)
        if tool_name == "git_status":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._run_checked(["git", "status", "--short"])
        if tool_name == "git_diff":
            self._require(task.permissions.read_files, "read_files", tool_name)
            path = arguments.get("path")
            command = ["git", "diff", "--"] + ([str(path)] if path else [])
            return self._run_checked(command)
        if tool_name == "write_file":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._write_file(task, arguments)
        if tool_name == "replace_in_file":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._replace_in_file(task, arguments)
        if tool_name == "run_readonly_command":
            self._require(task.permissions.run_commands, "run_commands", tool_name)
            return self._run_readonly_command(arguments)
        if tool_name == "web_search":
            self._require(task.permissions.web_research, "web_research", tool_name)
            raise ToolUnavailableError("web_search provider is not configured for worker runtime")
        if tool_name == "web_fetch":
            self._require(task.permissions.web_research, "web_research", tool_name)
            return self._web_fetch(arguments)
        raise ToolPermissionError(f"unknown or unavailable worker tool: {tool_name}")

    def _require(self, allowed: bool, permission: str, tool_name: str) -> None:
        if not allowed:
            raise ToolPermissionError(f"tool {tool_name} requires permission {permission}")

    def _list_dir(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_read_path(str(arguments.get("path") or "."))
        if not path.is_dir():
            raise ToolExecutionError(f"path is not a directory: {self._display_path(path)}")
        entries = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if self._is_ignored_path(child):
                continue
            entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file"})
            if len(entries) >= 200:
                break
        return {"path": self._display_path(path), "entries": entries}

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_read_path(str(arguments.get("path") or ""))
        if not path.is_file():
            raise ToolExecutionError(f"path is not a file: {self._display_path(path)}")
        content = path.read_text(encoding="utf-8", errors="replace")[: self._config.max_file_bytes]
        return {"path": self._display_path(path), "content": content, "truncated": path.stat().st_size > len(content)}

    def _file_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern") or "*")
        start = self._resolve_read_path(str(arguments.get("path") or "."))
        matches = []
        for match in sorted(start.rglob(pattern)):
            if self._is_ignored_path(match):
                continue
            matches.append(self._display_path(match))
            if len(matches) >= 200:
                break
        return {"pattern": pattern, "matches": matches}

    def _text_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern") or "")
        if not pattern:
            raise ToolExecutionError("text_search requires a non-empty pattern")
        path = str(arguments.get("path") or ".")
        resolved = self._resolve_read_path(path)
        command = ["rg", "-n", pattern, self._display_path(resolved)]
        try:
            return self._run_checked(command)
        except ToolExecutionError as exc:
            if "exit code 1" in str(exc):
                return {"stdout": "", "stderr": "", "returncode": 1, "matches": []}
            raise

    def _json_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_read_path(str(arguments.get("path") or ""))
        query = str(arguments.get("query") or "")
        data = json.loads(path.read_text(encoding="utf-8"))
        value: Any = data
        for part in [p for p in query.strip(".").split(".") if p]:
            if isinstance(value, list):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise ToolExecutionError(f"json_query cannot descend into {type(value).__name__}")
        return {"path": self._display_path(path), "query": query, "value": value}

    def _write_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_write_path(task, str(arguments.get("path") or ""))
        content = str(arguments.get("content") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": self._display_path(path), "bytes_written": len(content.encode("utf-8"))}

    def _replace_in_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_write_path(task, str(arguments.get("path") or ""))
        old = str(arguments.get("old") or "")
        new = str(arguments.get("new") or "")
        if not old:
            raise ToolExecutionError("replace_in_file requires a non-empty old value")
        content = path.read_text(encoding="utf-8")
        if old not in content:
            raise ToolExecutionError("replace_in_file old value was not found")
        updated = content.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        return {"path": self._display_path(path), "replacements": 1}

    def _run_readonly_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_command = arguments.get("command")
        if isinstance(raw_command, str):
            command = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            command = [str(part) for part in raw_command]
        else:
            raise ToolExecutionError("run_readonly_command requires command as string or list")
        env_overrides, command = self._split_env_assignments(command)
        if not self._is_allowed_readonly_command(command):
            raise ToolPermissionError(f"command is not in the readonly allowlist: {' '.join(command)}")
        return self._run_checked(command, allowed_returncodes=None, env_overrides=env_overrides)

    def _web_fetch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ToolExecutionError("web_fetch requires an http(s) URL")
        request = urllib.request.Request(url, headers={"User-Agent": "allthebest-worker-runtime/1.0"})
        with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
            body = response.read(self._config.max_file_bytes).decode("utf-8", errors="replace")
        return {"url": url, "content": body}

    def _run_checked(
        self,
        command: list[str],
        *,
        allowed_returncodes: set[int] | None = {0, 1},
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            env = None
            if env_overrides:
                env = {**os.environ, **env_overrides}
            completed = subprocess.run(
                command,
                cwd=self._root,
                text=True,
                capture_output=True,
                timeout=self._config.timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(f"tool command timed out: {' '.join(command)}") from exc
        except OSError as exc:
            raise ToolExecutionError(f"tool command failed to start: {' '.join(command)}") from exc
        stdout = completed.stdout[: self._config.max_file_bytes]
        stderr = completed.stderr[: self._config.max_file_bytes]
        if allowed_returncodes is not None and completed.returncode not in allowed_returncodes:
            raise ToolExecutionError(
                f"tool command exited with code {completed.returncode}: {' '.join(command)}\n{stderr}"
            )
        return {
            "command": command,
            "env": env_overrides or {},
            "stdout": stdout,
            "stderr": stderr,
            "returncode": completed.returncode,
        }

    def _split_env_assignments(self, command: list[str]) -> tuple[dict[str, str], list[str]]:
        env_overrides: dict[str, str] = {}
        while command and "=" in command[0] and not command[0].startswith("="):
            key, value = command[0].split("=", 1)
            if key != "PYTHONPATH":
                raise ToolPermissionError(f"environment override is not allowlisted: {key}")
            env_overrides[key] = value
            command = command[1:]
        return env_overrides, command

    def _is_allowed_readonly_command(self, command: list[str]) -> bool:
        if not command:
            return False
        if command[0] in {"rg", "grep", "jq"}:
            return True
        if command[0] == "git" and len(command) >= 2:
            return command[1] in {"status", "diff", "show", "log"}
        if command[:3] == ["uv", "run", "pytest"]:
            return True
        if command[0] == "pytest":
            return True
        executable = Path(command[0]).name
        if executable in {"python", "python3"} and command[1:3] == ["-m", "pytest"]:
            return True
        return False

    def _resolve_read_path(self, raw_path: str) -> Path:
        if not raw_path:
            raise ToolExecutionError("path is required")
        path = (self._root / raw_path).resolve()
        if not path.is_relative_to(self._root):
            raise ToolPermissionError("path escapes worker root")
        return path

    def _resolve_write_path(self, task: Task, raw_path: str) -> Path:
        path = self._resolve_read_path(raw_path)
        allowed = self._allowed_write_paths(task)
        if not allowed:
            raise ToolUnavailableError("write_files was allowed but no write scope paths were provided")
        if not any(path == allowed_path or path.is_relative_to(allowed_path) for allowed_path in allowed):
            raise ToolPermissionError(f"write path is outside allowed scope: {self._display_path(path)}")
        return path

    def _allowed_write_paths(self, task: Task) -> list[Path]:
        raw_paths = list(task.permissions.write_paths)
        artifact_lookup = {artifact.id: artifact for artifact in task.input_artifacts}
        for artifact_id in task.permissions.write_paths_from_artifacts:
            artifact = artifact_lookup.get(artifact_id)
            if artifact is None:
                continue
            raw_paths.extend(self._paths_from_artifact(artifact))
        resolved: list[Path] = []
        for raw_path in raw_paths:
            try:
                resolved.append(self._resolve_read_path(raw_path))
            except WorkerToolError:
                continue
        return resolved

    def _paths_from_artifact(self, artifact: ArtifactPayload) -> list[str]:
        return sorted(set(_extract_path_candidates(artifact.content)))

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._root))
        except ValueError:
            return str(path)

    def _is_ignored_path(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self._root)
        except ValueError:
            return True
        return any(part in IGNORED_DIR_NAMES for part in relative.parts)


def _tool_spec(name: str, description: str, permission: str, parameters: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{description} Requires {permission} permission.",
            "parameters": {
                "type": "object",
                "properties": {key: _parameter_schema(value) for key, value in parameters.items()},
                "required": list(parameters),
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def _parameter_schema(value: str) -> dict[str, Any]:
    if value == "string_or_string_array":
        return {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        }
    return {"type": "string"}


def _extract_path_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        if _looks_like_repo_path(value):
            candidates.append(value)
        return candidates
    if isinstance(value, list):
        for item in value:
            candidates.extend(_extract_path_candidates(item))
        return candidates
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"path", "file"} and isinstance(child, str) and _looks_like_repo_path(child):
                candidates.append(child)
            elif key in {"paths", "files", "allowed_paths", "candidate_paths"}:
                candidates.extend(_extract_path_candidates(child))
            else:
                candidates.extend(_extract_path_candidates(child))
    return candidates


def _looks_like_repo_path(value: str) -> bool:
    value = value.strip()
    if not value or "\n" in value:
        return False
    if any(char.isspace() for char in value):
        return False
    if value.startswith(("-", "git ", "pytest ", "python ")):
        return False
    return "/" in value or "." in Path(value).name
