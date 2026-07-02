"""appv2-env provider: OpenAI/OpenRouter-compatible streaming over httpx SSE."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Iterator

import httpx

from appv231.ai.env_config import ModelConfig, load_model_config
from appv231.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from appv231.ai.providers.base import ProviderProfile
from appv231.ai.providers.capabilities import build_generation_payload
from appv231.ai.providers.catalog import get_provider_profile, resolve_provider_runtime
from appv231.ai.providers.message_sanitization import repair_tool_call_arguments
from appv231.ai.providers.params import GenerationParams, merge_generation_params
from appv231.ai.providers.transports import get_transport
from appv231.ai.stream import ApiProvider
from appv231.ai.types import (
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
from appv231.ai.validation import ToolValidationError, validate_tool_arguments
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
_TOOL_CALL_LEAK_PATTERN = re.compile(r"(?:^|[\s>|])to=functions\.[A-Za-z_][\w.]*", re.IGNORECASE)
_TOOL_PROTOCOL_XML_BLOCK_PATTERN = re.compile(
    r"(?is)"
    r"<(?:tool_call|tool_calls|tool_response|function_call|function_calls)\b[^>]*>.*?"
    r"</(?:tool_call|tool_calls|tool_response|function_call|function_calls)>"
    r"|<function\s+name\s*=\s*[\"'][^\"']+[\"'][^>]*>.*?</function>"
)
_TOOL_PROTOCOL_XML_LINE_PATTERN = re.compile(
    r"(?im)^[ \t]*</?(?:parameter|function|tool_call|tool_calls|tool_response|function_call|function_calls)"
    r"(?:[\s=>][^>]*)?>[ \t]*(?:\r?\n|$)"
)
_TOOL_PROTOCOL_XML_PREFIX_PATTERN = re.compile(
    r"(?is)(?:^|[\s>])(?:"
    r"</?(?:tool_call|tool_calls|tool_response|function_call|function_calls|parameter)(?:$|[\s=>_/])"
    r"|<tool(?:$|_)"
    r"|</tool(?:$|[_>\s])"
    r"|<function\s+(?:$|name\s*=)"
    r"|</function(?:$|[\s>])"
    r"|<parameter(?:$|[\s=>])"
    r"|</parameter(?:$|[\s>])"
    r")"
)
_PROVIDER_ERROR_DETAIL_HEAD_CHARS = 450
_PROVIDER_ERROR_DETAIL_TAIL_CHARS = 300
_PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER = "... [truncated provider error body] ..."
_NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
_NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"


def _has_tool_history(messages: list[Message]) -> bool:
    for message in messages:
        if getattr(message, "role", None) == "toolResult":
            return True
        if getattr(message, "role", None) == "assistant":
            for block in getattr(message, "content", []) or []:
                if isinstance(block, ToolCall):
                    return True
    return False


def convert_messages(context: Context, model: Model | None = None) -> "tuple[list[dict], list[dict] | None]":
    messages: list[dict] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": _sanitize_surrogates(context.system_prompt)})
    context_messages = _transform_messages(context.messages, model) if model is not None else context.messages
    index = 0
    while index < len(context_messages):
        message = context_messages[index]
        if message.role == "toolResult":
            tool_messages, next_index = _convert_tool_result_group(
                context_messages,
                index,
                model,
            )
            messages.extend(tool_messages)
            index = next_index
            continue
        converted_message = _convert_message(message, model)
        if converted_message is not None:
            messages.append(converted_message)
        index += 1
    tools = None
    if context.tools:
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in context.tools
        ]
    elif _has_tool_history(context_messages):
        tools = []
    return messages, tools


def _transform_messages(messages: list[Message], model: Model) -> list[Message]:
    tool_call_id_map: dict[str, str] = {}
    image_aware_messages = _downgrade_unsupported_images(messages, model)
    transformed: list[Message] = []

    for message in image_aware_messages:
        if message.role == "user":
            transformed.append(message)
            continue

        if message.role == "toolResult":
            normalized_id = tool_call_id_map.get(message.tool_call_id)
            transformed.append(
                replace(message, tool_call_id=normalized_id)
                if normalized_id and normalized_id != message.tool_call_id
                else message
            )
            continue

        is_same_model = message.provider == model.provider and message.api == model.api and message.model == model.id
        transformed_content: list[TextContent | ThinkingContent | ImageContent | ToolCall] = []
        for block in message.content:
            if isinstance(block, ThinkingContent):
                if block.redacted:
                    if is_same_model:
                        transformed_content.append(block)
                    continue
                if is_same_model and block.thinking_signature:
                    transformed_content.append(block)
                    continue
                if not block.thinking or not block.thinking.strip():
                    continue
                transformed_content.append(block if is_same_model else TextContent(text=block.thinking))
                continue

            if isinstance(block, TextContent):
                transformed_content.append(block if is_same_model else TextContent(text=block.text))
                continue

            if isinstance(block, ToolCall):
                transformed_tool_call = block
                if not is_same_model and block.thought_signature:
                    transformed_tool_call = replace(block, thought_signature=None)
                if not is_same_model:
                    normalized_id = _normalize_tool_call_id(block.id, model)
                    if normalized_id != block.id:
                        tool_call_id_map[block.id] = normalized_id
                        transformed_tool_call = replace(transformed_tool_call, id=normalized_id)
                transformed_content.append(transformed_tool_call)
                continue

            transformed_content.append(block)

        transformed.append(replace(message, content=transformed_content))

    result: list[Message] = []
    pending_tool_calls: list[ToolCall] = []
    existing_tool_result_ids: set[str] = set()

    def insert_synthetic_tool_results() -> None:
        nonlocal pending_tool_calls, existing_tool_result_ids
        if not pending_tool_calls:
            return
        for tool_call in pending_tool_calls:
            if tool_call.id not in existing_tool_result_ids:
                result.append(
                    ToolResultMessage(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        content=[TextContent(text="No result provided")],
                        is_error=True,
                        timestamp=now_ms(),
                    )
                )
        pending_tool_calls = []
        existing_tool_result_ids = set()

    for message in transformed:
        if message.role == "assistant":
            insert_synthetic_tool_results()
            if message.stop_reason in ("error", "aborted"):
                continue
            tool_calls = [block for block in message.content if isinstance(block, ToolCall)]
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()
            result.append(message)
        elif message.role == "toolResult":
            existing_tool_result_ids.add(message.tool_call_id)
            result.append(message)
        elif message.role == "user":
            insert_synthetic_tool_results()
            result.append(message)
        else:
            result.append(message)

    insert_synthetic_tool_results()
    return result


def _downgrade_unsupported_images(messages: list[Message], model: Model) -> list[Message]:
    if "image" in model.input:
        return messages

    downgraded: list[Message] = []
    for message in messages:
        if message.role == "user" and isinstance(message.content, list):
            downgraded.append(
                replace(
                    message,
                    content=_replace_images_with_placeholder(message.content, _NON_VISION_USER_IMAGE_PLACEHOLDER),
                )
            )
        elif message.role == "toolResult":
            downgraded.append(
                replace(
                    message,
                    content=_replace_images_with_placeholder(message.content, _NON_VISION_TOOL_IMAGE_PLACEHOLDER),
                )
            )
        else:
            downgraded.append(message)
    return downgraded


def _replace_images_with_placeholder(
    content: list[TextContent | ImageContent], placeholder: str
) -> list[TextContent]:
    result: list[TextContent] = []
    previous_was_placeholder = False
    for block in content:
        if isinstance(block, ImageContent):
            if not previous_was_placeholder:
                result.append(TextContent(text=placeholder))
            previous_was_placeholder = True
            continue
        result.append(block)
        previous_was_placeholder = block.text == placeholder
    return result


def _normalize_tool_call_id(tool_call_id: str, model: Model) -> str:
    if "|" in tool_call_id:
        call_id = tool_call_id.split("|", 1)[0]
        return re.sub(r"[^a-zA-Z0-9_-]", "_", call_id)[:40]
    if model.provider == "openai":
        return tool_call_id[:40]
    return tool_call_id


def _repair_tool_call_name_fragment(name: str) -> str:
    if not name:
        return ""
    separator_indexes = [index for sep in ('"', "'", "<", ">") if (index := name.find(sep)) >= 0]
    if not separator_indexes:
        return name
    first = min(separator_indexes)
    return name[:first] if first > 0 else ""


def _coerce_tool_call_arguments_for_replay(arguments: Any, tool_name: str = "?") -> tuple[dict[str, Any], bool]:
    if arguments is None:
        return {}, False
    if isinstance(arguments, dict):
        return arguments, False
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}, False
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            repaired = repair_tool_call_arguments(arguments, tool_name)
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                return {}, True
            return (parsed, True) if isinstance(parsed, dict) else ({}, True)
        return (parsed, False) if isinstance(parsed, dict) else ({}, True)
    return {}, True


def _convert_message(
    message: Message,
    model: Model | None = None,
) -> dict | None:
    if message.role == "user":
        content = _sanitize_surrogates(message.content) if isinstance(message.content, str) else _convert_user_content_parts(message.content)
        return {"role": "user", "content": content}
    if message.role == "toolResult":
        return _convert_single_tool_result(message)
    # assistant
    text_parts = [_sanitize_surrogates(b.text) for b in message.content if isinstance(b, TextContent)]
    thinking_parts = [b for b in message.content if isinstance(b, ThinkingContent) and b.thinking.strip()]
    tool_calls = []
    for block in message.content:
        if not isinstance(block, ToolCall):
            continue
        name = _repair_tool_call_name_fragment(block.name)
        arguments, _repaired_corruption = _coerce_tool_call_arguments_for_replay(block.arguments, name)
        tool_calls.append(
            {
                "id": block.id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments),
                },
            },
        )
    content_text = "".join(text_parts)
    if not content_text and not thinking_parts and not tool_calls:
        return None
    out: dict = {"role": "assistant", "content": content_text}
    if thinking_parts:
        signature = thinking_parts[0].thinking_signature
        if model is not None and model.provider == "opencode-go" and signature == "reasoning":
            signature = "reasoning_content"
        if signature:
            out[signature] = "\n".join(_sanitize_surrogates(block.thinking) for block in thinking_parts)
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _compact_validation_error_for_provider(tool_name: str, content: str) -> str:
    if not isinstance(content, str) or not content.startswith('Validation failed for tool "'):
        return content
    match = re.match(r'^Validation failed for tool "([^"]+)":\n\s+- ([^\n]+)', content)
    if not match:
        return content
    name = match.group(1) or tool_name or "tool"
    error = match.group(2).strip()
    return (
        f"Tool argument validation failed for {name}: {error}. "
        "The previous tool call did not execute."
    )


def _convert_single_tool_result(
    message: ToolResultMessage,
) -> dict:
    content = _text_of(message.content)
    if not content and any(isinstance(block, ImageContent) for block in message.content):
        content = "(see attached image)"
    if message.is_error:
        content = _compact_validation_error_for_provider(message.tool_name, content)
    return {
        "role": "tool",
        "tool_call_id": message.tool_call_id,
        "name": message.tool_name,
        "content": content,
    }


def _convert_tool_result_group(
    messages: list[Message],
    start_index: int,
    model: Model | None,
) -> tuple[list[dict], int]:
    converted: list[dict] = []
    image_parts: list[dict] = []
    index = start_index
    while index < len(messages) and messages[index].role == "toolResult":
        message = messages[index]
        converted_message = _convert_single_tool_result(message)
        converted.append(converted_message)
        if model is not None and "image" in model.input:
            for block in message.content:
                if isinstance(block, ImageContent):
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                    })
        index += 1
    if image_parts:
        converted.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                *image_parts,
            ],
        })
    return converted, index


def _convert_user_content_parts(content: list[TextContent | ImageContent]) -> list[dict]:
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": _sanitize_surrogates(block.text)})
        elif isinstance(block, ImageContent):
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
            })
    return parts


def _text_of(content) -> str:
    if isinstance(content, str):
        return _sanitize_surrogates(content)
    return "".join(_sanitize_surrogates(b.text) for b in content if isinstance(b, TextContent))


def _sanitize_surrogates(text: str) -> str:
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF)


def _blank(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[], api=model.api, provider=model.provider, model=model.id,
        usage=empty_usage(), stop_reason="stop", timestamp=now_ms(),
    )


def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    if reason is None:
        return "stop", None
    if reason in ("stop", "end"):
        return "stop", None
    if reason == "length":
        return "length", None
    if reason in ("function_call", "tool_calls"):
        return "toolUse", None
    if reason in ("content_filter", "network_error"):
        return "error", f"Provider finish_reason: {reason}"
    return "error", f"Provider finish_reason: {reason}"


def _format_provider_exception(error: Exception, model: Model, configured_model: str | None = None) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return _format_http_status_error(error, model, configured_model)
    return str(error)


def _format_http_status_error(error: httpx.HTTPStatusError, model: Model, configured_model: str | None = None) -> str:
    response = error.response
    status = response.status_code
    reason = response.reason_phrase
    provider = _provider_label(model.provider)
    requested_model = model.id or configured_model or "unknown"
    detail = _extract_response_error_message(response) or reason or str(error)
    lower_detail = detail.lower()
    display_detail = _truncate_provider_error_detail(detail)

    prefix = f"{provider} API error (HTTP {status}{f' {reason}' if reason else ''}) for model {requested_model}."
    if model.provider == "openrouter" and any(pattern in lower_detail for pattern in _OPENROUTER_POLICY_PATTERNS):
        return (
            f"{prefix} OpenRouter blocked the selected endpoint because of account privacy/data-policy settings. "
            "Open https://openrouter.ai/settings/privacy and allow a compatible endpoint, or choose another model. "
            f"Provider message: {display_detail}"
        )
    if status == 403 and model.provider == "openrouter" and _is_openrouter_prompt_guardrail_detail(lower_detail):
        return (
            f"OpenRouter prompt-injection guardrail blocked the request (HTTP 403) for model {requested_model}. "
            "The provider rejected prompt/tool-result content before generation; this is not an API-key failure. "
            "Compact or narrow raw tool output, then retry. "
            f"Provider message: {display_detail}"
        )
    if status == 403:
        if any(pattern in lower_detail for pattern in _BILLING_PATTERNS):
            return (
                f"{provider} billing or account limit failed (HTTP 403) for model {requested_model}. "
                "Check credits, spending limits, plan entitlement, and model access. "
                f"Provider message: {display_detail}"
            )
        return (
            f"{provider} authorization failed (HTTP 403) for model {requested_model}. "
            f"Check {_provider_api_key_label(model.provider)}, account credits, and model access. "
            f"Provider message: {display_detail}"
        )
    if status == 401:
        return (
            f"{provider} authentication failed (HTTP 401) for model {requested_model}. "
            f"Check {_provider_api_key_label(model.provider)} and re-authenticate if needed. "
            f"Provider message: {display_detail}"
        )
    if status == 402:
        return (
            f"{provider} billing or quota failed (HTTP 402) for model {requested_model}. "
            "Add credits or update billing with that provider, then retry. "
            f"Provider message: {display_detail}"
        )
    return f"{prefix} Provider message: {display_detail}"


def _truncate_provider_error_detail(detail: str) -> str:
    max_chars = (
        _PROVIDER_ERROR_DETAIL_HEAD_CHARS
        + len(_PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER)
        + _PROVIDER_ERROR_DETAIL_TAIL_CHARS
    )
    if len(detail) <= max_chars:
        return detail
    return (
        detail[:_PROVIDER_ERROR_DETAIL_HEAD_CHARS]
        + _PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER
        + detail[-_PROVIDER_ERROR_DETAIL_TAIL_CHARS:]
    )


def _extract_response_error_message(response: httpx.Response) -> str:
    text = _read_response_text(response)
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        extracted = _extract_error_message_from_payload(payload)
        if extracted:
            return extracted
    return text


def _read_response_text(response: httpx.Response) -> str:
    try:
        return response.text.strip()
    except httpx.ResponseNotRead:
        try:
            response.read()
            return response.text.strip()
        except Exception:  # noqa: BLE001 - best-effort provider error extraction
            return ""
    except Exception:  # noqa: BLE001 - best-effort provider error extraction
        return ""


def _extract_error_message_from_payload(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("error") or error.get("detail")
        metadata = error.get("metadata")
        parts = [message.strip()] if isinstance(message, str) and message.strip() else []
        if isinstance(metadata, dict):
            patterns = metadata.get("patterns")
            if isinstance(patterns, list):
                normalized_patterns = [str(pattern).strip() for pattern in patterns if str(pattern).strip()]
                if normalized_patterns:
                    parts.append("Patterns: " + ", ".join(normalized_patterns))
            raw = metadata.get("raw")
            if isinstance(raw, str) and raw.strip():
                try:
                    nested = json.loads(raw)
                except json.JSONDecodeError:
                    if not parts:
                        return raw.strip()
                if isinstance(nested, dict):
                    nested_message = _extract_error_message_from_payload(nested)
                    if nested_message:
                        parts.append(nested_message)
        if parts:
            return ". ".join(parts)
    message = payload.get("message") or payload.get("detail")
    return message.strip() if isinstance(message, str) and message.strip() else ""


def _is_openrouter_prompt_guardrail_detail(lower_detail: str) -> bool:
    return any(pattern in lower_detail for pattern in _OPENROUTER_PROMPT_GUARDRAIL_PATTERNS)


def _provider_label(provider: str) -> str:
    if provider == "openrouter":
        return "OpenRouter"
    return provider or "Provider"


def _provider_api_key_label(provider: str) -> str:
    if provider == "openrouter":
        return "OPENROUTER_API_KEY"
    return "the configured API key"


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
    repaired = ""
    in_string = False
    index = 0
    while index < len(json_text):
        char = json_text[index]

        if not in_string:
            repaired += char
            if char == '"':
                in_string = True
            index += 1
            continue

        if char == '"':
            repaired += char
            in_string = False
            index += 1
            continue

        if char == "\\":
            next_char = json_text[index + 1] if index + 1 < len(json_text) else None
            if next_char is None:
                repaired += "\\\\"
                index += 1
                continue

            if next_char == "u":
                unicode_digits = json_text[index + 2 : index + 6]
                if len(unicode_digits) == 4 and all(digit in "0123456789abcdefABCDEF" for digit in unicode_digits):
                    repaired += "\\u" + unicode_digits
                    index += 6
                    continue

            if next_char in _VALID_JSON_ESCAPES:
                repaired += "\\" + next_char
                index += 2
                continue

            repaired += "\\\\"
            index += 1
            continue

        repaired += _escape_control_character(char) if _is_control_character(char) else char
        index += 1

    return repaired


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


def _looks_like_leaked_tool_protocol_text(text: str) -> bool:
    return bool(
        _TOOL_CALL_LEAK_PATTERN.search(text)
        or _TOOL_PROTOCOL_XML_BLOCK_PATTERN.search(text)
        or _TOOL_PROTOCOL_XML_LINE_PATTERN.search(text)
        or _TOOL_PROTOCOL_XML_PREFIX_PATTERN.search(text)
    )


def _strip_leaked_tool_xml(text: str) -> str:
    if not text:
        return text
    stripped = _TOOL_PROTOCOL_XML_BLOCK_PATTERN.sub("", text)
    stripped = _TOOL_PROTOCOL_XML_LINE_PATTERN.sub("", stripped)
    stripped = _TOOL_CALL_LEAK_PATTERN.sub("", stripped)
    return stripped


def _iter_sse_data(
    lines: Iterable[str],
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[str]:
    last_data_at = clock()
    for raw in lines:
        if (
            data_idle_timeout_seconds is not None
            and data_idle_timeout_seconds > 0
            and clock() - last_data_at > data_idle_timeout_seconds
        ):
            seconds = int(data_idle_timeout_seconds)
            raise TimeoutError(f"SSE stream received no data events for {seconds} seconds")
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        last_data_at = clock()
        yield payload


def parse_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
    api_mode: str = "chat_completions",
    tools: Iterable[Tool] | None = None,
) -> Iterator:
    """Pure transform: decoded SSE lines -> AssistantMessageEvent stream."""
    if api_mode == "codex_responses":
        yield from _parse_codex_responses_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=data_idle_timeout_seconds,
            clock=clock,
            include_reasoning=include_reasoning,
        )
        return
    if api_mode == "anthropic_messages":
        yield from _parse_anthropic_messages_sse_chunks(
            lines,
            model,
            data_idle_timeout_seconds=data_idle_timeout_seconds,
            clock=clock,
            include_reasoning=include_reasoning,
        )
        return

    message = _blank(model)
    started = False
    text_index: int | None = None
    text_buf = ""
    thinking_index: int | None = None
    tool_call_blocks_by_index: dict[int, ToolCall] = {}
    tool_call_blocks_by_id: dict[str, ToolCall] = {}
    pending_tool_call_parts: dict[tuple[str, int | str], dict[str, str]] = {}
    tool_arg_bufs: dict[int, str] = {}
    finish_reason = "stop"
    has_finish_reason = False
    stream_leaked_tool_protocol_text = False
    usage = empty_usage()

    def content_index_of(block: TextContent | ThinkingContent | ToolCall) -> int:
        for index, candidate in enumerate(message.content):
            if candidate is block:
                return index
        return -1

    def ensure_start():
        nonlocal started
        if not started:
            started = True
            return StartEvent(partial=message)
        return None

    def end_content_events() -> Iterator:
        for content_index, block in enumerate(message.content):
            if isinstance(block, TextContent):
                block.text = _strip_leaked_tool_xml(block.text)
                yield TextEndEvent(content_index=content_index, content=block.text, partial=message)
            elif isinstance(block, ThinkingContent):
                yield ThinkingEndEvent(content_index=content_index, content=block.thinking, partial=message)
            elif isinstance(block, ToolCall):
                block.arguments = _parse_streaming_json(tool_arg_bufs.get(content_index, ""))
                yield ToolcallEndEvent(content_index=content_index, tool_call=block, partial=message)

        if not started:
            yield StartEvent(partial=message)
        message.usage = usage

    def final_events() -> Iterator:
        leaked_tool_protocol_text = stream_leaked_tool_protocol_text or any(
            isinstance(block, TextContent) and _looks_like_leaked_tool_protocol_text(block.text)
            for block in message.content
        )
        malformed_mutating_tool_names: list[str] = []
        if has_finish_reason and finish_reason == "tool_calls":
            malformed_mutating_tool_names = (
                _malformed_finished_tool_call_names_against_active_schema(message.content, tool_arg_bufs, tools)
                if tools is not None
                else _malformed_finished_mutating_tool_call_names(message.content, tool_arg_bufs)
            )
        should_drop_tool_calls = not has_finish_reason or bool(malformed_mutating_tool_names)
        dropped_tool_names: list[str] = []
        dropped_tool_finish_reason = finish_reason if has_finish_reason else None
        if should_drop_tool_calls:
            if malformed_mutating_tool_names:
                dropped_tool_names = malformed_mutating_tool_names
            else:
                dropped_tool_names = [
                    block.name or "?"
                    for block in message.content
                    if isinstance(block, ToolCall)
                ]
            message.content = [
                block for block in message.content if not isinstance(block, ToolCall)
            ]
            tool_arg_bufs.clear()
        has_remaining_tool_calls = any(isinstance(block, ToolCall) for block in message.content)
        if leaked_tool_protocol_text and not has_remaining_tool_calls:
            for block in message.content:
                if isinstance(block, TextContent):
                    block.text = ""
        yield from end_content_events()
        if dropped_tool_names and malformed_mutating_tool_names:
            message.stop_reason = "length"
            message.response_id = PARTIAL_STREAM_STUB_ID
            diagnostics = list(message.diagnostics or [])
            diagnostics.append(
                {
                    "code": MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE,
                    "dropped_tool_names": dropped_tool_names,
                    "finish_reason": dropped_tool_finish_reason,
                }
            )
            message.diagnostics = diagnostics
            yield DoneEvent(reason="length", message=message)
            return
        if dropped_tool_names and not has_finish_reason:
            message.stop_reason = "length"
            message.response_id = PARTIAL_STREAM_STUB_ID
            diagnostics = list(message.diagnostics or [])
            diagnostics.append(
                {
                    "code": PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE,
                    "dropped_tool_names": dropped_tool_names,
                    "finish_reason": dropped_tool_finish_reason,
                }
            )
            message.diagnostics = diagnostics
            yield DoneEvent(reason="length", message=message)
            return
        if leaked_tool_protocol_text and not has_remaining_tool_calls:
            message.stop_reason = "length"
            message.response_id = PARTIAL_STREAM_STUB_ID
            diagnostics = list(message.diagnostics or [])
            diagnostics.append({"code": LEAKED_TOOL_PROTOCOL_TEXT_CODE})
            message.diagnostics = diagnostics
            yield DoneEvent(reason="length", message=message)
            return
        if not has_finish_reason:
            message.stop_reason = "error"
            message.error_message = "Stream ended without finish_reason"
            yield ErrorEvent(reason="error", error=message)
            return
        reason, error_message = _map_stop_reason(finish_reason)
        if reason == "toolUse" and not any(isinstance(block, ToolCall) for block in message.content):
            reason = "stop"
            error_message = None
        message.stop_reason = reason
        if reason == "error":
            message.error_message = error_message
            yield ErrorEvent(reason="error", error=message)
            return
        yield DoneEvent(reason=reason, message=message)

    try:
        payloads = _iter_sse_data(lines, data_idle_timeout_seconds=data_idle_timeout_seconds, clock=clock)
        for payload in payloads:
            yield from _parse_sse_payload(
                payload,
                model,
                message,
                usage_ref := {"usage": usage},
                state := {
                    "started": started,
                    "text_index": text_index,
                    "text_buf": text_buf,
                    "thinking_index": thinking_index,
                    "tool_call_blocks_by_index": tool_call_blocks_by_index,
                    "tool_call_blocks_by_id": tool_call_blocks_by_id,
                    "pending_tool_call_parts": pending_tool_call_parts,
                    "tool_arg_bufs": tool_arg_bufs,
                    "leaked_tool_protocol_text": stream_leaked_tool_protocol_text,
                    "content_index_of": content_index_of,
                    "ensure_start": ensure_start,
                },
                include_reasoning=include_reasoning,
            )
            usage = usage_ref["usage"]
            started = state["started"]
            text_index = state["text_index"]
            text_buf = state["text_buf"]
            thinking_index = state["thinking_index"]
            stream_leaked_tool_protocol_text = bool(state.get("leaked_tool_protocol_text"))
            if state.get("finish_reason"):
                finish_reason = state["finish_reason"]
                has_finish_reason = True
                yield from final_events()
                return
    except TimeoutError as error:
        yield from end_content_events()
        message.stop_reason = "error"
        message.error_message = str(error)
        yield ErrorEvent(reason="error", error=message)
        return

    yield from final_events()


def _responses_tool_call_id(call_id: str, item_id: str | None) -> str:
    return f"{call_id}|{item_id}" if item_id else call_id


def _map_responses_status(status: str | None) -> tuple[str, str | None]:
    if status in (None, "completed", "in_progress", "queued"):
        return "stop", None
    if status == "incomplete":
        return "length", None
    if status in ("failed", "cancelled"):
        return "error", f"Provider response status: {status}"
    return "error", f"Provider response status: {status}"


def _merge_responses_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not isinstance(raw, dict):
        return usage
    details = raw.get("input_tokens_details")
    cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    output_details = raw.get("output_tokens_details")
    reasoning = int(output_details.get("reasoning_tokens") or 0) if isinstance(output_details, dict) else 0
    input_tokens = int(raw.get("input_tokens") or 0)
    usage.input = max(0, input_tokens - cached) or usage.input
    usage.output = int(raw.get("output_tokens") or 0) or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    if hasattr(usage, "cache_read"):
        usage.cache_read = cached or getattr(usage, "cache_read")
    if hasattr(usage, "reasoning"):
        usage.reasoning = reasoning or getattr(usage, "reasoning")
    return usage


def _parse_codex_responses_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
) -> Iterator:
    message = _blank(model)
    started = False
    usage = empty_usage()
    output_slots: dict[int, tuple[str, int]] = {}
    tool_arg_bufs: dict[int, str] = {}
    completed = False

    def ensure_start():
        nonlocal started
        if not started:
            started = True
            return StartEvent(partial=message)
        return None

    def create_slot(output_index: int, item: dict[str, Any]) -> Iterator:
        item_type = item.get("type")
        if item_type == "reasoning":
            if not include_reasoning:
                return
            start = ensure_start()
            if start:
                yield start
            content_index = len(message.content)
            message.content.append(ThinkingContent(thinking="", thinking_signature=None))
            output_slots[output_index] = ("thinking", content_index)
            yield ThinkingStartEvent(content_index=content_index, partial=message)
            return
        if item_type == "message":
            start = ensure_start()
            if start:
                yield start
            content_index = len(message.content)
            message.content.append(TextContent(text=""))
            output_slots[output_index] = ("text", content_index)
            yield TextStartEvent(content_index=content_index, partial=message)
            return
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            item_id = str(item.get("id") or "") or None
            name = str(item.get("name") or "")
            raw_arguments = item.get("arguments") if isinstance(item.get("arguments"), str) else ""
            start = ensure_start()
            if start:
                yield start
            content_index = len(message.content)
            block = ToolCall(
                id=_responses_tool_call_id(call_id, item_id),
                name=name,
                arguments=_parse_streaming_json(raw_arguments),
            )
            message.content.append(block)
            output_slots[output_index] = ("toolCall", content_index)
            tool_arg_bufs[content_index] = raw_arguments
            yield ToolcallStartEvent(content_index=content_index, partial=message)

    def get_slot(output_index: int, item: dict[str, Any] | None = None) -> tuple[str, int] | None:
        slot = output_slots.get(output_index)
        if slot is None and item is not None:
            for event in create_slot(output_index, item):
                pending_events.append(event)
            slot = output_slots.get(output_index)
        return slot

    pending_events: list[Any] = []

    try:
        payloads = _iter_sse_data(lines, data_idle_timeout_seconds=data_idle_timeout_seconds, clock=clock)
        for payload in payloads:
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "response.created":
                response = event.get("response")
                if isinstance(response, dict) and isinstance(response.get("id"), str):
                    message.response_id = response["id"]
                continue
            if event_type == "response.output_item.added":
                item = event.get("item")
                if isinstance(item, dict) and isinstance(event.get("output_index"), int):
                    yield from create_slot(event["output_index"], item)
                continue
            if event_type in ("response.output_text.delta", "response.refusal.delta"):
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "text" or not isinstance(message.content[content_index], TextContent):
                    continue
                delta = event.get("delta")
                if not isinstance(delta, str) or not delta:
                    continue
                block = message.content[content_index]
                block.text += delta
                yield TextDeltaEvent(content_index=content_index, delta=delta, partial=message)
                continue
            if event_type in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                if not include_reasoning:
                    continue
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "thinking" or not isinstance(message.content[content_index], ThinkingContent):
                    continue
                delta = event.get("delta")
                if not isinstance(delta, str) or not delta:
                    continue
                block = message.content[content_index]
                block.thinking += delta
                yield ThinkingDeltaEvent(content_index=content_index, delta=delta, partial=message)
                continue
            if event_type == "response.function_call_arguments.delta":
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "toolCall" or not isinstance(message.content[content_index], ToolCall):
                    continue
                delta = event.get("delta")
                if not isinstance(delta, str):
                    continue
                tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + delta
                message.content[content_index].arguments = _parse_streaming_json(tool_arg_bufs[content_index])
                yield ToolcallDeltaEvent(content_index=content_index, delta=delta, partial=message)
                continue
            if event_type == "response.function_call_arguments.done":
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    continue
                slot = output_slots.get(output_index)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind != "toolCall" or not isinstance(message.content[content_index], ToolCall):
                    continue
                arguments = event.get("arguments")
                if isinstance(arguments, str):
                    tool_arg_bufs[content_index] = arguments
                    message.content[content_index].arguments = _parse_streaming_json(arguments)
                continue
            if event_type == "response.output_item.done":
                item = event.get("item")
                output_index = event.get("output_index")
                if not isinstance(item, dict) or not isinstance(output_index, int):
                    continue
                pending_events.clear()
                slot = get_slot(output_index, item)
                while pending_events:
                    yield pending_events.pop(0)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind == "text" and isinstance(message.content[content_index], TextContent):
                    parts = item.get("content")
                    if isinstance(parts, list):
                        text = "".join(
                            str(part.get("text") or part.get("refusal") or "")
                            for part in parts
                            if isinstance(part, dict)
                        )
                        if text:
                            message.content[content_index].text = _strip_leaked_tool_xml(text)
                    yield TextEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].text,
                        partial=message,
                    )
                elif kind == "thinking" and isinstance(message.content[content_index], ThinkingContent):
                    summary = item.get("summary")
                    if isinstance(summary, list):
                        text = "\n\n".join(
                            str(part.get("text") or "")
                            for part in summary
                            if isinstance(part, dict) and part.get("text")
                        )
                        if text:
                            message.content[content_index].thinking = text
                    message.content[content_index].thinking_signature = json.dumps(item)
                    yield ThinkingEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].thinking,
                        partial=message,
                    )
                elif kind == "toolCall" and isinstance(message.content[content_index], ToolCall):
                    raw_arguments = item.get("arguments")
                    if isinstance(raw_arguments, str):
                        tool_arg_bufs[content_index] = raw_arguments
                    message.content[content_index].arguments = _parse_complete_tool_arguments(
                        tool_arg_bufs.get(content_index, "")
                    ) or {}
                    yield ToolcallEndEvent(
                        content_index=content_index,
                        tool_call=message.content[content_index],
                        partial=message,
                    )
                output_slots.pop(output_index, None)
                continue
            if event_type in ("response.completed", "response.incomplete"):
                response = event.get("response")
                if isinstance(response, dict):
                    completed = True
                    if isinstance(response.get("id"), str):
                        message.response_id = response["id"]
                    usage = _merge_responses_usage(usage, response.get("usage"))
                    reason, error_message = _map_responses_status(response.get("status"))
                    if reason == "stop" and any(isinstance(block, ToolCall) for block in message.content):
                        reason = "toolUse"
                    message.usage = usage
                    message.stop_reason = reason
                    if reason == "error":
                        message.error_message = error_message
                        yield ErrorEvent(reason="error", error=message)
                    else:
                        yield DoneEvent(reason=reason, message=message)
                    return
            if event_type == "response.failed":
                message.stop_reason = "error"
                response = event.get("response")
                error = response.get("error") if isinstance(response, dict) else None
                if isinstance(error, dict):
                    message.error_message = str(error.get("message") or error.get("code") or "Provider response failed")
                else:
                    message.error_message = "Provider response failed"
                yield ErrorEvent(reason="error", error=message)
                return
    except TimeoutError as error:
        message.stop_reason = "error"
        message.error_message = str(error)
        yield ErrorEvent(reason="error", error=message)
        return

    if not completed:
        message.stop_reason = "error"
        message.error_message = "Responses stream ended before a terminal response event"
        yield ErrorEvent(reason="error", error=message)


def _map_anthropic_stop_reason(reason: str | None) -> tuple[str, str | None]:
    if reason in (None, "end_turn", "stop_sequence", "pause_turn"):
        return "stop", None
    if reason == "tool_use":
        return "toolUse", None
    if reason in ("max_tokens", "model_context_window_exceeded"):
        return "length", None
    if reason == "refusal":
        return "error", "The model refused to complete the request"
    return "error", f"Provider stop_reason: {reason}"


def _merge_anthropic_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not isinstance(raw, dict):
        return usage
    input_tokens = int(raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_write = int(raw.get("cache_creation_input_tokens") or 0)
    usage.input = input_tokens or usage.input
    usage.output = output_tokens or usage.output
    usage.total_tokens = (usage.input or 0) + (usage.output or 0) + cache_read + cache_write
    if hasattr(usage, "cache_read"):
        usage.cache_read = cache_read or getattr(usage, "cache_read")
    if hasattr(usage, "cache_write"):
        usage.cache_write = cache_write or getattr(usage, "cache_write")
    output_details = raw.get("output_tokens_details")
    if hasattr(usage, "reasoning") and isinstance(output_details, dict):
        usage.reasoning = int(output_details.get("thinking_tokens") or 0) or getattr(usage, "reasoning")
    return usage


def _parse_anthropic_messages_sse_chunks(
    lines: Iterable[str],
    model: Model,
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_reasoning: bool = True,
) -> Iterator:
    message = _blank(model)
    started = False
    usage = empty_usage()
    block_slots: dict[int, tuple[str, int]] = {}
    tool_arg_bufs: dict[int, str] = {}
    stop_reason = "stop"
    error_message: str | None = None
    saw_message_start = False
    saw_message_stop = False

    def ensure_start():
        nonlocal started
        if not started:
            started = True
            return StartEvent(partial=message)
        return None

    try:
        payloads = _iter_sse_data(lines, data_idle_timeout_seconds=data_idle_timeout_seconds, clock=clock)
        for payload in payloads:
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "message_start":
                saw_message_start = True
                raw_message = event.get("message")
                if isinstance(raw_message, dict):
                    if isinstance(raw_message.get("id"), str):
                        message.response_id = raw_message["id"]
                    usage = _merge_anthropic_usage(usage, raw_message.get("usage"))
                continue
            if event_type == "content_block_start":
                index = event.get("index")
                content_block = event.get("content_block")
                if not isinstance(index, int) or not isinstance(content_block, dict):
                    continue
                block_type = content_block.get("type")
                start = ensure_start()
                if start:
                    yield start
                content_index = len(message.content)
                if block_type == "text":
                    initial_text = content_block.get("text") if isinstance(content_block.get("text"), str) else ""
                    message.content.append(TextContent(text=initial_text))
                    block_slots[index] = ("text", content_index)
                    yield TextStartEvent(content_index=content_index, partial=message)
                elif block_type == "thinking" and include_reasoning:
                    initial_thinking = content_block.get("thinking") if isinstance(content_block.get("thinking"), str) else ""
                    signature = content_block.get("signature") if isinstance(content_block.get("signature"), str) else None
                    message.content.append(ThinkingContent(thinking=initial_thinking, thinking_signature=signature))
                    block_slots[index] = ("thinking", content_index)
                    yield ThinkingStartEvent(content_index=content_index, partial=message)
                elif block_type == "redacted_thinking" and include_reasoning:
                    signature = content_block.get("data") if isinstance(content_block.get("data"), str) else None
                    message.content.append(
                        ThinkingContent(thinking="[Reasoning redacted]", thinking_signature=signature, redacted=True)
                    )
                    block_slots[index] = ("thinking", content_index)
                    yield ThinkingStartEvent(content_index=content_index, partial=message)
                elif block_type == "tool_use":
                    raw_input = content_block.get("input")
                    initial_args = raw_input if isinstance(raw_input, dict) else {}
                    raw_arguments = json.dumps(initial_args) if initial_args else ""
                    message.content.append(
                        ToolCall(
                            id=str(content_block.get("id") or ""),
                            name=str(content_block.get("name") or ""),
                            arguments=initial_args,
                        )
                    )
                    block_slots[index] = ("toolCall", content_index)
                    tool_arg_bufs[content_index] = raw_arguments
                    yield ToolcallStartEvent(content_index=content_index, partial=message)
                continue
            if event_type == "content_block_delta":
                index = event.get("index")
                delta = event.get("delta")
                if not isinstance(index, int) or not isinstance(delta, dict):
                    continue
                slot = block_slots.get(index)
                if slot is None:
                    continue
                kind, content_index = slot
                delta_type = delta.get("type")
                if delta_type == "text_delta" and kind == "text" and isinstance(message.content[content_index], TextContent):
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        message.content[content_index].text += text
                        yield TextDeltaEvent(content_index=content_index, delta=text, partial=message)
                elif (
                    delta_type == "thinking_delta"
                    and kind == "thinking"
                    and isinstance(message.content[content_index], ThinkingContent)
                ):
                    thinking = delta.get("thinking")
                    if isinstance(thinking, str) and thinking:
                        message.content[content_index].thinking += thinking
                        yield ThinkingDeltaEvent(content_index=content_index, delta=thinking, partial=message)
                elif (
                    delta_type == "signature_delta"
                    and kind == "thinking"
                    and isinstance(message.content[content_index], ThinkingContent)
                ):
                    signature = delta.get("signature")
                    if isinstance(signature, str):
                        block = message.content[content_index]
                        block.thinking_signature = (block.thinking_signature or "") + signature
                elif (
                    delta_type == "input_json_delta"
                    and kind == "toolCall"
                    and isinstance(message.content[content_index], ToolCall)
                ):
                    partial_json = delta.get("partial_json")
                    if isinstance(partial_json, str):
                        tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + partial_json
                        message.content[content_index].arguments = _parse_streaming_json(tool_arg_bufs[content_index])
                        yield ToolcallDeltaEvent(content_index=content_index, delta=partial_json, partial=message)
                continue
            if event_type == "content_block_stop":
                index = event.get("index")
                if not isinstance(index, int):
                    continue
                slot = block_slots.pop(index, None)
                if slot is None:
                    continue
                kind, content_index = slot
                if kind == "text" and isinstance(message.content[content_index], TextContent):
                    message.content[content_index].text = _strip_leaked_tool_xml(message.content[content_index].text)
                    yield TextEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].text,
                        partial=message,
                    )
                elif kind == "thinking" and isinstance(message.content[content_index], ThinkingContent):
                    yield ThinkingEndEvent(
                        content_index=content_index,
                        content=message.content[content_index].thinking,
                        partial=message,
                    )
                elif kind == "toolCall" and isinstance(message.content[content_index], ToolCall):
                    message.content[content_index].arguments = _parse_complete_tool_arguments(
                        tool_arg_bufs.get(content_index, "")
                    ) or {}
                    yield ToolcallEndEvent(
                        content_index=content_index,
                        tool_call=message.content[content_index],
                        partial=message,
                    )
                continue
            if event_type == "message_delta":
                delta = event.get("delta")
                if isinstance(delta, dict):
                    reason, mapped_error = _map_anthropic_stop_reason(delta.get("stop_reason"))
                    stop_reason = reason
                    error_message = mapped_error
                usage = _merge_anthropic_usage(usage, event.get("usage"))
                continue
            if event_type == "message_stop":
                saw_message_stop = True
                message.usage = usage
                if stop_reason == "error":
                    message.stop_reason = "error"
                    message.error_message = error_message
                    yield ErrorEvent(reason="error", error=message)
                else:
                    message.stop_reason = stop_reason
                    yield DoneEvent(reason=stop_reason, message=message)
                return
            if event_type == "error":
                message.stop_reason = "error"
                error = event.get("error")
                if isinstance(error, dict):
                    message.error_message = str(error.get("message") or error.get("type") or "Anthropic stream error")
                else:
                    message.error_message = "Anthropic stream error"
                yield ErrorEvent(reason="error", error=message)
                return
    except TimeoutError as error:
        message.stop_reason = "error"
        message.error_message = str(error)
        yield ErrorEvent(reason="error", error=message)
        return

    if saw_message_start and not saw_message_stop:
        message.stop_reason = "error"
        message.error_message = "Anthropic stream ended before message_stop"
        yield ErrorEvent(reason="error", error=message)


def _parse_sse_payload(
    payload: str,
    model: Model,
    message: AssistantMessage,
    usage_ref: dict,
    state: dict,
    *,
    include_reasoning: bool = True,
) -> Iterator:
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return
    usage = usage_ref["usage"]
    tool_arg_bufs = state["tool_arg_bufs"]
    tool_call_blocks_by_index = state["tool_call_blocks_by_index"]
    tool_call_blocks_by_id = state["tool_call_blocks_by_id"]
    pending_tool_call_parts = state.setdefault("pending_tool_call_parts", {})
    content_index_of = state["content_index_of"]
    ensure_start = state["ensure_start"]
    text_index = state["text_index"]
    text_buf = state["text_buf"]
    thinking_index = state["thinking_index"]

    if not message.response_id and isinstance(chunk.get("id"), str) and chunk["id"]:
        message.response_id = chunk["id"]
    chunk_model = chunk.get("model")
    if (
        not message.response_model
        and isinstance(chunk_model, str)
        and chunk_model
        and chunk_model != model.id
    ):
        message.response_model = chunk_model
    usage = _merge_usage(usage, chunk.get("usage"))
    choices = chunk.get("choices") or []
    if not choices:
        usage_ref["usage"] = usage
        return
    choice = choices[0]
    if not chunk.get("usage"):
        usage = _merge_usage(usage, choice.get("usage"))
    delta = choice.get("delta") or {}

    found_reasoning_field = None
    for field in _REASONING_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value:
            found_reasoning_field = field
            break

    if found_reasoning_field and include_reasoning:
        reasoning = delta[found_reasoning_field]
        thinking_signature = (
            "reasoning_content"
            if model.provider == "opencode-go" and found_reasoning_field == "reasoning"
            else found_reasoning_field
        )
        start = ensure_start()
        if start:
            state["started"] = True
            yield start
        if thinking_index is None:
            thinking_index = len(message.content)
            state["thinking_index"] = thinking_index
            message.content.append(ThinkingContent(thinking="", thinking_signature=thinking_signature))
            yield ThinkingStartEvent(content_index=thinking_index, partial=message)
        message.content[thinking_index].thinking += reasoning
        yield ThinkingDeltaEvent(content_index=thinking_index, delta=reasoning, partial=message)

    content_piece = delta.get("content")
    if content_piece:
        if state.get("leaked_tool_protocol_text"):
            pass
        elif _looks_like_leaked_tool_protocol_text(text_buf + content_piece):
            state["leaked_tool_protocol_text"] = True
        else:
            start = ensure_start()
            if start:
                state["started"] = True
                yield start
            if text_index is None:
                text_index = len(message.content)
                state["text_index"] = text_index
                message.content.append(TextContent(text=""))
                yield TextStartEvent(content_index=text_index, partial=message)
            text_buf += content_piece
            state["text_buf"] = text_buf
            message.content[text_index].text = text_buf
            yield TextDeltaEvent(content_index=text_index, delta=content_piece, partial=message)

    for tc in delta.get("tool_calls") or []:
        stream_index = tc.get("index")
        if not isinstance(stream_index, int):
            stream_index = None
        tool_call_id = tc.get("id") or ""
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        name_fragment = _repair_tool_call_name_fragment(fn.get("name")) if isinstance(fn.get("name"), str) else ""
        arg_fragment = fn.get("arguments") if isinstance(fn.get("arguments"), str) else ""
        tool_call = tool_call_blocks_by_index.get(stream_index) if stream_index is not None else None
        if tool_call is None and tool_call_id:
            tool_call = tool_call_blocks_by_id.get(tool_call_id)
        if tool_call is None:
            pending_key = (
                ("index", stream_index)
                if stream_index is not None
                else (("id", tool_call_id) if tool_call_id else None)
            )
            if pending_key is None:
                continue
            pending = pending_tool_call_parts.setdefault(
                pending_key,
                {"id": "", "name": "", "arguments": ""},
            )
            if tool_call_id:
                pending["id"] = tool_call_id
            if name_fragment:
                pending["name"] = name_fragment
            if arg_fragment:
                pending["arguments"] += arg_fragment
            if not pending["id"] or not pending["name"]:
                continue

            start = ensure_start()
            if start:
                state["started"] = True
                yield start
            tool_call = ToolCall(
                id=pending["id"],
                name=pending["name"],
                arguments=_parse_streaming_json(pending["arguments"]),
            )
            content_index = len(message.content)
            message.content.append(tool_call)
            tool_arg_bufs[content_index] = pending["arguments"]
            if stream_index is not None:
                tool_call_blocks_by_index[stream_index] = tool_call
            if tool_call.id:
                tool_call_blocks_by_id[tool_call.id] = tool_call
            pending_tool_call_parts.pop(pending_key, None)
            yield ToolcallStartEvent(content_index=content_index, partial=message)
            if arg_fragment:
                yield ToolcallDeltaEvent(content_index=content_index, delta=arg_fragment, partial=message)
            continue
        else:
            start = ensure_start()
            if start:
                state["started"] = True
                yield start
            content_index = content_index_of(tool_call)
            if content_index < 0:
                continue
            if stream_index is not None:
                tool_call_blocks_by_index[stream_index] = tool_call
            if tool_call_id:
                tool_call_blocks_by_id[tool_call_id] = tool_call
        if tool_call_id and not tool_call.id:
            tool_call.id = tool_call_id
            tool_call_blocks_by_id[tool_call_id] = tool_call
        if name_fragment and not tool_call.name:
            tool_call.name = name_fragment
        if arg_fragment:
            tool_arg_bufs[content_index] = tool_arg_bufs.get(content_index, "") + arg_fragment
            tool_call.arguments = _parse_streaming_json(tool_arg_bufs[content_index])
        yield ToolcallDeltaEvent(content_index=content_index, delta=arg_fragment, partial=message)

    usage_ref["usage"] = usage
    if choice.get("finish_reason"):
        state["finish_reason"] = choice["finish_reason"]
    return


def _merge_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not raw:
        return usage
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    usage.input = prompt or usage.input
    usage.output = completion or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    return usage


class AppV2EnvProvider:
    api = PROVIDER_API

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.provider_profile = get_provider_profile("openrouter") or ProviderProfile(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
        self.transport = get_transport(self.provider_profile.api_mode)

    def _transport_for_profile(self, profile: ProviderProfile):
        if profile.api_mode == self.provider_profile.api_mode:
            return self.transport
        return get_transport(profile.api_mode)

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        threading.Thread(target=self._run, args=(s, model, context, options), daemon=True).start()
        return s

    stream_simple = stream

    def _run(self, s: AssistantMessageEventStream, model: Model, context: Context, options) -> None:
        try:
            messages, tools = convert_messages(context, model)
            option_params = getattr(options, "generation_params", None) if options is not None else None
            if option_params is not None and not isinstance(option_params, GenerationParams):
                option_params = None
            generation_params = merge_generation_params(self.config.generation_params, option_params)
            max_tokens = getattr(options, "max_tokens", None) if options is not None else None
            if max_tokens is not None:
                generation_params = merge_generation_params(
                    generation_params,
                    GenerationParams(max_tokens=max_tokens, sources={"max_tokens": "runtime_options"}),
                )
            runtime = resolve_provider_runtime(
                model.provider,
                explicit_base_url=model.base_url,
                fallback_base_url=self.config.base_url,
            )
            profile = runtime.profile or self.provider_profile
            transport = (
                self._transport_for_profile(profile)
                if profile.api_mode == runtime.api_mode
                else get_transport(runtime.api_mode)
            )
            endpoint_path = runtime.endpoint_path
            base_url = runtime.base_url or model.base_url or profile.base_url or self.config.base_url
            api_mode = getattr(transport, "api_mode", runtime.api_mode)
            generation_payload = build_generation_payload(
                provider=runtime.provider,
                api_mode=api_mode,
                params=generation_params,
                tools_enabled=bool(tools),
            )
            on_generation_warning = (
                getattr(options, "on_generation_warning", None) if options is not None else None
            )
            if callable(on_generation_warning):
                for warning in generation_payload.warnings:
                    on_generation_warning(warning)
            transport_kwargs = {
                "model": model.id or self.config.model,
                "messages": messages,
                "tools": tools,
                "profile": profile,
                "stream": True,
                "temperature": generation_payload.temperature,
                "max_tokens": generation_payload.max_tokens,
                "provider_preferences": generation_payload.provider_preferences,
                "request_overrides": generation_payload.request_overrides,
            }
            session_id = getattr(options, "session_id", None) if options is not None else None
            if isinstance(session_id, str) and session_id.strip():
                transport_kwargs["session_id"] = session_id
            reasoning_config = getattr(options, "reasoning_config", None) if options is not None else None
            if isinstance(reasoning_config, dict):
                transport_kwargs["reasoning_config"] = reasoning_config
            body = transport.build_kwargs(
                **transport_kwargs,
            )
            on_payload = getattr(options, "on_payload", None) if options is not None else None
            if callable(on_payload):
                next_body = on_payload(body)
                if isinstance(next_body, dict):
                    body = next_body
            option_headers = getattr(options, "headers", None) if options is not None else None
            headers = dict(profile.default_headers)
            if isinstance(option_headers, dict):
                headers.update({str(key): str(value) for key, value in option_headers.items()})
            extra_headers = body.pop("extra_headers", None)
            if isinstance(extra_headers, dict):
                headers.update({str(key): str(value) for key, value in extra_headers.items()})
            option_api_key = getattr(options, "api_key", None) if options is not None else None
            api_key = option_api_key if isinstance(option_api_key, str) and option_api_key.strip() else self.config.api_key
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            headers.setdefault("Content-Type", "application/json")
            url = base_url.rstrip("/") + endpoint_path
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    on_response = getattr(options, "on_response", None) if options is not None else None
                    if callable(on_response):
                        on_response({"status": response.status_code, "headers": dict(response.headers)})
                    response.raise_for_status()
                    for event in parse_sse_chunks(
                        response.iter_lines(),
                        model,
                        data_idle_timeout_seconds=self.config.timeout_seconds,
                        include_reasoning=bool(getattr(options, "reasoning", None)),
                        api_mode=api_mode,
                        tools=context.tools,
                    ):
                        s.push(event)
        except Exception as exc:  # encode failure as an error event, never raise
            err = _blank(model)
            err.stop_reason = "error"
            err.error_message = _format_provider_exception(exc, model, self.config.model)
            s.push(ErrorEvent(reason="error", error=err))


class NullProvider:
    api = PROVIDER_API

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        err = _blank(model)
        err.stop_reason = "error"
        err.error_message = "model transport not configured"
        s.push(ErrorEvent(reason="error", error=err))
        return s

    stream_simple = stream


def _has_runtime_api_key(options) -> bool:
    api_key = getattr(options, "api_key", None) if options is not None else None
    return isinstance(api_key, str) and bool(api_key.strip())


class RuntimeAuthProvider:
    api = PROVIDER_API

    def __init__(self, config: ModelConfig) -> None:
        self.configured = AppV2EnvProvider(config)
        self.null = NullProvider()

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        if _has_runtime_api_key(options):
            return self.configured.stream(model, context, options)
        return self.null.stream(model, context, options)

    stream_simple = stream


def create_appv2_env_provider(
    prefix: str = "APPV2_WORKER_LLM",
    dotenv_path: "str" = ".env",
    *,
    config: ModelConfig | None = None,
) -> ApiProvider:
    config = config or load_model_config(prefix, dotenv_path)
    impl = AppV2EnvProvider(config) if config.enabled else RuntimeAuthProvider(config)
    return ApiProvider(api=PROVIDER_API, stream=impl.stream, stream_simple=impl.stream_simple)
