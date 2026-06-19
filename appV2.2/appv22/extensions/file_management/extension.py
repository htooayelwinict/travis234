from __future__ import annotations

import json
import re
from typing import Any

from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILLS
from appv22.extensions.file_management.tools import register_file_management_tools


class FileManagementExtension:
    """Pi-style extension: expose skill metadata, register tools, guide failed tool recovery.

    The extension does not compile hidden plans. It exposes tool/skill metadata and
    finalize guidance so the Pi-style loop can keep asking the model for selected
    tool calls while Hermes-style context remains reference material.
    """

    extension_id = "file_management"

    def skill_cards(self):
        return list(FILE_MANAGEMENT_SKILLS)

    def register_tools(self, registry) -> None:
        register_file_management_tools(registry)

    def sanitize_world_ref_payload(self, kind: str, payload: Any) -> dict[str, Any]:
        return _sanitize_world_ref_payload(kind, payload)

    def world_ref_has_usable_payload(self, state, world_ref: dict[str, Any]) -> bool | None:
        return _world_ref_has_usable_payload(state, world_ref)

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        tool_id = result.get("tool_id")
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        if tool_id == "read_file" or any("inactive_tool:read_file" in str(error) for error in errors):
            return (
                "read_file is not a registered appv22 tool ID; use selected tool "
                "file_management.read_file with the same path arguments instead."
            )
        if tool_id == "file_management.list_directory" or any(
            "inactive_tool:file_management.list_directory" in str(error) for error in errors
        ):
            return (
                "file_management.list_directory is not a registered appv22 tool ID; use selected tool "
                "file_management.tree for directory layout, or file_management.repo_snapshot when lightweight file previews are needed."
            )
        if tool_id == "observe_directory" or any("inactive_tool:observe_directory" in str(error) for error in errors):
            return (
                "observe_directory is not a registered appv22 tool ID; use selected tool "
                "file_management.tree for directory layout, or file_management.repo_snapshot when lightweight file previews are needed."
            )
        if not isinstance(tool_id, str) or not tool_id.startswith("file_management."):
            return ""
        suggested_path = payload.get("suggested_path")
        if isinstance(suggested_path, str) and any("existing_file_requires_overwrite" in str(error) for error in errors):
            return (
                f"{tool_id} reported an existing target and suggested {suggested_path!r}; "
                "when the latest request is to add, update, edit, fix, or patch existing content, "
                "read the current file if needed and use file_management.edit_file for targeted replacements. "
                "Use file_management.write_file with overwrite:true only for complete rewrites, and use the suggested alternate path "
                "only when the latest request asks to create a separate new file."
            )
        if any("copy_requires_preserve_source:true" in str(error) for error in errors):
            source = payload.get("source")
            destination = payload.get("destination")
            path_hint = ""
            if isinstance(source, str) and isinstance(destination, str):
                path_hint = f" from {source} to {destination}"
            return (
                f"{tool_id} requires explicit source preservation; retry file_management.copy_file{path_hint} "
                "with preserve_source:true and the same source/destination arguments."
            )
        if any("protected_path" in str(error) for error in errors):
            return (
                f"{tool_id} reported a protected path; do not retry that path, "
                "and continue using non-protected workspace evidence."
            )
        return ""

    def finalize_guidance(self, state) -> str:
        request = str(state.request.active_user_request or state.request.user_goal).lower()
        completed = [
            result
            for result in state.tool_results.values()
            if isinstance(result, dict)
            and result.get("status") == "completed"
            and isinstance(result.get("tool_id"), str)
            and str(result.get("tool_id")).startswith("file_management.")
        ]
        manifest_index, manifest = _latest_manifest(completed)
        changed_paths = _changed_paths(completed[:manifest_index] if manifest_index is not None else completed)
        cleanup_record = _cleanup_record_requested(request)
        if cleanup_record and changed_paths:
            if manifest is None:
                return (
                    "A workspace record was requested but docs/workspace_manifest.json has not been written; "
                    "call file_management.write_file for docs/workspace_manifest.json before finalizing."
                )
            missing = sorted(path for path in changed_paths if not _manifest_mentions(manifest, path))
            if missing:
                return (
                    "The workspace record is missing changed paths "
                    f"{', '.join(missing[:4])}; call file_management.write_file for docs/workspace_manifest.json "
                    "with the missing paths before finalizing."
                )

        if cleanup_record:
            snapshot_winners = _unresolved_snapshot_winners(state, completed)
            if snapshot_winners:
                source = snapshot_winners[0]
                return (
                    "snapshot evidence contains unresolved winning sources; "
                    f"call file_management.move_file for {source} before finalizing."
                )

            unresolved_winners = _unresolved_manifest_winners(manifest, completed) if manifest is not None else []
            if unresolved_winners:
                source = unresolved_winners[0]
                return (
                    "The manifest names unresolved winning sources; "
                    f"call file_management.move_file for {source} before finalizing."
                )

            unresolved_deletions = _unresolved_manifest_deletions(manifest, completed) if manifest is not None else []
            if unresolved_deletions:
                path = unresolved_deletions[0]
                return (
                    "The manifest names unresolved deletions; "
                    f"call file_management.delete_file for {path} before finalizing."
                )
        if _source_reads_need_file_write(request, completed):
            paths = _read_source_paths(completed)
            suffix = f" using source evidence from {', '.join(paths[:4])}" if paths else ""
            return (
                "Source file evidence has been read for the requested file creation; "
                "the next decision must be a tool_call to file_management.write_file "
                f"for docs/handoff.md{suffix} before finalizing."
            )
        if _existing_file_edit_requested(request, completed):
            paths = _read_source_paths(completed)
            target = paths[0] if paths else "the existing file"
            return (
                "Current source file evidence has been read for the requested existing-file edit; "
                "the next decision must be a tool_call to file_management.edit_file "
                f"for {target} before finalizing."
            )
        if _mutation_write_requested(request, completed):
            return (
                "The latest file mutation request has no completed write evidence; "
                "the next decision must be a tool_call to file_management.write_file before finalizing."
            )
        return ""

    def transform_tool_result(self, result: dict[str, Any]) -> dict[str, Any] | None:
        tool_id = result.get("tool_id")
        if not isinstance(tool_id, str) or result.get("status") != "completed":
            return None
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if tool_id == "file_management.repo_snapshot":
            text = _format_repo_snapshot(payload)
        elif tool_id == "file_management.find_files":
            text = _format_find_files(payload)
        elif tool_id == "file_management.search_text":
            text = _format_search_text(payload)
        elif tool_id == "file_management.read_many":
            text = _format_read_many(payload)
        elif tool_id == "file_management.tree":
            text = _format_tree(payload)
        elif tool_id == "file_management.grep":
            text = _format_grep(payload)
        elif tool_id == "file_management.read_range":
            text = _format_read_range(payload)
        elif tool_id == "file_management.read_file":
            text = _format_read_file(payload)
        elif tool_id in {
            "file_management.write_file",
            "file_management.edit_file",
            "file_management.mkdir",
            "file_management.move_file",
            "file_management.copy_file",
            "file_management.delete_file",
        }:
            text = _format_file_action(tool_id, payload)
        else:
            text = ""
        if not text:
            return None
        return {
            "model_view": text,
            "user_message": text,
        }


