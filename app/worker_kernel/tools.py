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
import tomllib
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
    MutationOperationDenial,
    MutationScope,
    Task,
    WritePolicy,
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


class MutationOperationDeniedError(WorkerToolError):
    code = "mutation_operation_denied"

    def __init__(self, denial: MutationOperationDenial) -> None:
        self.denial = denial
        super().__init__(denial.message)


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
                    _tool_spec("write_file", "Write a full file inside approved write policy.", "write_files", {"path": "string", "content": "string"}),
                    _tool_spec("write_many_files", "Write multiple full files inside approved write policy in one atomic preflighted batch.", "write_files", {"files": "file_write_array"}),
                    _tool_spec(
                        "write_json_manifest",
                        "Primary tool for JSON manifests, indexes, inventories, and reports with exact keys/counts. It writes the file, enforces required keys, and checks total/count reconciliation.",
                        "write_files",
                        {"path": "string", "payload": "json_object", "required_keys": "string_array", "total_key": "string", "count_keys": "string_array"},
                    ),
                    _tool_spec("apply_file_operations", "Preflight and apply a compact batch of move/write/replace/delete/create_directory operations with an idempotent operation ledger.", "write_files", {"operations": "file_operation_array"}),
                    _tool_spec("replace_in_file", "Replace one exact text occurrence inside approved write policy.", "write_files", {"path": "string", "old": "string", "new": "string"}),
                    _tool_spec("move_file", "Move one file when both source and destination are inside approved write policy.", "write_files", {"source": "string", "destination": "string", "overwrite": "boolean"}),
                    _tool_spec("delete_file", "Delete one file inside approved write policy.", "write_files", {"path": "string"}),
                ]
            )
        if task.permissions.run_commands:
            tools.extend(
                [
                    _tool_spec("runtime_capabilities", "Return structured availability/version checks for common local runtimes and package/test tools.", "run_commands", {}),
                    _tool_spec("run_readonly_command", "Run an allowlisted readonly verification command.", "run_commands", {"command": "string_or_string_array"}),
                    _tool_spec("run_focused_tests", "Run pytest for selected repo-relative test paths with PYTHONPATH set to the repo root.", "run_commands", {"paths": "string_or_string_array"}),
                    _tool_spec("run_project_tests", "Run the repository's pytest command using the detected package manager and dev extras when needed.", "run_commands", {"paths": "string_or_string_array"}),
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
        policy = self._write_policy(task)
        allowed = self._strict_allowed_write_paths(task, policy)
        if not allowed and not _is_bounded_mutation_task(task):
            raise ToolUnavailableError("write_files was allowed but no write scope paths were provided")
        forbidden = self._forbidden_write_paths(task, policy)
        forbidden_globs = self._forbidden_write_globs(task, policy)
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
        if tool_name == "write_json_manifest":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._write_json_manifest(task, arguments)
        if tool_name == "apply_file_operations":
            self._require(task.permissions.write_files, "write_files", tool_name)
            return self._apply_file_operations(task, arguments)
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
        if tool_name == "run_project_tests":
            self._require(task.permissions.run_commands, "run_commands", tool_name)
            return self._run_project_tests(arguments)
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
        if not path.exists():
            return {"path": self._display_path(path), "exists": False, "entries": [], "error": "not_found"}
        if not path.is_dir():
            return {"path": self._display_path(path), "exists": True, "entries": [], "error": "not_directory"}
        entries = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if self._is_ignored_path(child):
                continue
            entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file"})
            if len(entries) >= 200:
                break
        return {"path": self._display_path(path), "exists": True, "entries": entries}

    def _repo_snapshot(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start = self._resolve_read_path(str(arguments.get("path") or "."))
        if not start.exists():
            return {
                "path": self._display_path(start),
                "exists": False,
                "directories": [],
                "files": [],
                "is_empty": True,
                "test_candidates": [],
                "config_files": [],
                "git_status": {},
                "error": "not_found",
            }
        if not start.is_dir():
            return {
                "path": self._display_path(start),
                "exists": True,
                "directories": [],
                "files": [self._display_path(start)],
                "is_empty": False,
                "test_candidates": [self._display_path(start)] if _looks_like_test_path(self._display_path(start)) else [],
                "config_files": [],
                "git_status": {},
                "error": "not_directory",
            }

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
            "exists": True,
            "directories": sorted(dirs)[:100],
            "files": files[:300],
            "is_empty": not files and not dirs,
            "test_candidates": test_candidates[:50],
            "config_files": config_files[:30],
            "git_status": git_status,
        }

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_read_path(str(arguments.get("path") or ""))
        if not path.exists():
            return {
                "path": self._display_path(path),
                "exists": False,
                "content": "",
                "truncated": False,
                "error": "not_found",
            }
        if not path.is_file():
            return {
                "path": self._display_path(path),
                "exists": True,
                "content": "",
                "truncated": False,
                "error": "not_file",
            }
        content = path.read_text(encoding="utf-8", errors="replace")[: self._config.max_file_bytes]
        return {
            "path": self._display_path(path),
            "exists": True,
            "content": content,
            "truncated": path.stat().st_size > len(content),
        }

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
        path = self._preflight_write_operation(
            task,
            tool_name="write_file",
            raw_paths=[str(arguments.get("path") or "")],
        )[0]
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

        raw_paths: list[str] = []
        contents: list[str] = []
        for index, item in enumerate(raw_files, start=1):
            if not isinstance(item, dict):
                raise ToolExecutionError(f"write_many_files item {index} must be an object")
            raw_paths.append(str(item.get("path") or ""))
            contents.append(str(item.get("content") or ""))
        planned = list(
            zip(
                self._preflight_write_operation(
                    task,
                    tool_name="write_many_files",
                    raw_paths=raw_paths,
                ),
                contents,
                strict=False,
            )
        )

        written = []
        for path, content in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append({"path": self._display_path(path), "bytes_written": len(content.encode("utf-8"))})
        return {"files_written": written, "count": len(written)}

    def _write_json_manifest(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._preflight_write_operation(
            task,
            tool_name="write_json_manifest",
            raw_paths=[str(arguments.get("path") or "")],
        )[0]
        payload = arguments.get("payload")
        if not isinstance(payload, dict):
            raise ToolExecutionError("write_json_manifest requires payload to be an object")

        required_keys = _coerce_string_list(arguments.get("required_keys"))
        if not required_keys:
            required_keys = _coerce_string_list(task.metadata.get("required_json_keys"))
        if not required_keys:
            required_keys = sorted(str(key) for key in payload)
        missing_keys = [key for key in required_keys if key not in payload]
        if missing_keys:
            self._raise_repairable_manifest_denial(
                task=task,
                tool_name="write_json_manifest",
                code="manifest_missing_required_keys",
                message="write_json_manifest payload is missing required keys: " + ", ".join(missing_keys),
                path=path,
                missing_keys=missing_keys,
            )

        total_key = str(arguments.get("total_key") or "").strip()
        if not total_key:
            total_key = _infer_manifest_total_key(required_keys=required_keys, payload=payload)
        count_keys = _coerce_string_list(arguments.get("count_keys"))
        if not count_keys:
            count_keys = _infer_manifest_count_keys(
                required_keys=required_keys,
                payload=payload,
                total_key=total_key,
            )

        non_list_count_keys = [key for key in count_keys if not isinstance(payload.get(key), list)]
        if non_list_count_keys:
            self._raise_repairable_manifest_denial(
                task=task,
                tool_name="write_json_manifest",
                code="manifest_count_key_not_list",
                message="manifest count keys must contain list values: " + ", ".join(non_list_count_keys),
                path=path,
                missing_keys=[],
            )

        counted_total = sum(len(payload.get(key) or []) for key in count_keys)
        counts_match = True
        if total_key:
            total_value = payload.get(total_key)
            if not isinstance(total_value, int):
                self._raise_repairable_manifest_denial(
                    task=task,
                    tool_name="write_json_manifest",
                    code="manifest_total_not_integer",
                    message=f"manifest total key {total_key} must be an integer",
                    path=path,
                    missing_keys=[],
                )
            counts_match = total_value == counted_total
            if not counts_match:
                self._raise_repairable_manifest_denial(
                    task=task,
                    tool_name="write_json_manifest",
                    code="manifest_total_mismatch",
                    message=(
                        f"manifest {total_key}={total_value} does not match counted "
                        f"items {counted_total} from keys: {', '.join(count_keys)}"
                    ),
                    path=path,
                    missing_keys=[],
                )

        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        path.write_text(content, encoding="utf-8")
        fields_present = sorted(str(key) for key in payload)
        return {
            "path": self._display_path(path),
            "manifest_path": self._display_path(path),
            "payload": payload,
            "required_keys": required_keys,
            "fields_present": fields_present,
            "missing_fields": [],
            "counts_match": counts_match,
            "total_key": total_key or None,
            "total_value": payload.get(total_key) if total_key else None,
            "total_artifacts": payload.get(total_key) if total_key else None,
            "count_keys": count_keys,
            "counted_total": counted_total,
            "bytes_written": len(content.encode("utf-8")),
        }

    def _apply_file_operations(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_operations = _coerce_file_operations(arguments)
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ToolExecutionError("apply_file_operations requires a non-empty operations array")
        if len(raw_operations) > 50:
            raise ToolExecutionError("apply_file_operations supports at most 50 operations per call")

        operations: list[dict[str, Any]] = []
        raw_paths: list[str] = []
        for index, raw_operation in enumerate(raw_operations, start=1):
            if not isinstance(raw_operation, dict):
                raise ToolExecutionError(f"apply_file_operations item {index} must be an object")
            action = str(raw_operation.get("action") or raw_operation.get("op") or "").strip().lower()
            if action in {"mkdir", "create_dir"}:
                action = "create_directory"
            if action not in {"move", "write", "replace", "delete", "create_directory"}:
                raise ToolExecutionError(f"apply_file_operations item {index} has unsupported action: {action}")
            operation = {"index": index, "action": action, "raw": raw_operation}
            if action == "move":
                operation["source"] = str(raw_operation.get("source") or raw_operation.get("from") or "")
                operation["destination"] = str(
                    raw_operation.get("destination") or raw_operation.get("to") or raw_operation.get("target") or ""
                )
                operation["overwrite"] = bool(raw_operation.get("overwrite", False))
                raw_paths.extend([operation["source"], operation["destination"]])
            elif action == "write":
                operation["path"] = str(raw_operation.get("path") or raw_operation.get("file") or "")
                operation["content"] = str(raw_operation.get("content") or "")
                operation["overwrite"] = bool(raw_operation.get("overwrite", True))
                raw_paths.append(operation["path"])
            elif action == "replace":
                operation["path"] = str(raw_operation.get("path") or raw_operation.get("file") or "")
                operation["old"] = str(raw_operation.get("old") or "")
                operation["new"] = str(raw_operation.get("new") or "")
                raw_paths.append(operation["path"])
            elif action == "delete":
                operation["path"] = str(raw_operation.get("path") or raw_operation.get("file") or "")
                raw_paths.append(operation["path"])
            else:
                operation["path"] = str(raw_operation.get("path") or raw_operation.get("directory") or "")
                raw_paths.append(operation["path"])
            operations.append(operation)

        resolved_paths = self._preflight_write_operation(
            task,
            tool_name="apply_file_operations",
            raw_paths=raw_paths,
        )
        path_index = 0
        for operation in operations:
            if operation["action"] == "move":
                operation["source_path"] = resolved_paths[path_index]
                operation["destination_path"] = resolved_paths[path_index + 1]
                path_index += 2
            else:
                operation["resolved_path"] = resolved_paths[path_index]
                path_index += 1

        denial = self._preflight_file_operations(task, operations)
        if denial is not None:
            raise denial

        ledger: list[dict[str, Any]] = []
        for operation in operations:
            ledger.append(self._apply_one_file_operation(operation))
        applied = [item for item in ledger if item["status"] == "applied"]
        already_done = [item for item in ledger if item["status"] == "already_done"]
        skipped = [item for item in ledger if item["status"] == "skipped"]
        return {
            "operation_count": len(ledger),
            "applied_count": len(applied),
            "already_done_count": len(already_done),
            "skipped_count": len(skipped),
            "changed_paths": sorted(
                {
                    path
                    for item in applied
                    for path in item.get("paths", [])
                    if path
                }
            ),
            "operations": ledger,
        }

    def _replace_in_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._preflight_write_operation(
            task,
            tool_name="replace_in_file",
            raw_paths=[str(arguments.get("path") or "")],
        )[0]
        old = str(arguments.get("old") or "")
        new = str(arguments.get("new") or "")
        if not old:
            raise ToolExecutionError("replace_in_file requires a non-empty old value")
        if not path.is_file():
            self._raise_repairable_write_denial(
                task=task,
                tool_name="replace_in_file",
                code="replace_target_not_file",
                message=f"replace_in_file target is not a file: {self._display_path(path)}",
                touched_paths=[self._display_path(path)],
                rejected_paths=[self._display_path(path)],
            )
        content = path.read_text(encoding="utf-8")
        if old not in content:
            self._raise_repairable_write_denial(
                task=task,
                tool_name="replace_in_file",
                code="replace_target_text_not_found",
                message="replace_in_file old value was not found",
                touched_paths=[self._display_path(path)],
                rejected_paths=[self._display_path(path)],
            )
        updated = content.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        return {"path": self._display_path(path), "replacements": 1}

    def _move_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        source, destination = self._preflight_write_operation(
            task,
            tool_name="move_file",
            raw_paths=[
                str(arguments.get("source") or ""),
                str(arguments.get("destination") or ""),
            ],
        )
        overwrite = bool(arguments.get("overwrite", False))
        if source == destination:
            return {
                "source": self._display_path(source),
                "destination": self._display_path(destination),
                "overwritten": False,
                "skipped": True,
                "reason": "source_equals_destination",
            }
        if not source.is_file():
            if destination.is_file():
                return {
                    "source": self._display_path(source),
                    "destination": self._display_path(destination),
                    "overwritten": False,
                    "already_done": True,
                    "skipped": True,
                    "reason": "source_missing_destination_exists",
                }
            self._raise_repairable_write_denial(
                task=task,
                tool_name="move_file",
                code="move_source_not_file",
                message=f"move_file source is not a file: {self._display_path(source)}",
                touched_paths=[self._display_path(source), self._display_path(destination)],
                rejected_paths=[self._display_path(source)],
            )
        if destination.exists() and not overwrite:
            self._raise_repairable_write_denial(
                task=task,
                tool_name="move_file",
                code="move_destination_exists",
                message=(
                    f"move_file destination exists: {self._display_path(destination)}. "
                    "Set overwrite=true only when replacing it is intentional, choose another destination, "
                    "or skip the move if the destination already satisfies the task."
                ),
                touched_paths=[self._display_path(source), self._display_path(destination)],
                rejected_paths=[self._display_path(destination)],
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return {"source": self._display_path(source), "destination": self._display_path(destination), "overwritten": overwrite}

    def _delete_file(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._preflight_write_operation(
            task,
            tool_name="delete_file",
            raw_paths=[str(arguments.get("path") or "")],
        )[0]
        if not path.exists():
            return {"path": self._display_path(path), "deleted": False, "reason": "not_found"}
        if not path.is_file():
            self._raise_repairable_write_denial(
                task=task,
                tool_name="delete_file",
                code="delete_target_not_file",
                message=f"delete_file path is not a file: {self._display_path(path)}",
                touched_paths=[self._display_path(path)],
                rejected_paths=[self._display_path(path)],
            )
        path.unlink()
        return {"path": self._display_path(path), "deleted": True}

    def _preflight_file_operations(
        self,
        task: Task,
        operations: list[dict[str, Any]],
    ) -> MutationOperationDeniedError | None:
        rejected_paths: list[str] = []
        messages: list[str] = []
        for operation in operations:
            action = operation["action"]
            if action == "move":
                source = operation["source_path"]
                destination = operation["destination_path"]
                if source == destination:
                    continue
                if not source.is_file() and not destination.is_file():
                    rejected_paths.append(self._display_path(source))
                    messages.append(f"move source is not a file: {self._display_path(source)}")
                if source.is_file() and destination.exists() and not operation["overwrite"]:
                    rejected_paths.append(self._display_path(destination))
                    messages.append(
                        f"move destination exists: {self._display_path(destination)}; set overwrite=true or skip"
                    )
            elif action == "write":
                path = operation["resolved_path"]
                if path.exists() and not path.is_file():
                    rejected_paths.append(self._display_path(path))
                    messages.append(f"write target is not a file: {self._display_path(path)}")
                if path.exists() and not operation["overwrite"]:
                    rejected_paths.append(self._display_path(path))
                    messages.append(
                        f"write target exists: {self._display_path(path)}; set overwrite=true or skip"
                    )
            elif action == "replace":
                path = operation["resolved_path"]
                if not operation["old"]:
                    rejected_paths.append(self._display_path(path))
                    messages.append("replace operation requires a non-empty old value")
                elif not path.is_file():
                    rejected_paths.append(self._display_path(path))
                    messages.append(f"replace target is not a file: {self._display_path(path)}")
                elif operation["old"] not in path.read_text(encoding="utf-8"):
                    rejected_paths.append(self._display_path(path))
                    messages.append(f"replace old value was not found: {self._display_path(path)}")
            elif action == "delete":
                path = operation["resolved_path"]
                if path.exists() and not path.is_file():
                    rejected_paths.append(self._display_path(path))
                    messages.append(f"delete target is not a file: {self._display_path(path)}")
            elif action == "create_directory":
                path = operation["resolved_path"]
                if path.exists() and not path.is_dir():
                    rejected_paths.append(self._display_path(path))
                    messages.append(f"create_directory target is not a directory: {self._display_path(path)}")
        if not rejected_paths:
            return None
        touched_paths = sorted(
            {
                path
                for operation in operations
                for path in _operation_display_paths(operation, self)
            }
        )
        return self._operation_denial(
            task=task,
            policy=self._write_policy(task),
            tool_name="apply_file_operations",
            code="file_operation_batch_denied",
            message="; ".join(messages),
            touched_paths=touched_paths,
            rejected_paths=sorted(set(rejected_paths)),
        )

    def _apply_one_file_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        action = operation["action"]
        if action == "move":
            source = operation["source_path"]
            destination = operation["destination_path"]
            paths = [self._display_path(source), self._display_path(destination)]
            if source == destination:
                return {
                    "index": operation["index"],
                    "action": action,
                    "status": "skipped",
                    "paths": paths,
                    "summary": "source equals destination",
                }
            if not source.is_file() and destination.is_file():
                return {
                    "index": operation["index"],
                    "action": action,
                    "status": "already_done",
                    "paths": paths,
                    "summary": "source missing and destination already exists",
                }
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            return {
                "index": operation["index"],
                "action": action,
                "status": "applied",
                "paths": paths,
                "summary": "file moved",
            }
        path = operation["resolved_path"]
        display_path = self._display_path(path)
        if action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            content = str(operation.get("content") or "")
            path.write_text(content, encoding="utf-8")
            return {
                "index": operation["index"],
                "action": action,
                "status": "applied",
                "paths": [display_path],
                "summary": f"wrote {len(content.encode('utf-8'))} bytes",
            }
        if action == "replace":
            content = path.read_text(encoding="utf-8")
            path.write_text(content.replace(operation["old"], operation["new"], 1), encoding="utf-8")
            return {
                "index": operation["index"],
                "action": action,
                "status": "applied",
                "paths": [display_path],
                "summary": "replaced one occurrence",
            }
        if action == "delete":
            if not path.exists():
                return {
                    "index": operation["index"],
                    "action": action,
                    "status": "already_done",
                    "paths": [display_path],
                    "summary": "file already absent",
                }
            path.unlink()
            return {
                "index": operation["index"],
                "action": action,
                "status": "applied",
                "paths": [display_path],
                "summary": "file deleted",
            }
        path.mkdir(parents=True, exist_ok=True)
        return {
            "index": operation["index"],
            "action": action,
            "status": "applied",
            "paths": [display_path],
            "summary": "directory created or already existed",
        }

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
        result = self._run_checked(command, allowed_returncodes=None, env_overrides=env_overrides)
        if command[:2] == ["uv", "run"]:
            result["detected_command_source"] = self._project_test_command_source(command)
        return result

    def _run_focused_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _string_or_list(arguments.get("paths"))
        safe_paths = [self._display_path(self._resolve_read_path(path)) for path in paths if path]
        command = [sys.executable, "-m", "pytest", *(safe_paths or ["-q"])]
        return self._run_checked(command, allowed_returncodes=None, env_overrides={"PYTHONPATH": "."})

    def _run_project_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _string_or_list(arguments.get("paths"))
        safe_paths = [self._display_path(self._resolve_read_path(path)) for path in paths if path]
        pytest_args = safe_paths or ["-q"]
        command = self._project_pytest_command(pytest_args)
        env_overrides = {"PYTHONPATH": "."} if command[0] != "uv" else None
        result = self._run_checked(command, allowed_returncodes=None, env_overrides=env_overrides)
        result["detected_command_source"] = self._project_test_command_source(command)
        return result

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

        targets = set(scope.write_scope_paths)
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
            "write_scope_paths": scope.write_scope_paths,
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
        if command[:2] == ["uv", "run"]:
            return _is_allowed_uv_pytest_command(command[2:])
        if command[0] == "pytest":
            return True
        executable = Path(command[0]).name
        if executable in {"python", "python3"} and command[1:3] == ["-m", "pytest"]:
            return True
        return False

    def _canonical_readonly_command(self, command: list[str]) -> list[str]:
        if command and command[0] == "pytest":
            return [sys.executable, "-m", "pytest", *command[1:]]
        if command[:2] == ["uv", "run"]:
            return self._canonical_uv_pytest_command(command)
        return command

    def _canonical_uv_pytest_command(self, command: list[str]) -> list[str]:
        arguments = command[2:]
        if not _is_allowed_uv_pytest_command(arguments):
            return command
        if _uv_arguments_select_extra(arguments):
            return command
        pytest_extra = self._pyproject_pytest_extra()
        if not pytest_extra:
            return command
        insert_at = 2 + _uv_run_option_prefix_length(arguments)
        return [*command[:insert_at], "--extra", pytest_extra, *command[insert_at:]]

    def _project_pytest_command(self, pytest_args: list[str]) -> list[str]:
        if (self._root / "pyproject.toml").is_file():
            pytest_extra = self._pyproject_pytest_extra()
            if pytest_extra:
                return ["uv", "run", "--extra", pytest_extra, "pytest", *pytest_args]
            return ["uv", "run", "pytest", *pytest_args]
        return [sys.executable, "-m", "pytest", *pytest_args]

    def _project_test_command_source(self, command: list[str]) -> str:
        if command[:2] == ["uv", "run"]:
            arguments = command[2:]
            if "--all-extras" in arguments:
                return "pyproject_all_extras"
            if "--extra" in arguments:
                index = arguments.index("--extra")
                if index + 1 < len(arguments):
                    return f"pyproject_optional_{arguments[index + 1]}_extra"
            return "pyproject_uv"
        return "python_module_pytest"

    def _pyproject_pytest_extra(self) -> str | None:
        pyproject = self._root / "pyproject.toml"
        if not pyproject.is_file():
            return None
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        optional = data.get("project", {}).get("optional-dependencies", {})
        if not isinstance(optional, dict):
            return None
        extras_with_pytest = [
            str(extra)
            for extra, dependencies in optional.items()
            if isinstance(dependencies, list)
            and any(str(dependency).split(";", 1)[0].strip().lower().startswith("pytest") for dependency in dependencies)
        ]
        for preferred in ("dev", "test", "tests", "testing"):
            if preferred in extras_with_pytest:
                return preferred
        return sorted(extras_with_pytest)[0] if extras_with_pytest else None

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

    def _preflight_write_operation(self, task: Task, *, tool_name: str, raw_paths: list[str]) -> list[Path]:
        paths = [self._resolve_read_path(raw_path) for raw_path in raw_paths]
        display_paths = [self._display_path(path) for path in paths]
        unique_paths = sorted(set(display_paths))
        policy = self._write_policy(task)
        if len(unique_paths) > policy.step_max_files:
            raise self._operation_denial(
                task=task,
                policy=policy,
                tool_name=tool_name,
                code="write_operation_too_broad",
                message=(
                    f"{tool_name} touches {len(unique_paths)} paths, "
                    f"exceeding step_max_files={policy.step_max_files}"
                ),
                touched_paths=unique_paths,
                rejected_paths=unique_paths,
            )
        if tool_name == "write_many_files" and len(unique_paths) > policy.batch_max_files:
            raise self._operation_denial(
                task=task,
                policy=policy,
                tool_name=tool_name,
                code="write_batch_too_broad",
                message=(
                    f"{tool_name} touches {len(unique_paths)} paths, "
                    f"exceeding batch_max_files={policy.batch_max_files}"
                ),
                touched_paths=unique_paths,
                rejected_paths=unique_paths,
            )

        allowed = self._strict_allowed_write_paths(task, policy)
        if allowed:
            rejected = [
                path
                for path in paths
                if not any(path == allowed_path or path.is_relative_to(allowed_path) for allowed_path in allowed)
            ]
            if rejected:
                if not _is_bounded_mutation_task(task):
                    raise ToolPermissionError(
                        "write path is outside allowed scope: "
                        + ", ".join(self._display_path(path) for path in rejected)
                    )
                raise self._operation_denial(
                    task=task,
                    policy=policy,
                    tool_name=tool_name,
                    code="write_path_outside_strict_scope",
                    message=(
                        f"{tool_name} includes paths outside strict allowed scope: "
                        f"{', '.join(self._display_path(path) for path in rejected)}"
                    ),
                    touched_paths=unique_paths,
                    rejected_paths=[self._display_path(path) for path in rejected],
                )
        elif not _is_bounded_mutation_task(task):
            raise ToolUnavailableError("write_files was allowed but no write scope paths were provided")

        forbidden = self._forbidden_write_paths(task, policy)
        rejected_forbidden = [
            path
            for path in paths
            if any(path == forbidden_path or path.is_relative_to(forbidden_path) for forbidden_path in forbidden)
        ]
        if rejected_forbidden:
            raise ToolPermissionError(
                "write path is inside forbidden scope: "
                + ", ".join(self._display_path(path) for path in rejected_forbidden)
            )
        forbidden_globs = self._forbidden_write_globs(task, policy)
        rejected_globs = [
            path for path in paths if any(_matches_repo_glob(self._display_path(path), pattern) for pattern in forbidden_globs)
        ]
        if rejected_globs:
            raise ToolPermissionError(
                "write path is inside forbidden scope: "
                + ", ".join(self._display_path(path) for path in rejected_globs)
            )
        return paths

    def _operation_denial(
        self,
        *,
        task: Task,
        policy: WritePolicy,
        tool_name: str,
        code: str,
        message: str,
        touched_paths: list[str],
        rejected_paths: list[str],
    ) -> MutationOperationDeniedError:
        return MutationOperationDeniedError(
            MutationOperationDenial(
                code=code,
                message=message,
                tool_name=tool_name,
                touched_paths=touched_paths,
                rejected_paths=rejected_paths,
                repairable=True,
                policy=policy.model_dump(mode="json"),
                metadata={
                    "step_id": task.step_id,
                    "worker_type": task.worker_type,
                    "instruction": "Revise the operation to satisfy write_policy. Do not restart analysis.",
                },
            )
        )

    def _raise_repairable_write_denial(
        self,
        *,
        task: Task,
        tool_name: str,
        code: str,
        message: str,
        touched_paths: list[str],
        rejected_paths: list[str],
    ) -> None:
        if not _is_bounded_mutation_task(task):
            raise ToolExecutionError(message)
        raise self._operation_denial(
            task=task,
            policy=self._write_policy(task),
            tool_name=tool_name,
            code=code,
            message=message,
            touched_paths=touched_paths,
            rejected_paths=rejected_paths,
        )

    def _raise_repairable_manifest_denial(
        self,
        *,
        task: Task,
        tool_name: str,
        code: str,
        message: str,
        path: Path,
        missing_keys: list[str],
    ) -> None:
        if not _is_bounded_mutation_task(task):
            raise ToolExecutionError(message)
        policy = self._write_policy(task)
        raise MutationOperationDeniedError(
            MutationOperationDenial(
                code=code,
                message=message,
                tool_name=tool_name,
                touched_paths=[self._display_path(path)],
                rejected_paths=[self._display_path(path)],
                repairable=True,
                policy=policy.model_dump(mode="json"),
                metadata={
                    "step_id": task.step_id,
                    "worker_type": task.worker_type,
                    "missing_keys": missing_keys,
                    "required_json_keys": _coerce_string_list(task.metadata.get("required_json_keys")),
                    "instruction": "Revise the manifest payload to include exact required keys and matching counts.",
                },
            )
        )

    def _strict_allowed_write_paths(self, task: Task, policy: WritePolicy | None = None) -> list[Path]:
        policy = policy or self._write_policy(task)
        raw_paths = list(policy.strict_allowed_paths or task.permissions.write_paths)
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
                return self._resolve_scope_for_verification(write_scope)
            except ValueError:
                return None
        for artifact in task.input_artifacts:
            if not _is_write_scope_artifact_id(artifact.id):
                continue
            try:
                return self._resolve_scope_for_verification(
                    artifact.content,
                    source_artifact_id=artifact.id,
                )
            except ValueError:
                return None
        return None

    def _resolve_scope_for_verification(
        self,
        value: Any,
        *,
        source_artifact_id: str | None = None,
    ) -> MutationScope:
        try:
            return resolve_mutation_scope_proposal(value, source_artifact_id=source_artifact_id)
        except ValueError as exc:
            if "exceeding max_files" not in str(exc) or not isinstance(value, dict):
                raise
            widened = dict(value)
            widened["max_files"] = max(25, int(widened.get("max_files") or 1))
            while True:
                try:
                    return resolve_mutation_scope_proposal(widened, source_artifact_id=source_artifact_id)
                except ValueError as retry_exc:
                    if "exceeding max_files" not in str(retry_exc) or widened["max_files"] >= 500:
                        raise retry_exc
                    widened["max_files"] *= 2

    def _write_policy(self, task: Task) -> WritePolicy:
        raw_policy = task.metadata.get("write_policy")
        if isinstance(raw_policy, dict):
            return WritePolicy.model_validate(raw_policy)
        raw_paths = list(task.permissions.write_paths)
        write_scope = task.metadata.get("write_scope")
        if isinstance(write_scope, dict):
            raw_paths.extend(str(path) for path in write_scope.get("target_paths") or [])
        return WritePolicy.model_validate(
            {
                "mode": "strict" if raw_paths else "advisory",
                "strict_allowed_paths": raw_paths,
            }
        )

    def _forbidden_write_paths(self, task: Task, policy: WritePolicy | None = None) -> list[Path]:
        raw_paths: list[str] = list((policy or self._write_policy(task)).forbidden_paths)
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

    def _forbidden_write_globs(self, task: Task, policy: WritePolicy | None = None) -> list[str]:
        raw_globs: list[str] = list((policy or self._write_policy(task)).forbidden_globs)
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


def _is_bounded_mutation_task(task: Task) -> bool:
    return task.metadata.get("mode") == "bounded_mutation" or task.metadata.get("phase") == "MUTATE"


def _parameter_schema(value: str) -> dict[str, Any]:
    if value == "string_or_string_array":
        return {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        }
    if value == "string_array":
        return {"type": "array", "items": {"type": "string"}}
    if value == "json_object":
        return {"type": "object", "additionalProperties": True}
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
    if value == "file_operation_array":
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["move", "write", "replace", "delete", "create_directory"],
                    },
                    "path": {"type": "string"},
                    "file": {"type": "string"},
                    "source": {"type": "string"},
                    "from": {"type": "string"},
                    "destination": {"type": "string"},
                    "to": {"type": "string"},
                    "target": {"type": "string"},
                    "directory": {"type": "string"},
                    "content": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "op": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
    return {"type": "string"}


def _coerce_file_operations(arguments: dict[str, Any]) -> Any:
    operations = arguments.get("operations")
    if isinstance(operations, list) and operations:
        return operations

    coerced: list[dict[str, Any]] = []
    for raw_directory in _coerce_string_list(
        arguments.get("create_dirs")
        or arguments.get("create_directories")
        or arguments.get("mkdirs")
    ):
        coerced.append({"action": "create_directory", "path": raw_directory})

    for raw_move in _coerce_object_list(arguments.get("move_files") or arguments.get("moves")):
        source = raw_move.get("source") or raw_move.get("from")
        destination = raw_move.get("destination") or raw_move.get("to") or raw_move.get("target")
        coerced.append(
            {
                "action": "move",
                "source": source,
                "destination": destination,
                "overwrite": bool(raw_move.get("overwrite", False)),
            }
        )

    for raw_write in _coerce_object_list(
        arguments.get("write_files")
        or arguments.get("update_files")
        or arguments.get("writes")
        or arguments.get("updates")
    ):
        coerced.append(
            {
                "action": str(raw_write.get("action") or "write"),
                "path": raw_write.get("path") or raw_write.get("file"),
                "content": raw_write.get("content") or "",
                "overwrite": bool(raw_write.get("overwrite", True)),
            }
        )

    for raw_replace in _coerce_object_list(arguments.get("replace_files") or arguments.get("replacements")):
        coerced.append(
            {
                "action": "replace",
                "path": raw_replace.get("path") or raw_replace.get("file"),
                "old": raw_replace.get("old") or "",
                "new": raw_replace.get("new") or "",
            }
        )

    for raw_delete in _coerce_delete_operations(arguments.get("delete_files") or arguments.get("deletes")):
        coerced.append(raw_delete)

    return coerced


def _coerce_object_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_delete_operations(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"action": "delete", "path": value}]
    if not isinstance(value, list):
        return []
    operations: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            operations.append({"action": "delete", "path": item.get("path") or item.get("file")})
        elif str(item).strip():
            operations.append({"action": "delete", "path": str(item)})
    return operations


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _infer_manifest_total_key(*, required_keys: list[str], payload: dict[str, Any]) -> str:
    for key in required_keys:
        if _looks_like_total_key(key):
            return key
    for key in payload:
        if _looks_like_total_key(str(key)):
            return str(key)
    return "total_artifacts" if "total_artifacts" in payload else ""


def _infer_manifest_count_keys(*, required_keys: list[str], payload: dict[str, Any], total_key: str) -> list[str]:
    keys = required_keys or [str(key) for key in payload]
    count_keys = [
        key
        for key in keys
        if key != total_key
        and isinstance(payload.get(key), list)
        and not _manifest_key_excluded_from_total(key)
        and (key.startswith("moved_") or not required_keys)
    ]
    if count_keys:
        return count_keys
    return [
        str(key)
        for key, value in payload.items()
        if key != total_key and isinstance(value, list) and not _manifest_key_excluded_from_total(str(key))
    ]


def _looks_like_total_key(key: str) -> bool:
    normalized = key.lower()
    return normalized == "total" or normalized.startswith("total_") or normalized.endswith("_total")


def _manifest_key_excluded_from_total(key: str) -> bool:
    normalized = key.lower()
    return any(token in normalized for token in ("held", "hold", "exclude", "excluded", "skipped", "ignored", "preserved"))


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
    if value.startswith(("-", "git ", "pytest ", "python ")) or ":" in value:
        return False
    return "/" in value or "." in Path(value).name


def _has_glob_meta(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _normalize_repo_glob(value: str) -> str | None:
    raw = value.strip().strip("`'\".,;)]}")
    if raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None
    if not _has_glob_meta(raw):
        return None
    if raw.startswith(("-", "~", "/")) or ":" in raw or "://" in raw or "\\" in raw:
        return None
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        return None
    return raw


def _matches_repo_glob(path: str, pattern: str) -> bool:
    normalized = _normalize_repo_glob(pattern)
    if normalized is None:
        return False
    return fnmatch.fnmatchcase(path, normalized) or fnmatch.fnmatchcase(Path(path).name, normalized)


def _operation_display_paths(operation: dict[str, Any], toolbox: WorkerToolbox) -> list[str]:
    if operation.get("action") == "move":
        return [
            toolbox._display_path(path)
            for path in (operation.get("source_path"), operation.get("destination_path"))
            if isinstance(path, Path)
        ]
    path = operation.get("resolved_path")
    return [toolbox._display_path(path)] if isinstance(path, Path) else []


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


def _is_allowed_uv_pytest_command(arguments: list[str]) -> bool:
    if not arguments:
        return False

    bool_flags = {
        "--active",
        "--all-extras",
        "--dev",
        "--frozen",
        "--locked",
        "--no-dev",
        "--no-sync",
    }
    value_flags = {
        "--extra",
        "--only-extra",
        "--with",
        "--with-editable",
        "--without",
    }

    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in bool_flags:
            index += 1
            continue
        if token in value_flags:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
                return False
            index += 2
            continue
        break

    if index >= len(arguments):
        return False
    executable = Path(arguments[index]).name
    if executable == "pytest":
        return True
    if executable in {"python", "python3"} and arguments[index + 1 : index + 3] == ["-m", "pytest"]:
        return True
    return False


def _uv_run_option_prefix_length(arguments: list[str]) -> int:
    bool_flags = {
        "--active",
        "--all-extras",
        "--dev",
        "--frozen",
        "--locked",
        "--no-dev",
        "--no-sync",
    }
    value_flags = {
        "--extra",
        "--only-extra",
        "--with",
        "--with-editable",
        "--without",
    }
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in bool_flags:
            index += 1
            continue
        if token in value_flags and index + 1 < len(arguments):
            index += 2
            continue
        break
    return index


def _uv_arguments_select_extra(arguments: list[str]) -> bool:
    return any(token in {"--extra", "--only-extra", "--all-extras", "--dev"} for token in arguments)


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


def _is_write_scope_artifact_id(artifact_id: str) -> bool:
    normalized = artifact_id.lower()
    return artifact_id in STRICT_WRITE_SCOPE_ARTIFACT_IDS or any(
        signal in normalized for signal in STRICT_WRITE_SCOPE_ARTIFACT_IDS
    )
