"""Shared presentation and metadata helpers for coding tools."""

from __future__ import annotations

import hashlib

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent


def context_value(context: object, key: str, default=None):
    if isinstance(context, dict):
        return context.get(key, default)
    return getattr(context, key, default)


def render_error_result(result: AgentToolResult, options=None, context=None) -> str:
    del options
    if not context_value(context, "is_error", False):
        return ""
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def file_content_metadata(content: str) -> dict[str, object]:
    encoded = content.encode("utf-8")
    line_count = 0 if not content else content.count("\n") + int(not content.endswith("\n"))
    return {
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "line_count": line_count,
        "final_newline": content.endswith(("\n", "\r")),
    }
