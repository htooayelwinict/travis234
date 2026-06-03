"""Permission-gated worker tools.

Workers ask for named tools with JSON arguments. This module owns the boundary
between agent decisions and local side effects.
"""

from __future__ import annotations

import fnmatch
import difflib
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from app.schemas import (
    ArtifactPayload,
    MutationScope,
    Task,
    extract_repo_path_candidates,
    resolve_mutation_scope_proposal,
)


STRICT_WRITE_SCOPE_ARTIFACT_IDS = {"mutation_scope", "allowed_write_paths", "writable_targets", "patch_scope"}

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
    issue_type = "kernel_failure"


class ToolExecutionError(WorkerToolError):
    code = "tool_execution_error"


@dataclass(frozen=True)
class WorkerToolConfig:
    root_path: Path
    timeout_seconds: float = 15.0
    max_file_bytes: int = 200_000
    web_search_provider: str = "brave"
    web_search_api_key: str | None = None
    web_search_max_results: int = 5


class WorkerToolbox:
    def __init__(self, config: WorkerToolConfig) -> None:
        self._config = config
        self._root = config.root_path.resolve()

    def available_tools(self, task: Task) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        if task.permissions.read_files:
            tools.extend(
                [
                    _tool_spec("repo_snapshot", "Return a compact repository inventory, common config files, git status, and test candidates.", "read_files", {"path": "string"}),
                    _tool_spec("list_dir", "List direct children under a repository path.", "read_files", {"path": "string"}),
                    _tool_spec("read_file", "Read a UTF-8 text file under the repository root.", "read_files", {"path": "string"}),
                    _tool_spec("read_many_files", "Read several UTF-8 text files under the repository root in one tool call.", "read_files", {"paths": "string_or_string_array"}),
                    _tool_spec("file_search", "Find repository files by glob pattern.", "read_files", {"path": "string", "pattern": "string"}),
                    _tool_spec("text_search", "Search repository text using a literal or regex pattern.", "read_files", {"path": "string", "pattern": "string"}),
                    _tool_spec("json_query", "Read a JSON file and return a dotted path value.", "read_files", {"path": "string", "query": "string"}),
                    _tool_spec("git_status", "Return git status --short.", "read_files", {}),
                    _tool_spec("git_diff", "Return git diff for the repo or one path.", "read_files", {"path": "string"}),
                    _tool_spec("diff_summary", "Return changed file names and bounded git diff text.", "read_files", {"path": "string"}),
                    _tool_spec("mutation_scope_check", "Check changed files against mutation_scope input artifacts or task write scope.", "read_files", {}),
                ]
            )
        if task.permissions.write_files:
            tools.extend(
                [
                    _tool_spec("write_file", "Write a full file inside approved write scope.", "write_files", {"path": "string", "content": "string"}),
                    _tool_spec("write_many_files", "Write multiple full files inside approved write scope in one atomic preflighted batch.", "write_files", {"files": "file_write_array"}),
                    _tool_spec("replace_in_file", "Replace one exact text occurrence inside approved write scope.", "write_files", {"path": "string", "old": "string", "new": "string"}),
                    _tool_spec("move_file", "Move one file when both source and destination are inside approved write scope.", "write_files", {"source": "string", "destination": "string", "overwrite": "boolean"}),
                    _tool_spec("delete_file", "Delete one file inside approved write scope.", "write_files", {"path": "string"}),
                ]
            )
        if task.permissions.run_commands:
            tools.extend(
                [
                    _tool_spec("runtime_capabilities", "Return structured availability/version checks for common local runtimes and package/test tools.", "run_commands", {}),
                    _tool_spec("run_readonly_command", "Run an allowlisted readonly verification command.", "run_commands", {"command": "string_or_string_array"}),
                    _tool_spec("run_focused_tests", "Run pytest for selected repo-relative test paths with PYTHONPATH set to the repo root.", "run_commands", {"paths": "string_or_string_array"}),
                ]
            )
        if task.permissions.web_research:
            tools.extend(
                [
                    _tool_spec("web_search", "Search the web using the configured provider.", "web_research", {"query": "string"}),
                    _tool_spec("web_fetch", "Fetch a known HTTP(S) URL.", "web_research", {"url": "string"}),
                ]
            )
        return tools

    def validate_write_scope(self, task: Task) -> dict[str, Any]:
        if not task.permissions.write_files:
            return {"write_scope_paths": [], "forbidden_paths": [], "forbidden_globs": []}
        allowed = self._allowed_write_paths(task)
        if not allowed:
            raise ToolUnavailableError("write_files was allowed but no write scope paths were provided")
        forbidden = self._forbidden_write_paths(task)
        forbidden_globs = self._forbidden_write_globs(task)
        return {
            "write_scope_paths": [self._display_path(path) for path in allowed],
            "forbidden_paths": [self._display_path(path) for path in forbidden],
            "forbidden_globs": forbidden_globs,
        }

    def execute(self, *, task: Task, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = arguments or {}
        if tool_name == "repo_snapshot":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._repo_snapshot(arguments)
        if tool_name == "list_dir":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._list_dir(arguments)
        if tool_name == "read_file":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._read_file(arguments)
        if tool_name == "read_many_files":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._read_many_files(arguments)
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
        if tool_name == "diff_summary":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._diff_summary(arguments)
        if tool_name == "mutation_scope_check":
            self._require(task.permissions.read_files, "read_files", tool_name)
            return self._mutation_scope_check(task)
        if tool_name == "write_file":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._write_file(task, arguments)
        if tool_name == "write_many_files":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._write_many_files(task, arguments)
        if tool_name == "replace_in_file":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._replace_in_file(task, arguments)
        if tool_name == "move_file":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._move_file(task, arguments)
        if tool_name == "delete_file":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._delete_file(task, arguments)
        if tool_name == "runtime_capabilities":
            self._require(task.permissions.run_commands, "run_commands", tool_name)
            return self._runtime_capabilities()
        if tool_name == "run_readonly_command":
            self._require(task.permissions.run_commands, "run_commands", tool_name)
            return self._run_readonly_command(arguments)
        if tool_name == "run_focused_tests":
            self._require(task.permissions.run_commands, "run_commands", tool_name)
            return self._run_focused_tests(arguments)
        if tool_name == "web_search":
            self._require(task.permissions.web_research, "web_research", tool_name)
            return self._web_search(arguments)
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

    def _repo_snapshot(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start = self._resolve_read_path(str(arguments.get("path") or "."))
        if not start.exists():
            raise ToolExecutionError(f"path does not exist: {self._display_path(start)}")

        files: list[str] = []
        dirs: set[str] = set()
        test_candidates: list[str] = []
        config_files: list[str] = []
        for path in sorted(start.rglob("*")):
            if self._is_ignored_path(path):
                continue
            relative = self._display_path(path)
            if path.is_dir():
                dirs.add(relative)
                continue
            files.append(relative)
            if _looks_like_test_path(relative):
                test_candidates.append(relative)
            if Path(relative).name in {"README.md", "pyproject.toml", "package.json", "requirements.txt", "Makefile"}:
                config_files.append(relative)
            if len(files) >= 300:
                break

        try:
            git_status = self._run_checked(["git", "status", "--short"])
        except WorkerToolError as exc:
            git_status = {"stdout": "", "stderr": str(exc), "returncode": 128}

        return {
            "path": self._display_path(start),
            "directories": sorted(dirs)[:100],
            "files": files[:300],
            "is_empty": not files and not dirs,
            "test_candidates": test_candidates[:50],
            "config_files": config_files[:30],
            "git_status": git_status,
        }

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_read_path(str(arguments.get("path") or ""))
        if not path.is_file():
            raise ToolExecutionError(f"path is not a file: {self._display_path(path)}")
        content = path.read_text(encoding="utf-8", errors="replace")[: self._config.max_file_bytes]
        return {"path": self._display_path(path), "content": content, "truncated": path.stat().st_size > len(content)}

    def _read_many_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _string_or_list(arguments.get("paths"))
        if not paths:
            raise ToolExecutionError("read_many_files requires at least one path")
        files = []
        per_file_limit = max(1, self._config.max_file_bytes // max(1, min(len(paths), 20)))
        for raw_path in paths[:20]:
            path = self._resolve_read_path(raw_path)
            if not path.is_file():
                files.append({"path": self._display_path(path), "error": "not a file"})
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[:per_file_limit]
            files.append(
                {
                    "path": self._display_path(path),
                    "content": content,
                    "truncated": path.stat().st_size > len(content),
                }
            )
        return {"files": files}

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

    def _write_many_files(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_files = arguments.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ToolExecutionError("write_many_files requires a non-empty files array")
        if len(raw_files) > 50:
            raise ToolExecutionError("write_many_files supports at most 50 files per call")

        planned: list[tuple[Path, str]] = []
        for index, item in enumerate(raw_files, start=1):
            if not isinstance(item, dict):
                raise ToolExecutionError(f"write_many_files item {index} must be an object")
            path = self._resolve_write_path(task, str(item.get("path") or ""))
            content = str(item.get("content") or "")
            planned.append((path, content))

        written = []
        for path, content in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append({"path": self._display_path(path), "bytes_written": len(content.encode("utf-8"))})
        return {"files_written": written, "count": len(written)}

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

    def _move_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        source = self._resolve_write_path(task, str(arguments.get("source") or ""))
        destination = self._resolve_write_path(task, str(arguments.get("destination") or ""))
        overwrite = bool(arguments.get("overwrite", False))
        if not source.is_file():
            raise ToolExecutionError(f"move_file source is not a file: {self._display_path(source)}")
        if destination.exists() and not overwrite:
            raise ToolExecutionError(f"move_file destination exists: {self._display_path(destination)}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return {"source": self._display_path(source), "destination": self._display_path(destination), "overwritten": overwrite}

    def _delete_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_write_path(task, str(arguments.get("path") or ""))
        if not path.exists():
            return {"path": self._display_path(path), "deleted": False, "reason": "not_found"}
        if not path.is_file():
            raise ToolExecutionError(f"delete_file path is not a file: {self._display_path(path)}")
        path.unlink()
        return {"path": self._display_path(path), "deleted": True}

    def _runtime_capabilities(self) -> dict[str, Any]:
        checks = {
            "python": [sys.executable, "--version"],
            "pytest": [sys.executable, "-m", "pytest", "--version"],
            "uv": ["uv", "--version"],
            "node": ["node", "--version"],
            "npm": ["npm", "--version"],
            "go": ["go", "version"],
            "docker": ["docker", "--version"],
            "git": ["git", "--version"],
        }
        results: dict[str, Any] = {}
        for name, command in checks.items():
            try:
                result = self._run_checked(command, allowed_returncodes=None)
            except WorkerToolError as exc:
                results[name] = {"available": False, "command": command, "error": str(exc)}
                continue
            output = (str(result.get("stdout") or "") or str(result.get("stderr") or "")).strip()
            results[name] = {
                "available": result.get("returncode") == 0,
                "command": command,
                "returncode": result.get("returncode"),
                "version": output.splitlines()[0] if output else "",
            }
        preferred = "python" if results.get("python", {}).get("available") else None
        return {"capabilities": results, "preferred_local_stack": preferred}

    def _run_readonly_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_command = arguments.get("command")
        if isinstance(raw_command, str):
            command = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            command = [str(part) for part in raw_command]
        else:
            raise ToolExecutionError("run_readonly_command requires command as string or list")
        env_overrides, command = self._normalize_readonly_command(command)
        if not self._is_allowed_readonly_command(command):
            raise ToolPermissionError(f"command is not in the readonly allowlist: {' '.join(command)}")
        command = self._canonical_readonly_command(command)
        return self._run_checked(command, allowed_returncodes=None, env_overrides=env_overrides)

    def _run_focused_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _string_or_list(arguments.get("paths"))
        safe_paths = [self._display_path(self._resolve_read_path(path)) for path in paths if path]
        command = [sys.executable, "-m", "pytest", *(safe_paths or ["-q"])]
        return self._run_checked(command, allowed_returncodes=None, env_overrides={"PYTHONPATH": "."})

    def _diff_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path") or "")
        command_suffix = ["--", path] if path else []
        diff = self._run_checked(["git", "diff", *command_suffix])
        changed_files = self._changed_file_names(path=path)
        diff_text = str(diff.get("stdout", ""))
        untracked_diffs = [
            self._new_file_diff(changed_file)
            for changed_file in changed_files
            if changed_file not in diff_text and (self._root / changed_file).is_file()
        ]
        return {
            "changed_files": changed_files,
            "diff": "\n".join(part for part in [diff_text, *untracked_diffs] if part),
            "returncode": diff.get("returncode"),
        }

    def _mutation_scope_check(self, task: Task) -> dict[str, Any]:
        scope = self._mutation_scope_from_task(task)
        changed_files = self._changed_file_names()
        if scope is None:
            return {
                "scope_available": False,
                "changed_files": changed_files,
                "in_scope": [],
                "out_of_scope": changed_files,
                "forbidden_changes": [],
            }

        targets = set(scope.target_paths)
        forbidden = set(scope.forbidden_paths)
        forbidden_globs = set(scope.forbidden_globs)
        in_scope = [
            path for path in changed_files if any(path == target or path.startswith(f"{target}/") for target in targets)
        ]
        out_of_scope = [path for path in changed_files if path not in in_scope]
        forbidden_changes = [
            path
            for path in changed_files
            if any(path == forbidden_path or path.startswith(f"{forbidden_path}/") for forbidden_path in forbidden)
            or any(_matches_repo_glob(path, forbidden_glob) for forbidden_glob in forbidden_globs)
        ]
        return {
            "scope_available": True,
            "target_paths": scope.target_paths,
            "forbidden_paths": scope.forbidden_paths,
            "forbidden_globs": scope.forbidden_globs,
            "changed_files": changed_files,
            "in_scope": in_scope,
            "out_of_scope": out_of_scope,
            "forbidden_changes": forbidden_changes,
            "passed": not out_of_scope and not forbidden_changes,
        }

    def _changed_file_names(self, *, path: str = "") -> list[str]:
        command_suffix = ["--", path] if path else []
        names = self._run_checked(["git", "diff", "--name-only", *command_suffix])
        changed: list[str] = [line.strip() for line in str(names.get("stdout") or "").splitlines() if line.strip()]
        status = self._run_checked(["git", "status", "--short", *command_suffix])
        for line in str(status.get("stdout") or "").splitlines():
            parsed = _parse_git_status_path(line)
            for changed_path in self._expand_changed_status_path(parsed):
                if changed_path not in changed:
                    changed.append(changed_path)
        return sorted(changed)

    def _new_file_diff(self, relative_path: str) -> str:
        path = self._root / relative_path
        if not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")[: self._config.max_file_bytes]
        return "".join(
            difflib.unified_diff(
                [],
                content.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=relative_path,
            )
        )

    def _expand_changed_status_path(self, relative_path: str | None) -> list[str]:
        if not relative_path:
            return []
        path = self._root / relative_path
        if path.is_dir():
            return [
                self._display_path(child)
                for child in sorted(path.rglob("*"))
                if child.is_file() and not self._is_ignored_path(child)
            ]
        return [relative_path.rstrip("/")]

    def _web_fetch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ToolExecutionError("web_fetch requires an http(s) URL")
        request = urllib.request.Request(url, headers={"User-Agent": "allthebest-worker-runtime/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                body = response.read(self._config.max_file_bytes).decode("utf-8", errors="replace")
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body = exc.read(min(self._config.max_file_bytes, 4000)).decode("utf-8", errors="replace")
            raise ToolExecutionError(f"web_fetch failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ToolExecutionError(f"web_fetch request failed: {exc}") from exc

        extractor = _ReadableHTMLExtractor()
        extractor.feed(body)
        text = extractor.text(max_chars=self._config.max_file_bytes)
        return {
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "title": extractor.title,
            "description": extractor.description,
            "content": text,
            "text": text,
            "links": extractor.links[:50],
            "truncated": len(body.encode("utf-8", errors="ignore")) >= self._config.max_file_bytes,
        }

    def _web_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        provider = (self._config.web_search_provider or "disabled").strip().lower()
        if provider in {"", "disabled", "none", "off"}:
            raise ToolUnavailableError("web_search provider is disabled for worker runtime")
        if provider == "brave":
            return self._brave_web_search(arguments)
        if provider != "duckduckgo":
            raise ToolUnavailableError(f"unsupported web_search provider: {provider}")

        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolExecutionError("web_search requires a non-empty query")
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "allthebest-worker-runtime/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
            html = response.read(self._config.max_file_bytes).decode("utf-8", errors="replace")

        parser = _DuckDuckGoHTMLParser(max_results=self._config.web_search_max_results)
        parser.feed(html)
        if not parser.results:
            raise ToolExecutionError(f"web_search returned no parseable results for query: {query}")
        return {"provider": provider, "query": query, "results": parser.results}

    def _brave_web_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        api_key = (self._config.web_search_api_key or "").strip()
        if not api_key:
            raise ToolUnavailableError("Brave web_search provider requires WORKER_WEB_SEARCH_API_KEY")

        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolExecutionError("web_search requires a non-empty query")
        max_results = max(1, min(self._config.web_search_max_results, 20))
        url = (
            "https://api.search.brave.com/res/v1/web/search"
            f"?q={quote_plus(query)}&count={max_results}&extra_snippets=true&safesearch=moderate"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
                "User-Agent": "allthebest-worker-runtime/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                payload = json.loads(response.read(self._config.max_file_bytes).decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read(min(self._config.max_file_bytes, 4000)).decode("utf-8", errors="replace")
            if exc.code in {401, 403, 429}:
                raise ToolUnavailableError(f"Brave web_search failed with HTTP {exc.code}: {body}") from exc
            raise ToolExecutionError(f"Brave web_search failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ToolExecutionError(f"Brave web_search request failed: {exc}") from exc

        raw_results = ((payload.get("web") or {}).get("results") or []) if isinstance(payload, dict) else []
        results = []
        for index, raw_result in enumerate(raw_results[:max_results], start=1):
            if not isinstance(raw_result, dict):
                continue
            result_url = str(raw_result.get("url") or "").strip()
            title = str(raw_result.get("title") or "").strip()
            if not result_url or not title:
                continue
            description = str(raw_result.get("description") or "").strip()
            snippets = [description] if description else []
            extra_snippets = raw_result.get("extra_snippets")
            if isinstance(extra_snippets, list):
                snippets.extend(str(snippet).strip() for snippet in extra_snippets if str(snippet).strip())
            profile = raw_result.get("profile")
            results.append(
                {
                    "rank": index,
                    "title": title,
                    "url": result_url,
                    "snippet": description,
                    "snippets": snippets,
                    "source": profile.get("name") if isinstance(profile, dict) else None,
                    "age": raw_result.get("age"),
                    "language": raw_result.get("language"),
                }
            )

        if not results:
            raise ToolExecutionError(f"Brave web_search returned no usable results for query: {query}")
        return {"provider": "brave", "query": query, "results": results, "raw_query": payload.get("query", {})}

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

    def _normalize_readonly_command(self, command: list[str]) -> tuple[dict[str, str], list[str]]:
        if not command:
            raise ToolExecutionError("run_readonly_command requires a non-empty command")

        if command[0] == "env":
            command = command[1:]
            if command and command[0].startswith("-"):
                raise ToolPermissionError("env options are not allowed in readonly commands")
            return self._split_env_assignments(command)

        if len(command) == 3 and command[0] == "sh" and command[1] == "-c":
            inner = command[2]
            if _contains_shell_control(inner):
                raise ToolPermissionError("shell control operators are not allowed in readonly commands")
            try:
                inner_command = shlex.split(inner)
            except ValueError as exc:
                raise ToolExecutionError("could not parse sh -c readonly command") from exc
            return self._split_env_assignments(inner_command)

        return self._split_env_assignments(command)

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

    def _canonical_readonly_command(self, command: list[str]) -> list[str]:
        if command and command[0] == "pytest":
            return [sys.executable, "-m", "pytest", *command[1:]]
        return command

    def _resolve_read_path(self, raw_path: str) -> Path:
        if not raw_path:
            raise ToolExecutionError("path is required")
        path = (self._root / self._normalize_worker_root_relative_path(raw_path)).resolve()
        if not path.is_relative_to(self._root):
            raise ToolPermissionError("path escapes worker root")
        return path

    def _normalize_worker_root_relative_path(self, raw_path: str) -> str:
        normalized = raw_path.strip()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized in {"", "."}:
            return "."
        root_name = self._root.name
        if normalized == root_name:
            return "."
        prefix = f"{root_name}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :] or "."
        return normalized

    def _resolve_write_path(self, task: Task, raw_path: str) -> Path:
        path = self._resolve_read_path(raw_path)
        scope = self.validate_write_scope(task)
        allowed = [self._resolve_read_path(raw_path) for raw_path in scope["write_scope_paths"]]
        if not any(path == allowed_path or path.is_relative_to(allowed_path) for allowed_path in allowed):
            raise ToolPermissionError(f"write path is outside allowed scope: {self._display_path(path)}")
        forbidden = [self._resolve_read_path(raw_path) for raw_path in scope["forbidden_paths"]]
        if any(path == forbidden_path or path.is_relative_to(forbidden_path) for forbidden_path in forbidden):
            raise ToolPermissionError(f"write path is inside forbidden scope: {self._display_path(path)}")
        relative_path = self._display_path(path)
        if any(_matches_repo_glob(relative_path, pattern) for pattern in scope.get("forbidden_globs", [])):
            raise ToolPermissionError(f"write path is inside forbidden scope: {relative_path}")
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
            except WorkerToolError as exc:
                raise ToolPermissionError(f"invalid write scope path: {raw_path}") from exc
        return resolved

    def _paths_from_artifact(self, artifact: ArtifactPayload) -> list[str]:
        try:
            return resolve_mutation_scope_proposal(
                artifact.content,
                source_artifact_id=artifact.id,
            ).write_scope_paths
        except ValueError as exc:
            if artifact.id in STRICT_WRITE_SCOPE_ARTIFACT_IDS:
                raise ToolPermissionError(f"invalid write scope artifact {artifact.id}: {exc}") from exc
            return extract_repo_path_candidates(artifact.content)

    def _mutation_scope_from_task(self, task: Task) -> MutationScope | None:
        write_scope = task.metadata.get("write_scope")
        if isinstance(write_scope, dict):
            try:
                return resolve_mutation_scope_proposal(write_scope)
            except ValueError:
                return None
        for artifact in task.input_artifacts:
            if artifact.id != "mutation_scope":
                continue
            try:
                return resolve_mutation_scope_proposal(
                    artifact.content,
                    source_artifact_id=artifact.id,
                )
            except ValueError:
                return None
        return None

    def _forbidden_write_paths(self, task: Task) -> list[Path]:
        raw_paths: list[str] = []
        write_scope = task.metadata.get("write_scope")
        if isinstance(write_scope, dict):
            raw_paths.extend(
                str(path)
                for path in write_scope.get("forbidden_paths") or []
                if not _has_glob_meta(str(path))
            )
        resolved: list[Path] = []
        for raw_path in raw_paths:
            try:
                resolved.append(self._resolve_read_path(raw_path))
            except WorkerToolError as exc:
                raise ToolPermissionError(f"invalid forbidden write scope path: {raw_path}") from exc
        return resolved

    def _forbidden_write_globs(self, task: Task) -> list[str]:
        raw_globs: list[str] = []
        write_scope = task.metadata.get("write_scope")
        if isinstance(write_scope, dict):
            raw_globs.extend(str(pattern) for pattern in write_scope.get("forbidden_globs") or [])
            raw_globs.extend(
                str(path)
                for path in write_scope.get("forbidden_paths") or []
                if _has_glob_meta(str(path))
            )
        globs: list[str] = []
        seen: set[str] = set()
        for raw_glob in raw_globs:
            normalized = _normalize_repo_glob(raw_glob)
            if normalized is None:
                raise ToolPermissionError(f"invalid forbidden write scope glob: {raw_glob}")
            if normalized not in seen:
                seen.add(normalized)
                globs.append(normalized)
        return globs

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
    if value == "boolean":
        return {"type": "boolean"}
    if value == "file_write_array":
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
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


def _has_glob_meta(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _normalize_repo_glob(value: str) -> str | None:
    raw = value.strip().strip("`'\".,;)]}")
    if raw.startswith("./"):
        raw = raw[2:]
    if not raw or any(char.isspace() for char in raw):
        return None
    if not _has_glob_meta(raw):
        return None
    if raw.startswith(("-", "~", "/")) or "://" in raw or "\\" in raw:
        return None
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        return None
    return raw


def _matches_repo_glob(path: str, pattern: str) -> bool:
    normalized = _normalize_repo_glob(pattern)
    if normalized is None:
        return False
    return fnmatch.fnmatchcase(path, normalized) or fnmatch.fnmatchcase(Path(path).name, normalized)


def _parse_git_status_path(line: str) -> str | None:
    if not line or len(line) < 4:
        return None
    if line[:2] != "??" and line[1] == " ":
        return None
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return path or None


def _looks_like_test_path(value: str) -> bool:
    path = Path(value)
    lowered = value.lower()
    return (
        "test" in path.name.lower()
        or "/tests/" in f"/{lowered}"
        or lowered.startswith("tests/")
        or lowered.endswith((".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx"))
    )


def _string_or_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _contains_shell_control(command: str) -> bool:
    forbidden_fragments = ("&&", "||", "$(", "`", "\n")
    if any(fragment in command for fragment in forbidden_fragments):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    return any(token in {";", "|", "<", ">", ">>", "&"} for token in tokens)


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self, *, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_results = max(1, max_results)
        self.results: list[dict[str, Any]] = []
        self._capture: str | None = None
        self._capture_depth = 0
        self._pending_url: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if tag == "a" and "result__a" in classes and len(self.results) < self.max_results:
            self._capture = "title"
            self._capture_depth = 1
            self._pending_url = _normalize_duckduckgo_url(attr_map.get("href", ""))
            self._text_parts = []
            return
        if "result__snippet" in classes and self.results:
            self._capture = "snippet"
            self._capture_depth = 1
            self._text_parts = []
            return
        if self._capture:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        self._capture_depth -= 1
        if self._capture_depth > 0:
            return
        text = " ".join("".join(self._text_parts).split())
        if self._capture == "title" and self._pending_url and text:
            self.results.append(
                {
                    "rank": len(self.results) + 1,
                    "title": unescape(text),
                    "url": self._pending_url,
                    "snippet": "",
                }
            )
        elif self._capture == "snippet" and text and self.results:
            self.results[-1]["snippet"] = unescape(text)
        self._capture = None
        self._pending_url = None
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text_parts.append(data)


def _normalize_duckduckgo_url(raw_url: str) -> str:
    if raw_url.startswith("//"):
        raw_url = f"https:{raw_url}"
    raw_url = urljoin("https://duckduckgo.com", raw_url)
    parsed = urlparse(raw_url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        redirected = parse_qs(parsed.query).get("uddg", [""])[0]
        if redirected:
            return unquote(redirected)
    return raw_url


class _ReadableHTMLExtractor(HTMLParser):
    BLOCKED_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.description: str | None = None
        self.links: list[dict[str, str]] = []
        self._blocked_depth = 0
        self._capture_title = False
        self._current_link: str | None = None
        self._current_link_text: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag in self.BLOCKED_TAGS:
            self._blocked_depth += 1
            return
        if self._blocked_depth:
            return
        if tag == "title":
            self._capture_title = True
            return
        if tag == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.description:
                self.description = _clean_text(attr_map.get("content", ""))
            return
        if tag == "a" and attr_map.get("href"):
            self._current_link = attr_map["href"]
            self._current_link_text = []
        if tag in {"p", "div", "section", "article", "header", "footer", "br", "li", "tr", "h1", "h2", "h3"}:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.BLOCKED_TAGS and self._blocked_depth:
            self._blocked_depth -= 1
            return
        if self._blocked_depth:
            return
        if tag == "title":
            self._capture_title = False
            if self.title:
                self.title = _clean_text(self.title)
            return
        if tag == "a" and self._current_link:
            text = _clean_text("".join(self._current_link_text))
            if text:
                self.links.append({"url": self._current_link, "text": text})
            self._current_link = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        if self._capture_title:
            self.title = (self.title or "") + data
            return
        if self._current_link:
            self._current_link_text.append(data)
        self._text_parts.append(data)

    def text(self, *, max_chars: int) -> str:
        return _clean_text(" ".join(self._text_parts))[:max_chars]


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())
