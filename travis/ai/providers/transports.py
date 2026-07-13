"""provider transports for travis."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from travis.ai.providers.base import (
    OMIT_TEMPERATURE,
    NormalizedResponse,
    NormalizedToolCall,
    NormalizedUsage,
    ProviderProfile,
)
from travis.ai.providers.message_sanitization import repair_tool_call_arguments

DEVELOPER_ROLE_MODELS = ("gpt-5", "codex")
_MUTATING_REPLAY_TOOL_NAMES = {"write"}
_PROTOCOL_SHAPED_CONTENT_PATTERNS = (
    "<function",
    "</function",
    "<tool_call",
    "</tool_call",
    "<function_call",
    "</function_call",
    "<parameter",
    "</parameter",
)
_MAX_HISTORICAL_WRITE_CONTENT_REPLAY_CHARS = 8192


def _merge_body(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key == "provider" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            provider = dict(merged[key])
            provider.update(value)
            merged[key] = provider
            continue
        merged[key] = value
    return merged


def _model_consumes_thought_signature(model: Any) -> bool:
    model_name = str(model or "").lower()
    return "gemini" in model_name or "gemma" in model_name


def _content_to_text(content: Any) -> str:
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


def _tool_function(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    return function if isinstance(function, dict) else tool


def _tool_arguments(arguments: Any, tool_name: str = "?") -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments, strict=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            try:
                parsed = json.loads(repair_tool_call_arguments(arguments, tool_name), strict=False)
            except (json.JSONDecodeError, TypeError, ValueError):
                return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _sanitize_historical_mutating_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name not in _MUTATING_REPLAY_TOOL_NAMES:
        return arguments
    sanitized: dict[str, Any] = {}
    path = arguments.get("path")
    if isinstance(path, str) and path:
        sanitized["path"] = path
    content = arguments.get("content")
    if isinstance(content, str) and not _should_omit_historical_write_content(content):
        sanitized["content"] = content
    return sanitized


def _should_omit_historical_write_content(content: str) -> bool:
    if len(content) > _MAX_HISTORICAL_WRITE_CONTENT_REPLAY_CHARS:
        return True
    lowered = content.lower()
    return any(pattern in lowered for pattern in _PROTOCOL_SHAPED_CONTENT_PATTERNS)


def _is_mutating_argument_failure(tool_name: str, content: Any) -> bool:
    if tool_name not in _MUTATING_REPLAY_TOOL_NAMES:
        return False
    text = _content_to_text(content).lower()
    if not text:
        return False
    name = tool_name.lower()
    return (
        f"tool argument validation failed for {name}:" in text
        or f'validation failed for tool "{name}"' in text
    )


def _failed_mutating_tool_call_ids(messages: list[dict[str, Any]]) -> set[str]:
    failed_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        tool_name = str(message.get("name") or "")
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and _is_mutating_argument_failure(tool_name, message.get("content")):
            failed_ids.add(tool_call_id)
    return failed_ids


def _looks_like_failed_tool_call_spillover(content: Any) -> bool:
    text = _content_to_text(content)
    if not text:
        return False
    lowered = text.lower()
    return (
        "received arguments:" in lowered
        or "being interpreted as tool arguments" in lowered
        or "being parsed as tool arguments" in lowered
        or any(pattern in lowered for pattern in _PROTOCOL_SHAPED_CONTENT_PATTERNS)
    )


def _split_responses_tool_call_id(tool_call_id: str) -> tuple[str, str | None]:
    if "|" not in tool_call_id:
        return tool_call_id, None
    call_id, item_id = tool_call_id.split("|", 1)
    return call_id, item_id or None


def _data_url_to_anthropic_image(part: dict[str, Any]) -> dict[str, Any] | None:
    image_url = part.get("image_url")
    if not isinstance(image_url, dict):
        return None
    url = image_url.get("url")
    if not isinstance(url, str):
        return None
    match = re.match(r"^data:([^;]+);base64,(.*)$", url, flags=re.DOTALL)
    if not match:
        return None
    media_type, data = match.groups()
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _openai_content_to_anthropic(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part.strip():
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                blocks.append({"type": "text", "text": text})
            continue
        if part.get("type") == "image_url":
            image = _data_url_to_anthropic_image(part)
            if image is not None:
                blocks.append(image)
    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text") or "")
    return blocks


def _openai_content_to_responses(content: Any, *, output: bool = False) -> list[dict[str, Any]]:
    text_type = "output_text" if output else "input_text"
    image_type = "input_image"
    if isinstance(content, str):
        return [{"type": text_type, "text": content, **({"annotations": []} if output else {})}]
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": text_type, "text": part, **({"annotations": []} if output else {})})
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            blocks.append({"type": text_type, "text": part["text"], **({"annotations": []} if output else {})})
        elif not output and part.get("type") == "image_url":
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                blocks.append({"type": image_type, "detail": "auto", "image_url": image_url["url"]})
    return blocks


class ChatCompletionsTransport:
    api_mode = "chat_completions"
    endpoint_path = "/chat/completions"

    def convert_messages(self, messages: list[dict[str, Any]], *, model: str | None = None) -> list[dict[str, Any]]:
        """Strip travis/Travis-internal replay fields before provider payload.

        This preserves the established chat-completions provider boundary: conversation
        history can carry provider/private bookkeeping, but strict OpenAI-
        compatible providers must only receive schema-valid chat messages.
        """
        strip_extra_content = not _model_consumes_thought_signature(model)
        failed_mutating_tool_call_ids = _failed_mutating_tool_call_ids(messages)
        needs_sanitize = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            if (
                "codex_reasoning_items" in message
                or "codex_message_items" in message
                or "tool_name" in message
                or "timestamp" in message
            ):
                needs_sanitize = True
                break
            if any(isinstance(key, str) and key.startswith("_") for key in message):
                needs_sanitize = True
                break
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict) and (
                        "call_id" in tool_call
                        or "response_item_id" in tool_call
                        or (strip_extra_content and "extra_content" in tool_call)
                        or (
                            isinstance(tool_call.get("id"), str)
                            and tool_call["id"] in failed_mutating_tool_call_ids
                        )
                        or self._tool_call_arguments_need_repair(tool_call)
                        or self._tool_call_arguments_need_replay_sanitize(tool_call)
                    ):
                        needs_sanitize = True
                        break
                if needs_sanitize:
                    break

        if not needs_sanitize:
            return messages

        sanitized = copy.deepcopy(messages)
        for message in sanitized:
            if not isinstance(message, dict):
                continue
            message.pop("codex_reasoning_items", None)
            message.pop("codex_message_items", None)
            message.pop("tool_name", None)
            message.pop("timestamp", None)
            for key in [key for key in message if isinstance(key, str) and key.startswith("_")]:
                message.pop(key, None)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                failed_tool_names = [
                    str(_tool_function(tool_call).get("name") or "")
                    for tool_call in tool_calls
                    if isinstance(tool_call, dict)
                    and isinstance(tool_call.get("id"), str)
                    and tool_call["id"] in failed_mutating_tool_call_ids
                ]
                if failed_tool_names and _looks_like_failed_tool_call_spillover(message.get("content")):
                    message["content"] = ""
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call.pop("call_id", None)
                    tool_call.pop("response_item_id", None)
                    if strip_extra_content:
                        tool_call.pop("extra_content", None)
                    call_id = tool_call.get("id")
                    function = _tool_function(tool_call)
                    name = str(function.get("name") or "")
                    if isinstance(call_id, str) and call_id in failed_mutating_tool_call_ids:
                        function["arguments"] = "{}"
                        continue
                    self._repair_tool_call_arguments_in_place(tool_call)
        return sanitized

    @staticmethod
    def _tool_call_arguments_need_repair(tool_call: dict[str, Any]) -> bool:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return False
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            return True
        try:
            json.loads(arguments, strict=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            return True
        return False

    @staticmethod
    def _tool_call_arguments_need_replay_sanitize(tool_call: dict[str, Any]) -> bool:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return False
        name = str(function.get("name") or "")
        if name not in _MUTATING_REPLAY_TOOL_NAMES:
            return False
        arguments = _tool_arguments(function.get("arguments"), name)
        return _sanitize_historical_mutating_tool_arguments(name, arguments) != arguments

    @staticmethod
    def _repair_tool_call_arguments_in_place(tool_call: dict[str, Any]) -> bool:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return False
        arguments = function.get("arguments")
        name = str(function.get("name") or "?")
        if isinstance(arguments, str):
            repaired = repair_tool_call_arguments(arguments, name)
        elif arguments is None:
            repaired = "{}"
        else:
            repaired = repair_tool_call_arguments(str(arguments), name)
        parsed = _tool_arguments(repaired, name)
        sanitized = _sanitize_historical_mutating_tool_arguments(name, parsed)
        function["arguments"] = json.dumps(sanitized, separators=(",", ":"))
        return name in _MUTATING_REPLAY_TOOL_NAMES and sanitized != parsed

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Chat Completions tools are already in OpenAI-compatible format."""
        return tools

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        provider_preferences: dict[str, Any] | None = None,
        session_id: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        extra_body_additions: dict[str, Any] | None = None,
        timeout: float | None = None,
        base_url: str | None = None,
        openrouter_min_coding_score: float | str | None = None,
        qwen_session_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prepared_messages = profile.prepare_messages(self.convert_messages(messages, model=model))
        model_lower = (model or "").lower()
        if (
            prepared_messages
            and isinstance(prepared_messages[0], dict)
            and prepared_messages[0].get("role") == "system"
            and any(part in model_lower for part in DEVELOPER_ROLE_MODELS)
        ):
            prepared_messages = list(prepared_messages)
            prepared_messages[0] = {**prepared_messages[0], "role": "developer"}
        prepared_tools = self.convert_tools(tools) if tools else None

        body: dict[str, Any] = {
            "model": model,
            "messages": prepared_messages,
            "stream": stream,
        }
        if timeout is not None:
            body["timeout"] = timeout
        if profile.fixed_temperature is OMIT_TEMPERATURE:
            pass
        elif profile.fixed_temperature is not None:
            body["temperature"] = profile.fixed_temperature
        elif temperature is not None:
            body["temperature"] = temperature
        if prepared_tools:
            body["tools"] = prepared_tools
        resolved_max_tokens = max_tokens if max_tokens is not None else profile.get_max_tokens(model)
        if resolved_max_tokens is not None:
            body["max_tokens"] = resolved_max_tokens

        extra_body = dict(profile.build_extra_body(
            session_id=session_id,
            provider_preferences=provider_preferences,
            model=model,
            tools=tools,
            base_url=base_url,
            reasoning_config=reasoning_config,
            openrouter_min_coding_score=openrouter_min_coding_score,
        ))
        api_extra_body, top_level = profile.build_api_kwargs_extras(
            reasoning_config=reasoning_config,
            supports_reasoning=reasoning_config is not None,
            model=model,
            session_id=session_id,
            tools=tools,
            provider_preferences=provider_preferences,
            base_url=base_url,
            qwen_session_metadata=qwen_session_metadata,
        )
        extra_body = _merge_body(extra_body, api_extra_body)
        if extra_body_additions:
            extra_body = _merge_body(extra_body, extra_body_additions)
        body.update(top_level)
        if request_overrides:
            for key, value in request_overrides.items():
                if key == "extra_body" and isinstance(value, dict):
                    extra_body = _merge_body(extra_body, value)
                else:
                    body[key] = value
        body.update(extra_body)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        """Normalize OpenAI ChatCompletion-like responses.

        The runtime streams directly from raw SSE today, while this transport
        keeps provider response shape centralized for non-streaming, tests,
        and future provider adapters.
        """
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason or "stop"

        tool_calls: list[NormalizedToolCall] | None = None
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            tool_calls = []
            for raw_tool_call in raw_tool_calls:
                provider_data: dict[str, Any] = {}
                extra_content = getattr(raw_tool_call, "extra_content", None)
                if extra_content is None and hasattr(raw_tool_call, "model_extra"):
                    model_extra = getattr(raw_tool_call, "model_extra", None) or {}
                    if isinstance(model_extra, dict):
                        extra_content = model_extra.get("extra_content")
                if extra_content is not None:
                    if hasattr(extra_content, "model_dump"):
                        try:
                            extra_content = extra_content.model_dump()
                        except Exception:
                            pass
                    provider_data["extra_content"] = extra_content
                function = getattr(raw_tool_call, "function", None)
                tool_calls.append(
                    NormalizedToolCall(
                        id=getattr(raw_tool_call, "id", None),
                        name=getattr(function, "name", "") if function is not None else "",
                        arguments=getattr(function, "arguments", "") if function is not None else "",
                        provider_data=provider_data or None,
                    )
                )

        usage = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage = NormalizedUsage(
                prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(raw_usage, "total_tokens", 0) or 0),
                cached_tokens=int(getattr(raw_usage, "cached_tokens", 0) or 0),
            )

        reasoning = getattr(message, "reasoning", None)
        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content is None and hasattr(message, "model_extra"):
            model_extra = getattr(message, "model_extra", None) or {}
            if isinstance(model_extra, dict):
                reasoning_content = model_extra.get("reasoning_content")

        provider_data: dict[str, Any] = {}
        if reasoning_content is not None:
            provider_data["reasoning_content"] = reasoning_content
        reasoning_details = getattr(message, "reasoning_details", None)
        if reasoning_details:
            provider_data["reasoning_details"] = reasoning_details

        content = getattr(message, "content", None)
        refusal = getattr(message, "refusal", None)
        if refusal is None and hasattr(message, "model_extra"):
            model_extra = getattr(message, "model_extra", None) or {}
            if isinstance(model_extra, dict):
                refusal = model_extra.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            provider_data["refusal"] = refusal
            has_text = isinstance(content, str) and bool(content.strip())
            has_tool_calls = bool(tool_calls)
            if not has_text and not has_tool_calls:
                content = refusal
                if finish_reason in (None, "stop"):
                    finish_reason = "content_filter"

        return NormalizedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            reasoning=reasoning or reasoning_content,
            usage=usage,
            provider_data=provider_data or None,
        )

    def validate_response(self, response: Any) -> bool:
        return bool(response is not None and getattr(response, "choices", None))

    def extract_cache_stats(self, response: Any) -> dict[str, int] | None:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
        if details is None:
            return None
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        written = int(getattr(details, "cache_write_tokens", 0) or 0)
        if cached or written:
            return {"cached_tokens": cached, "creation_tokens": written}
        return None


