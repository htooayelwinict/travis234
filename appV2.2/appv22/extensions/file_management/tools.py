from __future__ import annotations

from pathlib import Path

from appv22.extensions.file_management.mutation_policy import _absolute, _canonical_relative_path
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
    text_previews: dict[str, str] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_file():
            files.append(relative)
            preview = _safe_text_preview(relative, path)
            if preview is not None:
                text_previews[relative] = preview
        elif path.is_dir():
            directories.append(relative)
    return {
        "status": "completed",
        "files": sorted(files),
        "directories": sorted(directories),
        "text_previews": dict(sorted(text_previews.items())),
        "errors": [],
    }


def _safe_text_preview(relative: str, path: Path, *, max_chars: int = 700) -> str | None:
    lowered = relative.lower()
    if lowered.startswith(("secrets/", ".git/", "assets/")):
        return None
    if path.suffix.lower() not in {".md", ".txt", ".log", ".json", ".yaml", ".yml"}:
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    return content[:max_chars]


def read_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    if _absolute(relative):
        return {"status": "denied", "path": relative, "content": "", "errors": [f"absolute_path:path:{relative}"]}
    canonical_relative = _canonical_relative_path(root, relative)
    if canonical_relative is None:
        return {"status": "denied", "path": relative, "content": "", "errors": [f"path_outside_root:{relative}"]}
    if _protected_read(canonical_relative):
        return {
            "status": "denied",
            "path": canonical_relative,
            "content": "",
            "errors": [f"protected_path:{canonical_relative}"],
        }
    path = root / canonical_relative
    if not path.is_file():
        return {
            "status": "failed",
            "path": canonical_relative,
            "content": "",
            "errors": [f"missing_file:{canonical_relative}"],
        }
    return {"status": "completed", "path": canonical_relative, "content": path.read_text(encoding="utf-8")}


def _protected_read(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/").lower()
    return normalized.startswith((".git/", "secrets/", "assets/"))
