"""Pure helpers shared by concrete provider transport families."""

from __future__ import annotations

import json
from typing import Any


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "\n".join(parts)


def tool_function(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    return function if isinstance(function, dict) else tool


def tool_arguments(arguments: Any, _tool_name: str = "?") -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments, strict=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = ["content_to_text", "tool_arguments", "tool_function"]
