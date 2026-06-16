from __future__ import annotations

from typing import Any

from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL
from appv22.extensions.file_management.tools import register_file_management_tools


class FileManagementExtension:
    """Pi-style extension: expose skill metadata, register tools, guide failed tool recovery.

    The extension does not force post-hoc task completion. The model-driven agent loop
    decides whether to call another tool or finalize. Hermes-style context remains
    reference material, not deterministic mutation policy.
    """

    extension_id = "file_management"

    def skill_cards(self):
        return [FILE_MANAGEMENT_SKILL]

    def register_tools(self, registry) -> None:
        register_file_management_tools(registry)

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        tool_id = result.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id.startswith("file_management."):
            return ""
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        suggested_path = payload.get("suggested_path")
        if isinstance(suggested_path, str) and any("existing_file_requires_overwrite" in str(error) for error in errors):
            return (
                f"{tool_id} reported an existing target and suggested {suggested_path!r}; "
                "use the suggested safe alternate path unless the user explicitly requested overwrite."
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
    directories = [str(item) for item in payload.get("directories", []) if item]
    files = [str(item) for item in payload.get("files", []) if item]
    lines: list[str] = []
    if directories:
        lines.append("Directories: " + ", ".join(directories))
    if files:
        lines.append("Files: " + ", ".join(files))
    if not lines:
        lines.append("Workspace is empty.")
    return "\n".join(lines)


def _format_read_file(payload: dict[str, Any]) -> str:
    path = str(payload.get("path") or "file")
    content = payload.get("content")
    if not isinstance(content, str):
        return ""
    return f"{path}:\n{content}"


def _format_file_action(tool_id: str, payload: dict[str, Any]) -> str:
    path = payload.get("path") or payload.get("destination") or payload.get("source")
    if not isinstance(path, str) or not path:
        return f"{tool_id} completed."
    action = tool_id.rsplit(".", 1)[-1].replace("_", " ")
    return f"{action} completed for {path}."
