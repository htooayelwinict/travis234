"""provider transports for travis."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from travis.ai.providers.base import (
    NormalizedResponse,
    NormalizedToolCall,
    NormalizedUsage,
    ProviderProfile,
)
from travis.ai.providers.responses_translation import (
    convert_responses_messages,
    convert_responses_tools,
    split_deferred_tools,
)
from travis.ai.providers.transport_families._shared import (
    content_to_text as _content_to_text,
    tool_arguments as _tool_arguments,
    tool_function as _tool_function,
)
from travis.ai.providers.transport_families.anthropic import AnthropicMessagesTransport
from travis.ai.providers.transport_families.chat_completions import (
    ChatCompletionsTransport,
    _clamp_openai_prompt_cache_key,
)
from travis.ai.providers.transport_families.bedrock import BedrockConverseStreamTransport
from travis.ai.providers.transport_families.google import (
    GoogleGenerativeAITransport,
    GoogleVertexTransport,
)
from travis.ai.providers.transport_families.mistral import MistralConversationsTransport
from travis.ai.providers.transport_families.unsupported import UnsupportedTransport
from travis.ai.types import Context




def _split_responses_tool_call_id(tool_call_id: str) -> tuple[str, str | None]:
    if "|" not in tool_call_id:
        return tool_call_id, None
    call_id, item_id = tool_call_id.split("|", 1)
    return call_id, item_id or None




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






def _codex_instructions(
    context: Context | None,
    messages: list[dict[str, Any]],
) -> str:
    if context is not None and isinstance(context.system_prompt, str) and context.system_prompt.strip():
        return context.system_prompt
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
            continue
        content = _content_to_text(message.get("content"))
        if content.strip():
            return content
    return "You are a helpful assistant."


class CodexResponsesTransport:
    api = "openai-codex-responses"
    api_mode = "openai_codex_responses"
    endpoint_path = "/responses"

    @staticmethod
    def build_url(
        base_url: str,
        _model: str,
        _options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = (base_url or "https://chatgpt.com/backend-api").rstrip("/")
        if normalized.endswith("/codex/responses"):
            return normalized
        if normalized.endswith("/codex"):
            return normalized + "/responses"
        return normalized + "/codex/responses"

    @staticmethod
    def finalize_headers(
        headers: Mapping[str, str],
        *,
        api_key: str | None,
        session_id: str | None,
        **_kwargs: Any,
    ) -> dict[str, str]:
        if not api_key:
            raise ValueError("No API key for provider: openai-codex")
        from travis.ai.providers.codex_auth import build_codex_sse_headers

        return build_codex_sse_headers(headers, api_key, session_id)

    def convert_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        include_system: bool = False,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "system":
                if include_system:
                    converted.append({"role": "system", "content": _content_to_text(message.get("content"))})
                continue
            if role == "developer":
                if include_system:
                    converted.append({"role": "developer", "content": _content_to_text(message.get("content"))})
                continue
            if role == "user":
                converted.append({"role": "user", "content": _openai_content_to_responses(message.get("content"))})
                continue
            if role == "assistant":
                for reasoning_item in message.get("codex_reasoning_items") or []:
                    if isinstance(reasoning_item, dict) and reasoning_item.get("type") == "reasoning":
                        converted.append(copy.deepcopy(reasoning_item))
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
                            _tool_arguments(function.get("arguments") or "{}", name),
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
        reasoning_summary: str | None = None,
        service_tier: str | None = None,
        text_verbosity: str | None = None,
        tool_choice: Any | None = None,
        request_overrides: dict[str, Any] | None = None,
        context: Context | None = None,
        target_model: Any = None,
        model_compat: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        instructions = _codex_instructions(context, messages)
        immediate_tools: list[Any] = []
        deferred_tools: dict[str, Any] = {}
        if context is not None and target_model is not None:
            immediate_tools, deferred_tools = split_deferred_tools(
                context,
                (model_compat or {}).get("supportsToolSearch") is True,
            )
            converted_input = convert_responses_messages(
                target_model,
                context,
                {"openai", "openai-codex", "opencode"},
                include_system_prompt=False,
                deferred_tools=deferred_tools,
            )
        else:
            converted_input = self.convert_messages(messages)
        body: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": stream,
            "instructions": instructions,
            "input": converted_input,
            "text": {"verbosity": text_verbosity or "low"},
            "include": ["reasoning.encrypted_content"],
            "tool_choice": tool_choice if tool_choice is not None else "auto",
            "parallel_tool_calls": True,
        }
        if service_tier is not None:
            body["service_tier"] = service_tier
        if session_id:
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
        if immediate_tools:
            body["tools"] = convert_responses_tools(immediate_tools, strict=None)
        elif context is None and tools:
            body["tools"] = self.convert_tools(tools)
        if isinstance(reasoning_config, dict):
            effort = str(reasoning_config.get("effort") or "").strip().lower()
            if reasoning_config.get("enabled") is False or effort == "none":
                effort = "none"
            if effort:
                body["reasoning"] = {"effort": effort, "summary": reasoning_summary or "auto"}
        if request_overrides:
            body.update(request_overrides)
        for unsupported_field in ("temperature", "top_p", "max_output_tokens"):
            body.pop(unsupported_field, None)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class OpenAIResponsesTransport(CodexResponsesTransport):
    api = "openai-responses"
    api_mode = "openai_responses"

    @staticmethod
    def finalize_headers(
        headers: Mapping[str, str],
        **_kwargs: Any,
    ) -> dict[str, str]:
        return dict(headers)

    @staticmethod
    def build_url(
        base_url: str,
        _model: str,
        _options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = base_url.rstrip("/")
        return normalized if normalized.endswith("/responses") else normalized + "/responses"

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = super().convert_tools(tools)
        for tool in converted:
            tool["strict"] = False
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
        cache_retention: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        reasoning_summary: str | None = None,
        service_tier: str | None = None,
        tool_choice: Any | None = None,
        request_overrides: dict[str, Any] | None = None,
        model_compat: dict[str, Any] | None = None,
        model_reasoning: bool = False,
        model_thinking_level_map: dict[str, str | None] | None = None,
        context: Context | None = None,
        target_model: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        resolved_cache_retention = cache_retention or "short"
        supports_long = (model_compat or {}).get("supportsLongCacheRetention") is not False
        immediate_tools: list[Any] = []
        deferred_tools: dict[str, Any] = {}
        if context is not None and target_model is not None:
            immediate_tools, deferred_tools = split_deferred_tools(
                context,
                (model_compat or {}).get("supportsToolSearch") is True,
            )
            converted_input = convert_responses_messages(
                target_model,
                context,
                {"openai", "openai-codex", "opencode"},
                deferred_tools=deferred_tools,
            )
        else:
            converted_input = self.convert_messages(messages, include_system=True)
        body: dict[str, Any] = {
            "model": model,
            "input": converted_input,
            "stream": stream,
            "store": False,
        }
        if session_id and resolved_cache_retention != "none" and target_model is not None:
            from travis.ai.providers.openai_compat import resolve_openai_compat

            compat = resolve_openai_compat(target_model)
            if compat.session_affinity_format == "openrouter":
                body["extra_headers"] = {"x-session-id": session_id}
            else:
                affinity_headers = {"x-client-request-id": session_id}
                if compat.session_affinity_format == "openai":
                    affinity_headers["session_id"] = session_id
                body["extra_headers"] = affinity_headers
        if session_id and resolved_cache_retention != "none":
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(session_id)
        if resolved_cache_retention == "long" and supports_long:
            body["prompt_cache_retention"] = "24h"
        if max_tokens is not None:
            body["max_output_tokens"] = max(max_tokens, 16)
        if temperature is not None:
            body["temperature"] = temperature
        if service_tier is not None:
            body["service_tier"] = service_tier
        if immediate_tools:
            body["tools"] = convert_responses_tools(immediate_tools)
        elif context is None and tools:
            body["tools"] = self.convert_tools(tools)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if model_reasoning:
            config = reasoning_config or {}
            enabled = config.get("enabled", True) is not False
            effort = str(config.get("effort") or "").strip().lower()
            if enabled and ((effort and effort != "none") or reasoning_summary):
                selected_effort = effort if effort and effort != "none" else "medium"
                mapped = (model_thinking_level_map or {}).get(selected_effort, selected_effort)
                body["reasoning"] = {"effort": mapped, "summary": reasoning_summary or "auto"}
                body["include"] = ["reasoning.encrypted_content"]
            elif (model_thinking_level_map or {}).get("off", "none") is not None:
                body["reasoning"] = {"effort": (model_thinking_level_map or {}).get("off", "none")}
        if request_overrides:
            body.update(request_overrides)
        return body


class AzureOpenAIResponsesTransport(OpenAIResponsesTransport):
    api = "azure-openai-responses"
    api_mode = "azure_openai_responses"

    @staticmethod
    def _resolve_base_url(base_url: str, options: object | None) -> str:
        explicit = str(getattr(options, "azure_base_url", None) or os.environ.get("AZURE_OPENAI_BASE_URL") or "").strip()
        resource = str(
            getattr(options, "azure_resource_name", None)
            or os.environ.get("AZURE_OPENAI_RESOURCE_NAME")
            or ""
        ).strip()
        raw = explicit or (f"https://{resource}.openai.azure.com/openai/v1" if resource else base_url)
        parsed = urlsplit(raw.strip().rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid Azure OpenAI base URL: {raw}")
        hostname = (parsed.hostname or "").lower()
        is_azure_host = hostname.endswith(
            (".openai.azure.com", ".cognitiveservices.azure.com", ".ai.azure.com")
        )
        path = parsed.path.rstrip("/")
        if is_azure_host and path in {"", "/openai", "/openai/v1/responses"}:
            return urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1", "", ""))
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))

    @classmethod
    def build_url(
        cls,
        base_url: str,
        _model: str,
        options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = cls._resolve_base_url(base_url, options)
        parsed = urlsplit(normalized)
        path = parsed.path if parsed.path.endswith("/responses") else parsed.path.rstrip("/") + "/responses"
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault(
            "api-version",
            str(getattr(options, "azure_api_version", None) or os.environ.get("AZURE_OPENAI_API_VERSION") or "v1"),
        )
        return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))

    @staticmethod
    def finalize_headers(
        headers: Mapping[str, str],
        *,
        api_key: str | None,
        **_kwargs: Any,
    ) -> dict[str, str]:
        resolved = {key: value for key, value in headers.items() if key.lower() != "authorization"}
        if not api_key:
            raise ValueError("No API key for provider: azure-openai-responses")
        for key in tuple(resolved):
            if key.lower() == "api-key":
                del resolved[key]
        resolved["api-key"] = api_key
        return resolved

    def build_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        session_id = kwargs.get("session_id")
        options = kwargs.get("options")
        model_id = str(kwargs.get("model") or "")
        deployment_name = str(getattr(options, "azure_deployment_name", None) or "").strip()
        if not deployment_name:
            mappings = str(os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME_MAP") or "")
            for entry in mappings.split(","):
                source, separator, target = entry.strip().partition("=")
                if separator and source.strip() == model_id and target.strip():
                    deployment_name = target.strip()
                    break
        kwargs["model"] = deployment_name or model_id
        body = super().build_kwargs(**kwargs)
        body.pop("extra_headers", None)
        body.pop("prompt_cache_retention", None)
        body.pop("service_tier", None)
        body.pop("tool_choice", None)
        if session_id:
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(str(session_id))
        return body


def get_transport(api_mode: str):
    """Compatibility wrapper for the registry-owned lookup function."""

    from travis.ai.providers.transport_registry import get_transport as registry_get_transport

    return registry_get_transport(api_mode)
