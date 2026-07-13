"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Iterator

import httpx

from travis.ai.env_config import ModelConfig, load_model_config
from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers._shared import blank_assistant_message as _blank
from travis.ai.providers.capabilities import build_generation_payload
from travis.ai.providers.catalog import get_provider_profile, resolve_provider_runtime
from travis.ai.providers.message_sanitization import repair_tool_call_arguments
from travis.ai.providers.params import GenerationParams, merge_generation_params
from travis.ai.providers.transports import get_transport
from travis.ai.stream import ApiProvider
from travis.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    ToolResultMessage,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)
from travis.ai.validation import ToolValidationError, validate_tool_arguments
PROVIDER_API = "openai-completions"
PARTIAL_STREAM_STUB_ID = "partial-stream-stub"
PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE = "partial_stream_dropped_tool_calls"
MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE = "malformed_streamed_tool_call_arguments"
LEAKED_TOOL_PROTOCOL_TEXT_CODE = "leaked_tool_protocol_text"
_MUTATING_TOOL_REQUIRED_ARGUMENTS = {"write": ("path", "content")}
_VALID_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")
_BILLING_PATTERNS = (
    "key limit exceeded",
    "spending limit",
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "credits exhausted",
    "no usable credits",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "plan does not include",
    "out of funds",
    "run out of funds",
)
_OPENROUTER_POLICY_PATTERNS = (
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
)
_OPENROUTER_PROMPT_GUARDRAIL_PATTERNS = (
    "prompt injection patterns detected",
    "system_prefix_spoofing",
)
_PROVIDER_ERROR_DETAIL_HEAD_CHARS = 450
_PROVIDER_ERROR_DETAIL_TAIL_CHARS = 300
_PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER = "... [truncated provider error body] ..."
_NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
_NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"
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


def _repair_complete_tool_arguments(raw_arguments: str | None) -> dict | None:
    if not raw_arguments or not raw_arguments.strip():
        return {}

    source = raw_arguments.strip()
    try:
        parsed = json.loads(source, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    fixed = re.sub(r",\s*([}\]])", r"\1", source)
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket

    for _ in range(50):
        try:
            parsed = json.loads(fixed, strict=False)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            if fixed.endswith("}") and fixed.count("}") > fixed.count("{"):
                fixed = fixed[:-1]
            elif fixed.endswith("]") and fixed.count("]") > fixed.count("["):
                fixed = fixed[:-1]
            else:
                break

    try:
        repaired = _repair_json(fixed)
        parsed = json.loads(repaired, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _parse_complete_tool_arguments(raw_arguments: str | None) -> dict | None:
    return _repair_complete_tool_arguments(raw_arguments)


def _malformed_finished_mutating_tool_call_names(
    content: list[TextContent | ThinkingContent | ToolCall | ImageContent],
    tool_arg_bufs: dict[int, str],
) -> list[str]:
    names: list[str] = []
    for content_index, block in enumerate(content):
        if not isinstance(block, ToolCall):
            continue
        required = _MUTATING_TOOL_REQUIRED_ARGUMENTS.get(block.name)
        if not required:
            continue
        raw_arguments = tool_arg_bufs.get(content_index, "")
        if not raw_arguments.strip():
            continue
        try:
            complete_arguments = json.loads(raw_arguments, strict=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            complete_arguments = None
        if not isinstance(complete_arguments, dict):
            if block.name not in names:
                names.append(block.name)
            continue
        parsed = _parse_streaming_json(raw_arguments)
        if not isinstance(parsed, dict) or any(key not in parsed for key in required):
            if block.name not in names:
                names.append(block.name)
    return names


def _malformed_finished_tool_call_names_against_active_schema(
    content: list[TextContent | ThinkingContent | ToolCall | ImageContent],
    tool_arg_bufs: dict[int, str],
    tools: Iterable[Tool] | None,
) -> list[str]:
    tool_by_name = {tool.name: tool for tool in tools or []}
    if not tool_by_name:
        return []

    names: list[str] = []
    for content_index, block in enumerate(content):
        if not isinstance(block, ToolCall):
            continue
        tool = tool_by_name.get(block.name)
        if tool is None:
            continue
        raw_arguments = tool_arg_bufs.get(content_index, "")
        if not raw_arguments.strip():
            continue
        if block.name in _MUTATING_TOOL_REQUIRED_ARGUMENTS:
            try:
                complete_arguments = json.loads(raw_arguments, strict=False)
            except (json.JSONDecodeError, TypeError, ValueError):
                complete_arguments = None
            if not isinstance(complete_arguments, dict):
                if block.name not in names:
                    names.append(block.name)
                continue
        parsed_arguments = _parse_streaming_json(raw_arguments)
        try:
            validated_arguments = validate_tool_arguments(
                tool,
                replace(block, arguments=parsed_arguments),
            )
        except ToolValidationError:
            if block.name not in names:
                names.append(block.name)
            continue
        block.arguments = validated_arguments
        tool_arg_bufs[content_index] = json.dumps(validated_arguments)
    return names
