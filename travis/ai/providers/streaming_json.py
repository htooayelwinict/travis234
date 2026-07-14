"""Partial JSON parsing for streamed tool arguments."""

from __future__ import annotations

import json
from typing import Any

_VALID_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
_STREAMING_TOOL_ARGUMENT_PREVIEW_MAX_CHARS = 8_192

class _PartialJson(ValueError):
    pass


class _MalformedJson(ValueError):
    pass


def _is_control_character(char: str) -> bool:
    return 0 <= ord(char) <= 0x1F


def _escape_control_character(char: str) -> str:
    escapes = {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    return escapes.get(char, f"\\u{ord(char):04x}")


def _repair_json(json_text: str) -> str:
    repaired: list[str] = []
    in_string = False
    index = 0
    while index < len(json_text):
        char = json_text[index]

        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue

        if char == '"':
            repaired.append(char)
            in_string = False
            index += 1
            continue

        if char == "\\":
            next_char = json_text[index + 1] if index + 1 < len(json_text) else None
            if next_char is None:
                repaired.append("\\\\")
                index += 1
                continue

            if next_char == "u":
                unicode_digits = json_text[index + 2 : index + 6]
                if len(unicode_digits) == 4 and all(digit in "0123456789abcdefABCDEF" for digit in unicode_digits):
                    repaired.append("\\u" + unicode_digits)
                    index += 6
                    continue

            if next_char in _VALID_JSON_ESCAPES:
                repaired.append("\\" + next_char)
                index += 2
                continue

            repaired.append("\\\\")
            index += 1
            continue

        repaired.append(_escape_control_character(char) if _is_control_character(char) else char)
        index += 1

    return "".join(repaired)


def _parse_json_with_repair(json_text: str) -> Any:
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        repaired = _repair_json(json_text)
        if repaired != json_text:
            return json.loads(repaired)
        raise


def _partial_parse_json(json_text: str) -> Any:
    if not isinstance(json_text, str):
        raise TypeError(f"expecting str, got {type(json_text).__name__}")
    if not json_text.strip():
        raise ValueError(f"{json_text} is empty")

    source = json_text.strip()
    length = len(source)
    index = 0

    def mark_partial(message: str) -> None:
        raise _PartialJson(f"{message} at position {index}")

    def throw_malformed(message: str) -> None:
        raise _MalformedJson(f"{message} at position {index}")

    def skip_blank() -> None:
        nonlocal index
        while index < length and source[index] in " \n\r\t":
            index += 1

    def parse_str() -> str:
        nonlocal index
        start = index
        escape = False
        index += 1
        while index < length:
            char = source[index]
            if char == '"' and not escape:
                index += 1
                try:
                    return json.loads(source[start:index])
                except json.JSONDecodeError as exc:
                    throw_malformed(str(exc))
            if char == "\\":
                escape = not escape
            else:
                escape = False
            index += 1

        end = index - (1 if escape else 0)
        try:
            return json.loads(source[start:end] + '"')
        except json.JSONDecodeError:
            last_escape = source.rfind("\\", start, index)
            if last_escape >= start:
                return json.loads(source[start:last_escape] + '"')
            raise

    def parse_num() -> int | float:
        nonlocal index
        if index == 0:
            if source == "-":
                throw_malformed("Not sure what '-' is")
            try:
                return json.loads(source)
            except json.JSONDecodeError as exc:
                last_exponent = source.rfind("e")
                if last_exponent >= 0:
                    try:
                        return json.loads(source[:last_exponent])
                    except json.JSONDecodeError:
                        pass
                throw_malformed(str(exc))

        start = index
        if index < length and source[index] == "-":
            index += 1
        while index < length and source[index] not in ",]}":
            index += 1
        if index == length:
            pass
        token = source[start:index]
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            if token == "-":
                mark_partial("Not sure what '-' is")
            last_exponent = token.rfind("e")
            if last_exponent >= 0:
                try:
                    return json.loads(token[:last_exponent])
                except json.JSONDecodeError:
                    pass
            raise

    def parse_arr() -> list[Any]:
        nonlocal index
        index += 1
        array = []
        try:
            while index < length and source[index] != "]":
                array.append(parse_any())
                skip_blank()
                if index < length and source[index] == ",":
                    index += 1
        except Exception:
            return array
        if index < length and source[index] == "]":
            index += 1
        return array

    def parse_obj() -> dict[str, Any]:
        nonlocal index
        index += 1
        skip_blank()
        obj: dict[str, Any] = {}
        try:
            while index < length and source[index] != "}":
                skip_blank()
                if index >= length:
                    return obj
                key = parse_str()
                skip_blank()
                if index >= length or source[index] != ":":
                    return obj
                index += 1
                try:
                    obj[key] = parse_any()
                except Exception:
                    return obj
                skip_blank()
                if index < length and source[index] == ",":
                    index += 1
        except Exception:
            return obj
        if index < length and source[index] == "}":
            index += 1
        return obj

    def parse_any() -> Any:
        nonlocal index
        skip_blank()
        if index >= length:
            mark_partial("Unexpected end of input")
        if source[index] == '"':
            return parse_str()
        if source[index] == "{":
            return parse_obj()
        if source[index] == "[":
            return parse_arr()

        remaining = source[index:]
        for literal, value in (("null", None), ("true", True), ("false", False)):
            if remaining.startswith(literal) or literal.startswith(remaining):
                index += len(literal)
                return value
        return parse_num()

    return parse_any()


def _parse_streaming_json(partial_json: str | None) -> dict:
    if not partial_json or not partial_json.strip():
        return {}
    try:
        parsed = _parse_json_with_repair(partial_json)
    except Exception:
        try:
            parsed = _partial_parse_json(partial_json)
        except Exception:
            try:
                parsed = _partial_parse_json(_repair_json(partial_json))
            except Exception:
                return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_streaming_json_preview(partial_json: str | None, previous: dict | None = None) -> dict:
    if not partial_json or not partial_json.strip():
        return {}
    if len(partial_json) <= _STREAMING_TOOL_ARGUMENT_PREVIEW_MAX_CHARS:
        return _parse_streaming_json(partial_json)
    if previous:
        return previous
    return _parse_streaming_json(partial_json[:_STREAMING_TOOL_ARGUMENT_PREVIEW_MAX_CHARS])


def _parse_complete_tool_arguments(raw_arguments: str | None) -> dict | None:
    return _parse_streaming_json(raw_arguments)
