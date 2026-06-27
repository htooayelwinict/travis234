"""appv2-env provider: OpenAI/OpenRouter-compatible streaming over httpx SSE."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Iterator

import httpx

from appv23.ai.env_config import ModelConfig, load_model_config
from appv23.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from appv23.ai.stream import ApiProvider
from appv23.ai.types import (
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
    ToolCall,
    ToolResultMessage,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)

PROVIDER_API = "openai-completions"

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


def convert_messages(context: Context, model: Model | None = None) -> "tuple[list[dict], list[dict] | None]":
    messages: list[dict] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": _sanitize_surrogates(context.system_prompt)})
    context_messages = _transform_messages(context.messages, model) if model is not None else context.messages
    index = 0
    while index < len(context_messages):
        message = context_messages[index]
        if message.role == "toolResult":
            tool_messages, next_index = _convert_tool_result_group(context_messages, index, model)
            messages.extend(tool_messages)
            index = next_index
            continue
        messages.append(_convert_message(message, model))
        index += 1
    tools = None
    if context.tools:
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in context.tools
        ]
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


def _convert_message(message: Message, model: Model | None = None) -> dict:
    if message.role == "user":
        content = _sanitize_surrogates(message.content) if isinstance(message.content, str) else _convert_user_content_parts(message.content)
        return {"role": "user", "content": content}
    if message.role == "toolResult":
        return _convert_single_tool_result(message)
    # assistant
    text_parts = [_sanitize_surrogates(b.text) for b in message.content if isinstance(b, TextContent)]
    thinking_parts = [b for b in message.content if isinstance(b, ThinkingContent) and b.thinking.strip()]
    tool_calls = [
        {"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.arguments)}}
        for b in message.content
        if isinstance(b, ToolCall)
    ]
    out: dict = {"role": "assistant", "content": "".join(text_parts)}
    if thinking_parts:
        signature = thinking_parts[0].thinking_signature
        if model is not None and model.provider == "opencode-go" and signature == "reasoning":
            signature = "reasoning_content"
        if signature:
            out[signature] = "\n".join(_sanitize_surrogates(block.thinking) for block in thinking_parts)
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _convert_single_tool_result(message: ToolResultMessage) -> dict:
    content = _text_of(message.content)
    if not content and any(isinstance(block, ImageContent) for block in message.content):
        content = "(see attached image)"
    return {
        "role": "tool",
        "tool_call_id": message.tool_call_id,
        "name": message.tool_name,
        "content": content,
    }


def _convert_tool_result_group(
    messages: list[Message], start_index: int, model: Model | None
) -> tuple[list[dict], int]:
    converted: list[dict] = []
    image_parts: list[dict] = []
    index = start_index
    while index < len(messages) and messages[index].role == "toolResult":
        message = messages[index]
        converted.append(_convert_single_tool_result(message))
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
) -> Iterator:
    """Pure transform: decoded SSE lines -> AssistantMessageEvent stream."""
    message = _blank(model)
    started = False
    text_index: int | None = None
    text_buf = ""
    thinking_index: int | None = None
    tool_call_blocks_by_index: dict[int, ToolCall] = {}
    tool_call_blocks_by_id: dict[str, ToolCall] = {}
    tool_arg_bufs: dict[int, str] = {}
    finish_reason = "stop"
    has_finish_reason = False
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
        yield from end_content_events()
        if not has_finish_reason:
            message.stop_reason = "error"
            message.error_message = "Stream ended without finish_reason"
            yield ErrorEvent(reason="error", error=message)
            return
        reason, error_message = _map_stop_reason(finish_reason)
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
                    "tool_arg_bufs": tool_arg_bufs,
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
        start = ensure_start()
        if start:
            state["started"] = True
            yield start
        stream_index = tc.get("index")
        if not isinstance(stream_index, int):
            stream_index = None
        tool_call_id = tc.get("id") or ""
        fn = tc.get("function") or {}
        tool_call = tool_call_blocks_by_index.get(stream_index) if stream_index is not None else None
        if tool_call is None and tool_call_id:
            tool_call = tool_call_blocks_by_id.get(tool_call_id)
        if tool_call is None:
            tool_call = ToolCall(id=tool_call_id, name=fn.get("name") or "", arguments={})
            content_index = len(message.content)
            message.content.append(tool_call)
            tool_arg_bufs[content_index] = ""
            if stream_index is not None:
                tool_call_blocks_by_index[stream_index] = tool_call
            if tool_call_id:
                tool_call_blocks_by_id[tool_call_id] = tool_call
            yield ToolcallStartEvent(content_index=content_index, partial=message)
        else:
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
        fn = tc.get("function") or {}
        if fn.get("name") and not tool_call.name:
            tool_call.name = fn["name"]
        arg_fragment = fn.get("arguments") or ""
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

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        threading.Thread(target=self._run, args=(s, model, context, options), daemon=True).start()
        return s

    stream_simple = stream

    def _run(self, s: AssistantMessageEventStream, model: Model, context: Context, options) -> None:
        try:
            messages, tools = convert_messages(context, model)
            body: dict = {
                "model": model.id or self.config.model,
                "messages": messages,
                "stream": True,
                "temperature": self.config.temperature,
            }
            if tools:
                body["tools"] = tools
            max_tokens = getattr(options, "max_tokens", None) if options is not None else None
            if max_tokens is None:
                max_tokens = self.config.max_tokens
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            if self.config.provider_sort:
                body["provider"] = {"sort": self.config.provider_sort, "allow_fallbacks": True}
            option_headers = getattr(options, "headers", None) if options is not None else None
            headers = {str(key): str(value) for key, value in option_headers.items()} if isinstance(option_headers, dict) else {}
            option_api_key = getattr(options, "api_key", None) if options is not None else None
            api_key = option_api_key if isinstance(option_api_key, str) and option_api_key.strip() else self.config.api_key
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            headers.setdefault("Content-Type", "application/json")
            url = self.config.base_url.rstrip("/") + "/chat/completions"
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    response.raise_for_status()
                    for event in parse_sse_chunks(
                        response.iter_lines(),
                        model,
                        data_idle_timeout_seconds=self.config.timeout_seconds,
                        include_reasoning=bool(getattr(options, "reasoning", None)),
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


def create_appv2_env_provider(prefix: str = "APPV2_WORKER_LLM", dotenv_path: "str" = ".env") -> ApiProvider:
    config = load_model_config(prefix, dotenv_path)
    impl = AppV2EnvProvider(config) if config.enabled else NullProvider()
    return ApiProvider(api=PROVIDER_API, stream=impl.stream, stream_simple=impl.stream_simple)