class AnthropicMessagesTransport:
    api_mode = "anthropic_messages"
    endpoint_path = "/v1/messages"

    def convert_messages(self, messages: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if not isinstance(message, dict):
                index += 1
                continue
            role = message.get("role")
            if role == "system":
                index += 1
                continue
            if role == "user":
                content = _openai_content_to_anthropic(message.get("content"))
                if content:
                    converted.append({"role": "user", "content": content})
                index += 1
                continue
            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                text = _content_to_text(message.get("content"))
                if text.strip():
                    blocks.append({"type": "text", "text": text})
                for tool_call in message.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    function = _tool_function(tool_call)
                    name = str(function.get("name") or "")
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tool_call.get("id") or ""),
                            "name": name,
                            "input": _sanitize_historical_mutating_tool_arguments(
                                name,
                                _tool_arguments(function.get("arguments"), name),
                            ),
                        }
                    )
                if blocks:
                    converted.append({"role": "assistant", "content": blocks})
                index += 1
                continue
            if role == "tool":
                tool_results: list[dict[str, Any]] = []
                while index < len(messages):
                    tool_message = messages[index]
                    if not isinstance(tool_message, dict) or tool_message.get("role") != "tool":
                        break
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": str(tool_message.get("tool_call_id") or ""),
                            "content": _content_to_text(tool_message.get("content")),
                            "is_error": bool(tool_message.get("is_error", False)),
                        }
                    )
                    index += 1
                if tool_results:
                    converted.append({"role": "user", "content": tool_results})
                continue
            index += 1
        return converted

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            function = _tool_function(tool)
            name = str(function.get("name") or "")
            if not name:
                continue
            converted.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "input_schema": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
                }
            )
        return converted

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": self.convert_messages(messages),
            "max_tokens": max_tokens if max_tokens is not None else profile.get_max_tokens(model) or 4096,
            "stream": stream,
        }
        system_blocks = [
            {"type": "text", "text": content}
            for message in messages
            if isinstance(message, dict)
            and message.get("role") == "system"
            and (content := _content_to_text(message.get("content")).strip())
        ]
        if system_blocks:
            body["system"] = system_blocks
        if temperature is not None and profile.fixed_temperature is not OMIT_TEMPERATURE:
            body["temperature"] = profile.fixed_temperature if profile.fixed_temperature is not None else temperature
        if tools:
            body["tools"] = self.convert_tools(tools)
        if isinstance(reasoning_config, dict):
            if reasoning_config.get("enabled") is False or reasoning_config.get("effort") == "none":
                body["thinking"] = {"type": "disabled"}
            elif reasoning_config.get("effort"):
                body["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        if request_overrides:
            body.update(request_overrides)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class CodexResponsesTransport:
    api_mode = "codex_responses"
    endpoint_path = "/responses"

    def convert_messages(self, messages: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "system":
                continue
            if role == "user":
                converted.append({"role": "user", "content": _openai_content_to_responses(message.get("content"))})
                continue
            if role == "assistant":
                output_text = _openai_content_to_responses(message.get("content"), output=True)
                if output_text:
                    converted.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": output_text,
                            "status": "completed",
                        }
                    )
                for tool_call in message.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    function = _tool_function(tool_call)
                    name = str(function.get("name") or "")
                    call_id, item_id = _split_responses_tool_call_id(str(tool_call.get("id") or ""))
                    item: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(
                            _sanitize_historical_mutating_tool_arguments(
                                name,
                                _tool_arguments(
                                    repair_tool_call_arguments(str(function.get("arguments") or "{}"), name),
                                    name,
                                ),
                            ),
                            separators=(",", ":"),
                        ),
                    }
                    if item_id:
                        item["id"] = item_id
                    converted.append(item)
                continue
            if role == "tool":
                call_id, _item_id = _split_responses_tool_call_id(str(message.get("tool_call_id") or ""))
                converted.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _content_to_text(message.get("content")),
                    }
                )
        return converted

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            function = _tool_function(tool)
            name = str(function.get("name") or "")
            if not name:
                continue
            converted.append(
                {
                    "type": "function",
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
                    "strict": None,
                }
            )
        return converted

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        session_id: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        instructions = next(
            (
                content
                for message in messages
                if isinstance(message, dict)
                and message.get("role") == "system"
                and (content := _content_to_text(message.get("content")).strip())
            ),
            "You are a helpful assistant.",
        )
        body: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": stream,
            "instructions": instructions,
            "input": self.convert_messages(messages),
            "text": {"verbosity": "low"},
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        if session_id:
            body["prompt_cache_key"] = session_id
        if temperature is not None and profile.fixed_temperature is not OMIT_TEMPERATURE:
            body["temperature"] = profile.fixed_temperature if profile.fixed_temperature is not None else temperature
        resolved_max_tokens = max_tokens if max_tokens is not None else profile.get_max_tokens(model)
        if resolved_max_tokens is not None:
            body["max_output_tokens"] = resolved_max_tokens
        if tools:
            body["tools"] = self.convert_tools(tools)
        if isinstance(reasoning_config, dict):
            effort = str(reasoning_config.get("effort") or "").strip().lower()
            if reasoning_config.get("enabled") is False or effort == "none":
                effort = "none"
            if effort:
                body["reasoning"] = {"effort": effort, "summary": "auto"}
        if request_overrides:
            body.update(request_overrides)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class UnsupportedTransport:
    endpoint_path = "/unsupported"

    def __init__(self, api_mode: str) -> None:
        self.api_mode = api_mode

    def convert_messages(self, messages: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        return messages

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tools

    def build_kwargs(self, **_kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(f"{self.api_mode} transport is not supported by the travis HTTP provider")

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="error")


_REGISTRY = {
    ChatCompletionsTransport.api_mode: ChatCompletionsTransport(),
    AnthropicMessagesTransport.api_mode: AnthropicMessagesTransport(),
    CodexResponsesTransport.api_mode: CodexResponsesTransport(),
    "bedrock_converse": UnsupportedTransport("bedrock_converse"),
}


def get_transport(api_mode: str):
    return _REGISTRY[api_mode]
