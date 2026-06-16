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
