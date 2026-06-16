from __future__ import annotations

from pathlib import Path
import re
import shutil
from uuid import uuid4

from appv22.extensions.file_management.schemas import (
    COPY_FILE_OUTPUT_SCHEMA,
    DELETE_FILE_OUTPUT_SCHEMA,
    MKDIR_OUTPUT_SCHEMA,
    MOVE_FILE_OUTPUT_SCHEMA,
    READ_FILE_OUTPUT_SCHEMA,
    REPO_SNAPSHOT_OUTPUT_SCHEMA,
    WRITE_FILE_OUTPUT_SCHEMA,
)
from appv22.tools.definitions import ToolDefinition

_PROTECTED_PATH_PARTS = {".git", "secrets", "assets"}
_SNAPSHOT_TEXT_SUFFIXES = {".md", ".txt", ".log", ".json", ".yaml", ".yml"}
_SNAPSHOT_MAX_FILES = 600
_SNAPSHOT_MAX_DIRECTORIES = 300
_SNAPSHOT_MAX_PREVIEWS = 80
_SNAPSHOT_PREVIEW_BYTES = 4096
_SNAPSHOT_PREVIEW_CHARS = 700


def register_file_management_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            "file_management.repo_snapshot",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            REPO_SNAPSHOT_OUTPUT_SCHEMA,
            "runtime_observed",
            "List workspace files and directories relative to the root. Includes dotfiles, sorted paths, and clipped text previews for common text files.",
            freshness="turn",
            invalidated_by_mutation=True,
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
            "Read exact text content from one workspace file by relative path.",
        ),
        read_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.write_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
            WRITE_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Write complete text content to one workspace file by relative path. Creates parent directories.",
        ),
        write_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.mkdir",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            MKDIR_OUTPUT_SCHEMA,
            "runtime_observed",
            "Create one workspace directory by relative path.",
        ),
        mkdir,
    )
    registry.register(
        ToolDefinition(
            "file_management.move_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["source", "destination"],
            },
            MOVE_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Move one workspace file from source to destination. Creates parent directories.",
        ),
        move_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.copy_file",
            "act",
            "medium",
            {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                    "preserve_source": {"type": "boolean"},
                },
                "required": ["source", "destination"],
            },
            COPY_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Copy one workspace file from source to destination. Creates parent directories.",
        ),
        copy_file,
    )
    registry.register(
        ToolDefinition(
            "file_management.delete_file",
            "act",
            "high",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            DELETE_FILE_OUTPUT_SCHEMA,
            "runtime_observed",
            "Delete one workspace file by relative path.",
        ),
        delete_file,
    )


def repo_snapshot(_args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    files: list[str] = []
    directories: list[str] = []
    text_previews: dict[str, str] = {}
    errors: list[str] = []
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _protected_path(relative) or path.is_symlink():
            continue
        try:
            is_file = path.is_file()
            is_dir = path.is_dir()
        except OSError:
            errors.append(f"snapshot_stat_error:{relative}")
            continue
        if is_file:
            if len(files) >= _SNAPSHOT_MAX_FILES:
                errors.append("snapshot_file_limit_reached")
                continue
            files.append(relative)
            preview = _safe_text_preview(relative, path, root=root)
            if preview is not None and len(text_previews) < _SNAPSHOT_MAX_PREVIEWS:
                text_previews[relative] = preview
        elif is_dir:
            if len(directories) >= _SNAPSHOT_MAX_DIRECTORIES:
                errors.append("snapshot_directory_limit_reached")
                continue
            directories.append(relative)
    return {
        "status": "completed",
        "files": sorted(files),
        "directories": sorted(directories),
        "text_previews": dict(sorted(text_previews.items())),
        "errors": sorted(set(errors)),
    }


def _safe_text_preview(relative: str, path: Path, *, root: Path, max_chars: int = _SNAPSHOT_PREVIEW_CHARS) -> str | None:
    if _protected_path(relative) or path.is_symlink():
        return None
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    if path.suffix.lower() not in _SNAPSHOT_TEXT_SUFFIXES:
        return None
    try:
        if path.stat().st_size > _SNAPSHOT_PREVIEW_BYTES:
            return None
    except OSError:
        return None
    try:
        with path.open("rb") as handle:
            raw = handle.read(_SNAPSHOT_PREVIEW_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _SNAPSHOT_PREVIEW_BYTES:
        return None
    try:
        content = raw.decode("utf-8")
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


def write_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))
    if _request_forbids_overwrite(context):
        overwrite = False
    if not isinstance(content, str):
        content = str(content)
    obsolete_error = _obsolete_identifier_error(content)
    if obsolete_error:
        cleaned_content = _remove_obsolete_identifier_lines(content)
        if cleaned_content != content and not _obsolete_identifier_error(cleaned_content):
            content = cleaned_content
        else:
            return {
                "status": "denied",
                "path": relative,
                "bytes_written": 0,
                "overwritten": False,
                "errors": [obsolete_error],
            }
    if _absolute(relative):
        return {
            "status": "denied",
            "path": relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"absolute_path:path:{relative}"],
        }
    canonical_relative = _canonical_relative_path(root, relative)
    if canonical_relative is None:
        return {
            "status": "denied",
            "path": relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"path_outside_root:{relative}"],
        }
    if _protected_mutation(canonical_relative):
        return {
            "status": "denied",
            "path": canonical_relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"protected_path:{canonical_relative}"],
        }
    path = root / canonical_relative
    if path.exists() and path.is_dir():
        return {
            "status": "failed",
            "path": canonical_relative,
            "bytes_written": 0,
            "overwritten": False,
            "errors": [f"write_target_is_directory:{canonical_relative}"],
        }
    if path.exists() and not overwrite:
        suggested_path = _available_sibling_path(root, canonical_relative)
        return {
            "status": "denied",
            "path": canonical_relative,
            "bytes_written": 0,
            "overwritten": False,
            "suggested_path": suggested_path,
            "errors": [f"existing_file_requires_overwrite:{canonical_relative}"],
        }
    overwritten = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "status": "completed",
        "path": canonical_relative,
        "bytes_written": len(content.encode("utf-8")),
        "overwritten": overwritten,
        "errors": [],
    }