def _format_repo_snapshot(payload: dict[str, Any]) -> str:
    root = str(payload.get("root") or ".")
    directories = [str(item) for item in payload.get("directories", []) if item]
    files = [str(item) for item in payload.get("files", []) if item]
    lines: list[str] = []
    lines.append(f"Snapshot root: {root}")
    if directories:
        lines.append("Directories: " + ", ".join(_clip_items(directories)))
    if files:
        lines.append("Files: " + ", ".join(_clip_items(files)))
    errors = [str(item) for item in payload.get("errors", []) if item]
    if errors:
        lines.append("Notes: " + ", ".join(errors[:8]))
    if len(lines) == 1:
        lines.append("Workspace is empty.")
    return "\n".join(lines)


def _format_find_files(payload: dict[str, Any]) -> str:
    root = str(payload.get("root") or ".")
    matches = [str(item) for item in payload.get("matches", []) if item]
    lines = [f"File matches under {root}: {len(matches)}"]
    if matches:
        lines.append("\n".join(_clip_items(matches, limit=80)))
    return "\n".join(lines)


def _format_search_text(payload: dict[str, Any]) -> str:
    root = str(payload.get("root") or ".")
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    lines = [f"Text matches under {root}: {len(matches)}"]
    for item in matches[:80]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        line = item.get("line")
        snippet = str(item.get("snippet") or "")
        lines.append(f"{path}:{line}: {snippet}")
    return "\n".join(lines)


