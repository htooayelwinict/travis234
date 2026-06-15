from __future__ import annotations

from pathlib import Path

from appv22.extensions.file_management.mutation_policy import _absolute, _outside, _protected
from appv22.extensions.file_management.schemas import READ_FILE_OUTPUT_SCHEMA, REPO_SNAPSHOT_OUTPUT_SCHEMA
from appv22.tools.definitions import ToolDefinition


def register_file_management_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            "file_management.repo_snapshot",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            REPO_SNAPSHOT_OUTPUT_SCHEMA,
            "runtime_observed",
            "Return workspace files and directories relative to the root.",
        ),
        repo_snapshot,
    )
    registry.register(
        ToolDefinition(
            "file_management.read_file",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            READ_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Read a workspace file by relative path.",
        ),
        read_file,
    )


def repo_snapshot(_args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    files: list[str] = []
    directories: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_file():
            files.append(relative)
        elif path.is_dir():
            directories.append(relative)
    return {"status": "completed", "files": sorted(files), "directories": sorted(directories), "errors": []}


def read_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    if _absolute(relative):
        return {"status": "denied", "path": relative, "content": "", "errors": [f"absolute_path:path:{relative}"]}
    if _outside(root, relative):
        return {"status": "denied", "path": relative, "content": "", "errors": [f"path_outside_root:{relative}"]}
    if _protected(relative):
        return {"status": "denied", "path": relative, "content": "", "errors": [f"protected_path:{relative}"]}
    path = root / relative
    if not path.is_file():
        return {"status": "failed", "path": relative, "content": "", "errors": [f"missing_file:{relative}"]}
    return {"status": "completed", "path": relative, "content": path.read_text(encoding="utf-8")}
