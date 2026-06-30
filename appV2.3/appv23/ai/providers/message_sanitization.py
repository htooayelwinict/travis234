"""Hermes-style provider payload sanitization helpers."""

from __future__ import annotations

import json
import re
from typing import Any


def sanitize_surrogates(text: str) -> str:
    """Replace lone surrogate code points with U+FFFD."""
    return re.sub(r"[\ud800-\udfff]", "\ufffd", text)


def sanitize_structure_surrogates(payload: Any) -> bool:
    """Replace surrogate code points in nested dict/list payloads in-place."""
    found = False

    def walk(node: Any) -> None:
        nonlocal found
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    cleaned = sanitize_surrogates(value)
                    if cleaned != value:
                        node[key] = cleaned
                        found = True
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str):
                    cleaned = sanitize_surrogates(value)
                    if cleaned != value:
                        node[index] = cleaned
                        found = True
                elif isinstance(value, (dict, list)):
                    walk(value)

    walk(payload)
    return found


def escape_invalid_chars_in_json_strings(raw: str) -> str:
    """Escape unescaped control chars inside JSON string values."""
    out: list[str] = []
    in_string = False
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if in_string:
            if char == "\\" and index + 1 < length:
                out.append(char)
                out.append(raw[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
                out.append(char)
            elif ord(char) < 0x20:
                out.append(f"\\u{ord(char):04x}")
            else:
                out.append(char)
        else:
            if char == '"':
                in_string = True
            out.append(char)
        index += 1
    return "".join(out)


def repair_tool_call_arguments(raw_args: str, tool_name: str = "?") -> str:
    """Repair malformed tool-call argument JSON at the provider boundary."""
    raw_stripped = raw_args.strip() if isinstance(raw_args, str) else ""
    if not raw_stripped or raw_stripped == "None":
        return "{}"

    try:
        parsed = json.loads(raw_stripped, strict=False)
        return json.dumps(parsed, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    fixed = re.sub(r",\s*([}\]])", r"\1", raw_stripped)
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket

    for _ in range(50):
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            if fixed.endswith("}") and fixed.count("}") > fixed.count("{"):
                fixed = fixed[:-1]
            elif fixed.endswith("]") and fixed.count("]") > fixed.count("["):
                fixed = fixed[:-1]
            else:
                break

    try:
        escaped = escape_invalid_chars_in_json_strings(fixed)
        if escaped != fixed:
            json.loads(escaped)
            return escaped
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    return "{}"