def _format_read_many(payload: dict[str, Any]) -> str:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    lines = [f"Read files: {len(files)}"]
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "file")
        content = str(item.get("content") or "")
        line_count = item.get("line_count")
        line_suffix = f" ({line_count} lines)" if isinstance(line_count, int) else ""
        truncated = " [truncated]" if item.get("truncated") else ""
        lines.append(f"\n--- {path}{line_suffix}{truncated} ---\n{content}")
    errors = [str(item) for item in payload.get("errors", []) if item]
    if errors:
        lines.append("Notes: " + ", ".join(errors[:8]))
    return "\n".join(lines)


def _format_tree(payload: dict[str, Any]) -> str:
    root = str(payload.get("root") or ".")
    entries = [str(item) for item in payload.get("entries", []) if item]
    lines = [f"Tree under {root}: {len(entries)} entries"]
    if entries:
        lines.append("\n".join(_clip_items(entries, limit=160)))
    errors = [str(item) for item in payload.get("errors", []) if item]
    if errors:
        lines.append("Notes: " + ", ".join(errors[:8]))
    return "\n".join(lines)


def _format_grep(payload: dict[str, Any]) -> str:
    root = str(payload.get("root") or ".")
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    lines = [f"Grep matches under {root}: {len(matches)}"]
    for item in matches[:120]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        line = item.get("line")
        snippet = str(item.get("snippet") or "")
        lines.append(f"{path}:{line}: {snippet}")
    errors = [str(item) for item in payload.get("errors", []) if item]
    if errors:
        lines.append("Notes: " + ", ".join(errors[:8]))
    return "\n".join(lines)


def _format_read_range(payload: dict[str, Any]) -> str:
    path = str(payload.get("path") or "file")
    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    content = payload.get("content")
    if not isinstance(content, str):
        return ""
    return f"{path}:{start_line}-{end_line}\n{content}"


def _format_read_file(payload: dict[str, Any]) -> str:
    path = str(payload.get("path") or "file")
    content = payload.get("content")
    if not isinstance(content, str):
        return ""
    line_count = payload.get("line_count")
    if isinstance(line_count, int):
        return f"{path}:\nLine count: {line_count}\n{content}"
    return f"{path}:\n{content}"


def _format_file_action(tool_id: str, payload: dict[str, Any]) -> str:
    path = payload.get("path") or payload.get("destination") or payload.get("source")
    if not isinstance(path, str) or not path:
        return f"{tool_id} completed."
    action = tool_id.rsplit(".", 1)[-1].replace("_", " ")
    return f"{action} completed for {path}."


def _clip_items(items: list[str], *, limit: int = 120) -> list[str]:
    if len(items) <= limit:
        return items
    return [*items[:limit], f"... clipped {len(items) - limit} more"]


READ_FILE_SESSION_CONTENT_LIMIT = 12000
READ_MANY_SESSION_CONTENT_LIMIT = 4000