def mkdir(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    denied = _validate_single_mutation_path(root, relative)
    if denied:
        return {"status": "denied", "path": relative, "created": False, "errors": [denied]}
    canonical_relative = _canonical_relative_path(root, relative)
    assert canonical_relative is not None
    path = root / canonical_relative
    if path.exists() and not path.is_dir():
        return {"status": "failed", "path": canonical_relative, "created": False, "errors": [f"path_is_file:{canonical_relative}"]}
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    return {"status": "completed", "path": canonical_relative, "created": created, "errors": []}


def move_file(args: dict, context: dict) -> dict:
    return _copy_or_move_file(args, context, operation="move")


def copy_file(args: dict, context: dict) -> dict:
    if args.get("preserve_source") is not True:
        source = str(args.get("source", ""))
        destination = str(args.get("destination", ""))
        return _file_transfer_result(
            "denied",
            source,
            destination,
            False,
            ["copy_requires_preserve_source:true"],
        )
    return _copy_or_move_file(args, context, operation="copy")


def delete_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    denied = _validate_single_mutation_path(root, relative)
    if denied:
        return {"status": "denied", "path": relative, "deleted": False, "errors": [denied]}
    canonical_relative = _canonical_relative_path(root, relative)
    assert canonical_relative is not None
    path = root / canonical_relative
    if not path.exists():
        return {"status": "failed", "path": canonical_relative, "deleted": False, "errors": [f"missing_path:{canonical_relative}"]}
    if not path.is_file():
        return {"status": "denied", "path": canonical_relative, "deleted": False, "errors": [f"delete_target_not_file:{canonical_relative}"]}
    path.unlink()
    return {"status": "completed", "path": canonical_relative, "deleted": True, "errors": []}


def _copy_or_move_file(args: dict, context: dict, *, operation: str) -> dict:
    root = Path(context["root_path"]).resolve()
    source = str(args.get("source", ""))
    destination = str(args.get("destination", ""))
    overwrite = bool(args.get("overwrite", False))
    if _request_forbids_overwrite(context):
        overwrite = False
    source_error = _validate_single_mutation_path(root, source)
    if source_error:
        return _file_transfer_result("denied", source, destination, False, [f"source:{source_error}"])
    destination_error = _validate_single_mutation_path(root, destination)
    if destination_error:
        return _file_transfer_result("denied", source, destination, False, [f"destination:{destination_error}"])
    canonical_source = _canonical_relative_path(root, source)
    canonical_destination = _canonical_relative_path(root, destination)
    assert canonical_source is not None
    assert canonical_destination is not None
    source_path = root / canonical_source
    destination_path = root / canonical_destination
    if not source_path.exists():
        return _file_transfer_result("failed", canonical_source, canonical_destination, False, [f"missing_source:{canonical_source}"])
    if not source_path.is_file():
        return _file_transfer_result("denied", canonical_source, canonical_destination, False, [f"source_not_file:{canonical_source}"])
    if destination_path.exists() and destination_path.is_dir():
        return _file_transfer_result("failed", canonical_source, canonical_destination, False, [f"destination_is_directory:{canonical_destination}"])
    if destination_path.exists() and not overwrite:
        result = _file_transfer_result(
            "denied",
            canonical_source,
            canonical_destination,
            False,
            [f"existing_file_requires_overwrite:{canonical_destination}"],
        )
        result["suggested_path"] = _available_sibling_path(root, canonical_destination)
        return result
    overwritten = destination_path.exists()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if operation == "move":
        shutil.move(str(source_path), str(destination_path))
    else:
        shutil.copy2(source_path, destination_path)
    return _file_transfer_result("completed", canonical_source, canonical_destination, overwritten, [])


def _file_transfer_result(status: str, source: str, destination: str, overwritten: bool, errors: list[str]) -> dict:
    return {
        "status": status,
        "source": source,
        "destination": destination,
        "overwritten": overwritten,
        "errors": errors,
    }


def _protected_read(path: str) -> bool:
    return _protected_path(path)


def _protected_mutation(path: str) -> bool:
    return _protected_path(path)


def _protected_path(path: str) -> bool:
    parts = [part.lower() for part in path.replace("\\", "/").lstrip("/").split("/") if part]
    return any(part in _PROTECTED_PATH_PARTS for part in parts)


def _validate_single_mutation_path(root: Path, path: str) -> str:
    if _absolute(path):
        return f"absolute_path:path:{path}"
    canonical_relative = _canonical_relative_path(root, path)
    if canonical_relative is None:
        return f"path_outside_root:{path}"
    if _protected_mutation(canonical_relative):
        return f"protected_path:{canonical_relative}"
    return ""


def _absolute(path: str) -> bool:
    return Path(path).is_absolute()


def _canonical_relative_path(root: Path, path: str) -> str | None:
    if not path or "\x00" in path:
        return None
    candidate = (root / path).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


def _available_sibling_path(root: Path, relative: str) -> str:
    path = Path(relative)
    parent = path.parent.as_posix()
    stem = path.stem or "file"
    suffix = path.suffix
    for index in range(1, 100):
        candidate_name = f"{stem}-{index}{suffix}"
        candidate = candidate_name if parent == "." else f"{parent}/{candidate_name}"
        if not (root / candidate).exists():
            return candidate
    return f"{parent}/{stem}-{uuid4().hex[:8]}{suffix}" if parent != "." else f"{stem}-{uuid4().hex[:8]}{suffix}"


def _request_forbids_overwrite(context: dict) -> bool:
    request = context.get("request") if isinstance(context.get("request"), dict) else {}
    goal = str(request.get("active_user_request") or request.get("user_goal", "")).lower()
    return any(
        marker in goal
        for marker in (
            "do not overwrite",
            "don't overwrite",
            "dont overwrite",
            "no overwrite",
            "without overwriting",
            "not overwrite",
            "keep existing",
            "preserve existing",
        )
    )


def _obsolete_identifier_error(content: str) -> str:
    risky_lines = _obsolete_identifier_risky_lines(content)
    if not risky_lines:
        return ""
    code_like_identifiers = re.findall(
        r"\b[A-Z][A-Z0-9]+-[0-9]{2,}-[A-Z0-9]+\b",
        "\n".join(risky_lines),
    )
    if not code_like_identifiers:
        return ""
    unique_identifiers = sorted(set(code_like_identifiers))
    return "obsolete_identifier_leak:" + ",".join(unique_identifiers[:8])


def _remove_obsolete_identifier_lines(content: str) -> str:
    kept_lines: list[str] = []
    in_obsolete_section = False
    for line in content.splitlines():
        marker_line = _has_obsolete_marker(line)
        if marker_line:
            in_obsolete_section = True
        elif line.startswith("#"):
            in_obsolete_section = False
        elif not line.strip():
            in_obsolete_section = False
        has_identifier = re.search(r"\b[A-Z][A-Z0-9]+-[0-9]{2,}-[A-Z0-9]+\b", line)
        if has_identifier and (marker_line or in_obsolete_section):
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    return f"{cleaned}\n" if cleaned else content


def _obsolete_identifier_risky_lines(content: str) -> list[str]:
    risky_lines: list[str] = []
    in_obsolete_section = False
    for line in content.splitlines():
        if _has_obsolete_marker(line):
            in_obsolete_section = True
            risky_lines.append(line)
            continue
        if line.startswith("#") or not line.strip():
            in_obsolete_section = False
            continue
        if in_obsolete_section:
            risky_lines.append(line)
    return risky_lines


def _has_obsolete_marker(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in ("obsolete", "do not use", "excluded", "fake", "stale"))
