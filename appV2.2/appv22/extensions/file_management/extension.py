from __future__ import annotations

from typing import Any

from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILLS
from appv22.extensions.file_management.tools import register_file_management_tools


class FileManagementExtension:
    """Pi-style extension: expose skill metadata, register tools, guide failed tool recovery.

    The extension does not force post-hoc task completion. The model-driven agent loop
    decides whether to call another tool or finalize. Hermes-style context remains
    reference material, not deterministic mutation policy.
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
                "read the current file if needed and retry the same path with overwrite:true while preserving the existing content. "
                "Use the suggested alternate path only when the latest request asks to create a separate new file."
            )
        if any("protected_path" in str(error) for error in errors):
            return (
                f"{tool_id} reported a protected path; do not retry that path, "
                "and continue using non-protected workspace evidence."
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