def _sanitize_world_ref_payload(kind: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if kind == "file_management.repo_snapshot":
        item: dict[str, Any] = {}
        files = payload.get("files")
        directories = payload.get("directories")
        if isinstance(files, list):
            item["files"] = [str(path)[:240] for path in files[:600] if isinstance(path, str)]
        if isinstance(directories, list):
            item["directories"] = [str(path)[:240] for path in directories[:300] if isinstance(path, str)]
        previews = payload.get("text_previews")
        if isinstance(previews, dict):
            item["text_previews"] = {
                str(path)[:240]: str(text)[:700]
                for path, text in list(previews.items())[:40]
                if isinstance(path, str) and isinstance(text, str)
            }
        return item
    if kind == "file_management.read_file":
        content = payload.get("content")
        path = payload.get("path")
        item: dict[str, Any] = {}
        if isinstance(path, str):
            item["path"] = path[:240]
        content_truncated = bool(payload.get("content_truncated_by_session"))
        if isinstance(content, str):
            content_truncated = content_truncated or len(content) >= READ_FILE_SESSION_CONTENT_LIMIT
            item["content"] = content[:READ_FILE_SESSION_CONTENT_LIMIT]
            if content_truncated:
                item["content_truncated_by_session"] = True
        if isinstance(payload.get("line_count"), int):
            item["line_count"] = payload["line_count"]
        elif isinstance(content, str) and not content_truncated:
            item["line_count"] = _line_count(content)
        return item
    if kind == "file_management.find_files":
        matches = payload.get("matches")
        if isinstance(matches, list):
            return {"matches": [str(path)[:240] for path in matches[:500] if isinstance(path, str)]}
        return {}
    if kind == "file_management.search_text":
        return _sanitize_match_payload(payload, limit=120)
    if kind == "file_management.read_many":
        files = payload.get("files")
        if not isinstance(files, list):
            return {}
        sanitized_files = []
        for item in files[:12]:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            content = item.get("content")
            file_payload: dict[str, Any] = {}
            if isinstance(path, str):
                file_payload["path"] = path[:240]
            content_truncated = bool(item.get("content_truncated_by_session"))
            if isinstance(content, str):
                content_truncated = content_truncated or len(content) >= READ_MANY_SESSION_CONTENT_LIMIT
                file_payload["content"] = content[:READ_MANY_SESSION_CONTENT_LIMIT]
                if content_truncated:
                    file_payload["content_truncated_by_session"] = True
            if isinstance(item.get("bytes_read"), int):
                file_payload["bytes_read"] = item["bytes_read"]
            if isinstance(item.get("line_count"), int):
                file_payload["line_count"] = item["line_count"]
            elif isinstance(content, str) and not content_truncated:
                file_payload["line_count"] = _line_count(content)
            if isinstance(item.get("truncated"), bool):
                file_payload["truncated"] = item["truncated"]
            if file_payload:
                sanitized_files.append(file_payload)
        return {"files": sanitized_files}
    if kind == "file_management.tree":
        entries = payload.get("entries")
        if isinstance(entries, list):
            return {"entries": [str(entry)[:240] for entry in entries[:500] if isinstance(entry, str)]}
        return {}
    if kind == "file_management.grep":
        return _sanitize_match_payload(payload, limit=120)
    if kind == "file_management.read_range":
        item: dict[str, Any] = {}
        path = payload.get("path")
        content = payload.get("content")
        if isinstance(path, str):
            item["path"] = path[:240]
        if isinstance(payload.get("start_line"), int):
            item["start_line"] = payload["start_line"]
        if isinstance(payload.get("end_line"), int):
            item["end_line"] = payload["end_line"]
        if isinstance(content, str):
            item["content"] = content[:12000]
        return item
    return {}


def _world_ref_has_usable_payload(state, world_ref: dict[str, Any]) -> bool | None:
    kind = world_ref.get("kind")
    payload = world_ref.get("payload")
    if not isinstance(kind, str) or not kind.startswith("file_management."):
        return None
    if not isinstance(payload, dict) or not payload:
        return False
    if kind == "file_management.repo_snapshot":
        return isinstance(payload.get("files"), list) or isinstance(payload.get("directories"), list)
    if kind == "file_management.read_file":
        if not isinstance(payload.get("content"), str):
            return False
        if _is_line_count_followup(state) and payload.get("content_truncated_by_session") is True:
            return isinstance(payload.get("line_count"), int)
        return True
    if kind == "file_management.read_many":
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            return False
        if _is_line_count_followup(state):
            return any(
                isinstance(item, dict)
                and isinstance(item.get("line_count"), int)
                and (
                    item.get("content_truncated_by_session") is not True
                    or isinstance(item.get("line_count"), int)
                )
                for item in files
            )
        return True
    if kind in {"file_management.find_files", "file_management.search_text", "file_management.grep"}:
        return isinstance(payload.get("matches"), list)
    if kind == "file_management.tree":
        return isinstance(payload.get("entries"), list)
    if kind == "file_management.read_range":
        return isinstance(payload.get("content"), str)
    return True


def _is_line_count_followup(state) -> bool:
    request = getattr(state, "request", None)
    text = str(getattr(request, "active_user_request", "") or getattr(request, "user_goal", "") or "").lower()
    normalized = " ".join(text.split())
    return ("line" in normalized or "lines" in normalized) and (
        "how many" in normalized or "count" in normalized or "length" in normalized
    )


def _sanitize_match_payload(payload: dict[str, Any], *, limit: int) -> dict[str, Any]:
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return {}
    sanitized_matches = []
    for item in matches[:limit]:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        line = item.get("line")
        snippet = item.get("snippet")
        sanitized: dict[str, Any] = {}
        if isinstance(path, str):
            sanitized["path"] = path[:240]
        if isinstance(line, int):
            sanitized["line"] = line
        if isinstance(snippet, str):
            sanitized["snippet"] = snippet[:300]
        if sanitized:
            sanitized_matches.append(sanitized)
    return {"matches": sanitized_matches}


def _line_count(content: str) -> int:
    return len(content.splitlines())


def _cleanup_record_requested(request: str) -> bool:
    has_record = any(marker in request for marker in ("manifest", "keep a record"))
    has_cleanup = any(marker in request for marker in ("clean", "cleanup", "mess", "organize", "reorganize", "junk"))
    return "manifest" in request or (has_record and has_cleanup)


def _source_reads_need_file_write(request: str, results: list[dict[str, Any]]) -> bool:
    if _request_disallows_writes(request):
        return False
    if not _source_compilation_file_requested(request):
        return False
    has_read = any(result.get("tool_id") in {"file_management.read_file", "file_management.read_many"} for result in results)
    has_write = any(result.get("tool_id") == "file_management.write_file" for result in results)
    return has_read and not has_write


def _source_compilation_file_requested(request: str) -> bool:
    if any(marker in request for marker in ("do not create", "don't create", "dont create", "no sibling file")):
        return False
    return any(marker in request for marker in ("handoff file", "concise handoff", "make one"))


def _file_creation_requested(request: str) -> bool:
    if any(marker in request for marker in ("do not create", "don't create", "dont create", "no sibling file")):
        return False
    return any(marker in request for marker in ("handoff file", "concise handoff", "make one")) or _has_request_word(
        request,
        ("create", "write"),
    )


def _existing_file_edit_requested(request: str, results: list[dict[str, Any]]) -> bool:
    if _request_disallows_writes(request):
        return False
    if _request_is_question_only(request):
        return False
    if any(marker in request for marker in ("handoff file", "concise handoff", "create", "make one", "new file")):
        return False
    if not _has_request_word(request, ("edit", "modify", "patch", "replace", "fix", "update", "change")):
        return False
    has_read = any(result.get("tool_id") in {"file_management.read_file", "file_management.read_many"} for result in results)
    has_mutation = any(result.get("tool_id") in {"file_management.edit_file", "file_management.write_file"} for result in results)
    return has_read and not has_mutation


def _read_source_paths(results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for result in results:
        tool_id = result.get("tool_id")
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if tool_id == "file_management.read_file" and isinstance(payload.get("path"), str):
            paths.append(payload["path"])
        if tool_id == "file_management.read_many":
            files = payload.get("files")
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, dict) and isinstance(item.get("path"), str):
                        paths.append(item["path"])
    return paths


def _mutation_write_requested(request: str, results: list[dict[str, Any]]) -> bool:
    if _request_disallows_writes(request):
        return False
    if _request_is_question_only(request):
        return False
    if any(marker in request for marker in ("clean", "cleanup", "mess", "organize", "reorganize", "junk")):
        return False
    if not _has_request_word(request, ("add", "update", "fix", "create", "write", "make")):
        return False
    has_mutation = any(result.get("tool_id") in {"file_management.edit_file", "file_management.write_file"} for result in results)
    return not has_mutation


def _has_request_word(request: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", request) for word in words)


def _request_is_question_only(request: str) -> bool:
    question_markers = (
        "which ",
        "what ",
        "where ",
        "when ",
        "why ",
        "how ",
        "tell me",
        "explain",
        "summarize",
        "confirm",
        "whether",
    )
    mutation_markers = (
        "add ",
        "update",
        "fix",
        "create",
        "write",
        "make",
        "edit",
        "modify",
        "patch",
        "replace",
    )
    return any(marker in request for marker in question_markers) and not any(
        request.startswith(marker) for marker in mutation_markers
    )


def _request_disallows_writes(request: str) -> bool:
    return any(
        marker in request
        for marker in (
            "do not write",
            "don't write",
            "dont write",
            "no writes",
            "no write",
            "without writing",
            "do not edit",
            "don't edit",
            "dont edit",
            "no edit",
            "no edits",
            "do not modify",
            "don't modify",
            "dont modify",
            "do not change",
            "don't change",
            "dont change",
            "no changes",
            "no modifications",
            "read only",
            "read-only",
            "analysis only",
            "without changing",
        )
    )


def _latest_manifest(results: list[dict[str, Any]]) -> tuple[int | None, Any | None]:
    for index in range(len(results) - 1, -1, -1):
        result = results[index]
        if result.get("tool_id") != "file_management.write_file":
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if payload.get("path") != "docs/workspace_manifest.json":
            continue
        arguments = result.get("arguments") if isinstance(result.get("arguments"), dict) else {}
        content = arguments.get("content")
        if not isinstance(content, str):
            continue
        try:
            return index, json.loads(content)
        except json.JSONDecodeError:
            return index, content
    return None, None


def _changed_paths(results: list[dict[str, Any]]) -> set[str]:
    changed: set[str] = set()
    for result in results:
        tool_id = result.get("tool_id")
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if tool_id == "file_management.move_file":
            _add_str(changed, payload.get("source"))
            _add_str(changed, payload.get("destination"))
        elif tool_id == "file_management.copy_file":
            _add_str(changed, payload.get("destination"))
        elif tool_id == "file_management.delete_file":
            _add_str(changed, payload.get("path"))
        elif tool_id == "file_management.edit_file":
            _add_str(changed, payload.get("path"))
        elif tool_id == "file_management.write_file" and payload.get("path") != "docs/workspace_manifest.json":
            _add_str(changed, payload.get("path"))
    return changed


def _manifest_mentions(manifest: Any, path: str) -> bool:
    return not path or path in _manifest_strings(manifest)


def _manifest_strings(value: Any) -> set[str]:
    strings: set[str] = set()
    if isinstance(value, str):
        strings.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings.update(_manifest_strings(item))
    elif isinstance(value, list | tuple):
        for item in value:
            strings.update(_manifest_strings(item))
    return strings


def _unresolved_manifest_winners(manifest: Any, results: list[dict[str, Any]]) -> list[str]:
    winners: list[str] = []
    if isinstance(manifest, dict):
        for collision in manifest.get("collisions", []):
            if isinstance(collision, dict) and isinstance(collision.get("winner"), str):
                winners.append(collision["winner"])
        for held in manifest.get("held", []):
            if not isinstance(held, dict):
                continue
            reason = held.get("reason")
            if isinstance(reason, str):
                winners.extend(_claimed_sources(reason))
    moved_sources = _completed_move_sources(results)
    return [winner for winner in winners if winner not in moved_sources]


def _unresolved_manifest_deletions(manifest: Any, results: list[dict[str, Any]]) -> list[str]:
    deletions: list[str] = []
    if isinstance(manifest, dict):
        for deletion in manifest.get("deletions", []):
            if isinstance(deletion, dict) and isinstance(deletion.get("path"), str):
                deletions.append(deletion["path"])
            elif isinstance(deletion, str):
                deletions.append(deletion)
    deleted_paths = _completed_deleted_paths(results)
    return [path for path in deletions if path not in deleted_paths]


def _unresolved_snapshot_winners(state, results: list[dict[str, Any]]) -> list[str]:
    winners: list[str] = []
    for ref in state.world_refs.values():
        if not isinstance(ref, dict) or ref.get("kind") != "file_management.repo_snapshot":
            continue
        payload = ref.get("payload") if isinstance(ref.get("payload"), dict) else {}
        previews = payload.get("text_previews") if isinstance(payload.get("text_previews"), dict) else {}
        for path, text in previews.items():
            if isinstance(path, str) and isinstance(text, str) and "move this" in text.lower():
                winners.append(path)
            if isinstance(text, str):
                winners.extend(_claimed_sources(text))
    moved_sources = _completed_move_sources(results)
    return [winner for winner in winners if winner not in moved_sources]


def _completed_move_sources(results: list[dict[str, Any]]) -> set[str]:
    sources: set[str] = set()
    for result in results:
        if result.get("tool_id") != "file_management.move_file":
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        _add_str(sources, payload.get("source"))
    return sources


def _completed_deleted_paths(results: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for result in results:
        if result.get("tool_id") != "file_management.delete_file":
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        _add_str(paths, payload.get("path"))
    return paths


def _add_str(target: set[str], value: Any) -> None:
    if isinstance(value, str) and value:
        target.add(value)


def _claimed_sources(text: str) -> list[str]:
    return [
        match.strip(" .,\n\t")
        for match in re.findall(r"claimed by ([A-Za-z0-9_./-]+)", text, flags=re.IGNORECASE)
        if match.strip(" .,\n\t")
    ]
