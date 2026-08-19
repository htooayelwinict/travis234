"""Read-only validation helpers for sanitized provider wire fixtures."""

from __future__ import annotations

import json
import re
import base64
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from travis.ai.providers.base import OMIT_TEMPERATURE, ProviderProfile
from travis.ai.providers.bedrock_stream import _parse_bedrock_events
from travis.ai.providers.chat_stream import parse_sse_chunks
from travis.ai.providers.transports import get_transport
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)


SCHEMA_VERSION = 1
CASE_GROUPS = ("requestCases", "responseCases", "streamCases")
SENSITIVE_HEADER_NAMES = {
    "api-key",
    "authorization",
    "cf-aig-authorization",
    "proxy-authorization",
    "x-api-key",
    "x-goog-api-key",
}
ALLOWED_SECRET_PLACEHOLDERS = {
    "<ACCOUNT_ID>",
    "<API_KEY>",
    "<REQUEST_ID>",
    "<SESSION_ID>",
    "<USER_AGENT>",
    "Bearer <API_KEY>",
}
PRIVATE_PATH = re.compile(r"^(?:/Users/|/home/|/private/|/tmp/|[A-Za-z]:[\\/]Users[\\/])")
JWT_LIKE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
TOKEN_LIKE = re.compile(r"\b(?:sk|gh[opusr]|xox[abprs])-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
WIRE_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "provider_wire"


class FixtureValidationError(ValueError):
    """A provider wire fixture is unsafe, incomplete, or nondeterministic."""


def canonical_fixture_json(fixture: Mapping[str, Any]) -> str:
    """Serialize a fixture with the only accepted stable JSON representation."""

    return json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _validate_string(value: str, *, key: str, location: str) -> None:
    if PRIVATE_PATH.match(value):
        raise FixtureValidationError(f"private path at {location}")
    if JWT_LIKE.search(value) or TOKEN_LIKE.search(value):
        raise FixtureValidationError(f"token-like value at {location}")
    if value.lower().startswith("bearer ") and value not in ALLOWED_SECRET_PLACEHOLDERS:
        raise FixtureValidationError(f"authorization value must use a placeholder at {location}")
    if key.lower() in SENSITIVE_HEADER_NAMES and value not in ALLOWED_SECRET_PLACEHOLDERS:
        raise FixtureValidationError(f"authorization value must use a placeholder at {location}")


def _validate_value(value: object, *, location: str, key: str = "") -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            if not isinstance(raw_key, str):
                raise FixtureValidationError(f"non-string key at {location}")
            lowered = raw_key.lower()
            if "update" in lowered and ("environment" in lowered or "fixture" in lowered):
                raise FixtureValidationError(f"environment-controlled update mode at {location}.{raw_key}")
            _validate_string(raw_key, key="", location=f"{location}.<key>")
            _validate_value(nested, location=f"{location}.{raw_key}", key=raw_key)
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_value(nested, location=f"{location}[{index}]", key=key)
        return
    if isinstance(value, str):
        _validate_string(value, key=key, location=location)


def validate_wire_fixture(fixture: Mapping[str, Any]) -> None:
    """Validate the deterministic schema and recursively reject sensitive data."""

    if fixture.get("schemaVersion") != SCHEMA_VERSION:
        raise FixtureValidationError(f"unsupported schema version: {fixture.get('schemaVersion')!r}")
    if not isinstance(fixture.get("apiMode"), str) or not fixture["apiMode"]:
        raise FixtureValidationError("apiMode must be a non-empty string")
    if not isinstance(fixture.get("endpointPath"), str):
        raise FixtureValidationError("endpointPath must be a string")
    required_case_fields = {
        "requestCases": {"expectedBody", "expectedHeaders", "input", "name"},
        "responseCases": {"expectedResponse", "input", "name"},
        "streamCases": {"expectedEvents", "input", "name"},
    }
    for group in CASE_GROUPS:
        cases = fixture.get(group)
        if not isinstance(cases, list) or not cases:
            raise FixtureValidationError(f"{group} must contain at least one case")
        names: set[str] = set()
        for index, case in enumerate(cases):
            if not isinstance(case, Mapping):
                raise FixtureValidationError(f"{group}[{index}] must be an object")
            missing = required_case_fields[group] - set(case)
            if missing:
                raise FixtureValidationError(f"{group}[{index}] missing fields: {sorted(missing)}")
            name = case.get("name")
            if not isinstance(name, str) or not name:
                raise FixtureValidationError(f"{group}[{index}].name must be a non-empty string")
            if name in names:
                raise FixtureValidationError(f"duplicate case name in {group}: {name}")
            names.add(name)
    _validate_value(fixture, location="fixture")


def load_wire_fixture(path: Path) -> dict[str, Any]:
    """Load one fixture without any implicit update or recording behavior."""

    raw = path.read_text(encoding="utf-8")
    try:
        fixture = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixtureValidationError(f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(fixture, dict):
        raise FixtureValidationError(f"fixture root must be an object: {path.name}")
    validate_wire_fixture(fixture)
    if raw != canonical_fixture_json(fixture):
        raise FixtureValidationError(f"fixture must use canonical JSON ordering: {path.name}")
    return fixture


def wire_fixture_paths() -> tuple[Path, ...]:
    """Return the reviewed fixture files in deterministic filename order."""

    return tuple(sorted(WIRE_FIXTURE_DIR.glob("*.json")))


def _model_from_facts(facts: Mapping[str, Any], api_mode: str) -> Model:
    return Model(
        id=str(facts["id"]),
        name=str(facts.get("name") or facts["id"]),
        api=str(facts.get("api") or api_mode),
        provider=str(facts.get("provider") or "fixture"),
        base_url=str(facts.get("baseUrl") or "https://provider.example/v1"),
        reasoning=bool(facts.get("reasoning", False)),
        thinking_level_map=dict(facts.get("thinkingLevelMap") or {}) or None,
        input=list(facts.get("input") or ["text"]),
        context_window=int(facts.get("contextWindow") or 0),
        max_tokens=int(facts.get("maxTokens") or 0),
        headers=dict(facts.get("headers") or {}) or None,
        compat=dict(facts.get("compat") or {}) or None,
    )


def _content_block(spec: Mapping[str, Any]) -> TextContent | ThinkingContent | ImageContent | ToolCall:
    block_type = spec.get("type")
    if block_type == "text":
        return TextContent(text=str(spec.get("text") or ""), text_signature=spec.get("textSignature"))
    if block_type == "thinking":
        return ThinkingContent(
            thinking=str(spec.get("thinking") or ""),
            thinking_signature=spec.get("thinkingSignature"),
            redacted=bool(spec.get("redacted", False)),
        )
    if block_type == "image":
        return ImageContent(data=str(spec.get("data") or ""), mime_type=str(spec.get("mimeType") or "image/png"))
    if block_type == "toolCall":
        return ToolCall(
            id=str(spec.get("id") or ""),
            name=str(spec.get("name") or ""),
            arguments=dict(spec.get("arguments") or {}),
            thought_signature=spec.get("thoughtSignature"),
        )
    raise FixtureValidationError(f"unsupported fixture content block: {block_type!r}")


def _context_from_spec(spec: Mapping[str, Any] | None, model: Model) -> Context | None:
    if spec is None:
        return None
    messages = []
    for raw in spec.get("messages") or []:
        role = raw.get("role")
        if role == "user":
            raw_content = raw.get("content", "")
            content = (
                [_content_block(item) for item in raw_content]
                if isinstance(raw_content, list)
                else str(raw_content)
            )
            messages.append(UserMessage(content=content))
        elif role == "assistant":
            messages.append(
                AssistantMessage(
                    content=[_content_block(item) for item in raw.get("content") or []],
                    api=str(raw.get("api") or model.api),
                    provider=str(raw.get("provider") or model.provider),
                    model=str(raw.get("model") or model.id),
                    usage=empty_usage(),
                    stop_reason=str(raw.get("stopReason") or "stop"),
                )
            )
        elif role == "toolResult":
            messages.append(
                ToolResultMessage(
                    tool_call_id=str(raw.get("toolCallId") or ""),
                    tool_name=str(raw.get("toolName") or ""),
                    content=[_content_block(item) for item in raw.get("content") or []],
                    is_error=bool(raw.get("isError", False)),
                    added_tool_names=list(raw.get("addedToolNames") or []) or None,
                )
            )
        else:
            raise FixtureValidationError(f"unsupported fixture message role: {role!r}")
    tools = [
        Tool(
            name=str(tool["name"]),
            description=str(tool.get("description") or ""),
            parameters=dict(tool.get("parameters") or {"type": "object"}),
        )
        for tool in spec.get("tools") or []
    ]
    return Context(
        messages=messages,
        system_prompt=spec.get("systemPrompt"),
        tools=tools or None,
    )


def _profile_from_spec(spec: Mapping[str, Any], api_mode: str) -> ProviderProfile:
    fixed_temperature: Any = spec.get("fixedTemperature")
    if spec.get("omitTemperature") is True:
        fixed_temperature = OMIT_TEMPERATURE
    return ProviderProfile(
        name=str(spec.get("name") or "fixture"),
        api_mode=str(spec.get("apiMode") or api_mode),
        base_url=str(spec.get("baseUrl") or "https://provider.example/v1"),
        auth_type=str(spec.get("authType") or "api_key"),
        default_headers=dict(spec.get("defaultHeaders") or {}),
        fixed_temperature=fixed_temperature,
        default_max_tokens=spec.get("defaultMaxTokens"),
    )


def _synthetic_codex_token() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "fixture-account"}},
            separators=(",", ":"),
        ).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def _credential(options: Mapping[str, Any], api_mode: str) -> tuple[str | None, str]:
    kind = str(options.get("credentialKind") or "api_key")
    if kind == "none":
        return None, kind
    if api_mode == "openai-codex-responses":
        return _synthetic_codex_token(), "oauth"
    if kind == "oauth":
        return "sk-ant-" + "oat-fixture-placeholder", kind
    return "fixture-api-key", kind


def _merge_headers(target: dict[str, str], updates: Mapping[str, object] | None) -> None:
    for raw_key, value in (updates or {}).items():
        key = str(raw_key)
        for existing in tuple(target):
            if existing.lower() == key.lower():
                del target[existing]
        if value is not None:
            target[key] = str(value)


def _sanitize_headers(headers: Mapping[str, str], session_id: str | None) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in SENSITIVE_HEADER_NAMES:
            sanitized[key] = "Bearer <API_KEY>" if value.lower().startswith("bearer ") else "<API_KEY>"
        elif lowered == "chatgpt-account-id":
            sanitized[key] = "<ACCOUNT_ID>"
        elif lowered in {"session-id", "x-client-request-id", "x-session-affinity", "x-session-id", "x-affinity"}:
            sanitized[key] = "<SESSION_ID>"
        elif lowered == "user-agent" and value.startswith("travis234 ("):
            sanitized[key] = "<USER_AGENT>"
        else:
            sanitized[key] = value
    if session_id:
        serialized = json.dumps(sanitized)
        serialized = serialized.replace(session_id, "<SESSION_ID>")
        sanitized = json.loads(serialized)
    return sanitized


def _json_safe(value: object) -> object:
    if isinstance(value, bytes):
        return {"$base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    return value


OPTION_NAMES = {
    "cacheRetention": "cache_retention",
    "extraBodyAdditions": "extra_body_additions",
    "metadata": "metadata",
    "omitMaxTokens": "omit_max_tokens",
    "providerPreferences": "provider_preferences",
    "reasoningConfig": "reasoning_config",
    "reasoningSummary": "reasoning_summary",
    "requestOverrides": "request_overrides",
    "serviceTier": "service_tier",
    "sessionId": "session_id",
    "textVerbosity": "text_verbosity",
    "timeout": "timeout",
    "toolChoice": "tool_choice",
}


def _request_parts(fixture: Mapping[str, Any], case: Mapping[str, Any]) -> tuple[object, Model, ProviderProfile, Any, dict[str, Any]]:
    request_input = case["input"]
    api_mode = str(request_input.get("apiMode") or fixture["apiMode"])
    transport = get_transport(api_mode)
    model = _model_from_facts(request_input["model"], api_mode)
    profile = _profile_from_spec(request_input.get("profile") or {}, getattr(transport, "api_mode", api_mode))
    context = _context_from_spec(request_input.get("context"), model)
    options = request_input.get("options") or {}
    kwargs: dict[str, Any] = {
        "base_url": model.base_url,
        "context": context,
        "max_tokens": options.get("maxTokens"),
        "messages": request_input.get("messages") or [],
        "model": model.id,
        "model_compat": model.compat,
        "model_reasoning": model.reasoning,
        "model_thinking_level_map": model.thinking_level_map,
        "profile": profile,
        "stream": bool(options.get("stream", True)),
        "target_model": model,
        "temperature": options.get("temperature"),
        "tools": request_input.get("tools"),
    }
    for fixture_name, parameter_name in OPTION_NAMES.items():
        if fixture_name in options:
            kwargs[parameter_name] = options[fixture_name]
    credential, _kind = _credential(options, api_mode)
    if options.get("credentialKind") == "oauth":
        kwargs["api_key"] = credential
    transport_options = SimpleNamespace(**dict(options.get("transportOptions") or {}))
    kwargs["options"] = transport_options
    return transport, model, profile, transport_options, kwargs


def render_request_case(fixture: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    """Render one request fixture through the current transport implementation."""

    transport, model, profile, transport_options, kwargs = _request_parts(fixture, case)
    options = case["input"].get("options") or {}
    api_mode = str(case["input"].get("apiMode") or fixture["apiMode"])
    credential, credential_kind = _credential(options, api_mode)
    body = transport.build_kwargs(**kwargs)
    generated_headers = body.pop("extra_headers", None)
    headers = dict(profile.default_headers)
    _merge_headers(headers, generated_headers if isinstance(generated_headers, Mapping) else None)
    if credential:
        _merge_headers(headers, profile.auth_headers(credential, credential_kind=credential_kind))
    if not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/json"
    finalize_headers = getattr(transport, "finalize_headers", None)
    session_id = options.get("sessionId")
    if callable(finalize_headers):
        headers = finalize_headers(
            headers,
            api_key=credential,
            session_id=session_id,
            cache_retention=options.get("cacheRetention"),
            model=model,
        )
    build_url = getattr(transport, "build_url", None)
    if callable(build_url):
        url = build_url(model.base_url, model.id, transport_options, credential)
    else:
        url = model.base_url.rstrip("/") + str(getattr(transport, "endpoint_path", ""))
    parsed = urlsplit(url)
    endpoint_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return {
        "body": _json_safe(body),
        "endpointPath": endpoint_path,
        "headers": _sanitize_headers(headers, session_id),
    }


def _chat_response_namespace(spec: Mapping[str, Any]) -> SimpleNamespace:
    choices = []
    for raw_choice in spec.get("choices") or []:
        raw_message = dict(raw_choice.get("message") or {})
        tool_calls = []
        for raw_call in raw_message.get("tool_calls") or []:
            call = dict(raw_call)
            function = call.get("function")
            if isinstance(function, Mapping):
                call["function"] = SimpleNamespace(**dict(function))
            tool_calls.append(SimpleNamespace(**call))
        raw_message["tool_calls"] = tool_calls or None
        choices.append(
            SimpleNamespace(
                finish_reason=raw_choice.get("finish_reason"),
                message=SimpleNamespace(**raw_message),
            )
        )
    raw_usage = spec.get("usage")
    usage = SimpleNamespace(**dict(raw_usage)) if isinstance(raw_usage, Mapping) else None
    return SimpleNamespace(choices=choices, usage=usage)


def _normalized_response(response: object) -> dict[str, Any]:
    tool_calls = getattr(response, "tool_calls")
    usage = getattr(response, "usage")
    return {
        "content": getattr(response, "content"),
        "finishReason": getattr(response, "finish_reason"),
        "providerData": getattr(response, "provider_data"),
        "reasoning": getattr(response, "reasoning"),
        "toolCalls": [
            {
                "arguments": call.arguments,
                "id": call.id,
                "name": call.name,
                "providerData": call.provider_data,
            }
            for call in tool_calls or []
        ]
        if tool_calls is not None
        else None,
        "usage": asdict(usage) if usage is not None else None,
    }


def render_response_case(fixture: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one recorded non-streaming response case."""

    api_mode = str(case["input"].get("apiMode") or fixture["apiMode"])
    transport = get_transport(api_mode)
    raw = case["input"].get("response")
    if case["input"].get("shape") == "namespace":
        raw = _chat_response_namespace(raw)
    return _normalized_response(transport.normalize_response(raw))


def _message_summary(message: object) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in getattr(message, "content"):
        if isinstance(block, TextContent):
            content.append({"text": block.text, "textSignature": block.text_signature, "type": "text"})
        elif isinstance(block, ThinkingContent):
            content.append(
                {
                    "redacted": block.redacted,
                    "thinking": block.thinking,
                    "thinkingSignature": block.thinking_signature,
                    "type": "thinking",
                }
            )
        elif isinstance(block, ToolCall):
            tool_call_id = (
                "<REQUEST_ID>"
                if re.fullmatch(r"[A-Za-z0-9_-]+_\d{10,}_\d+", block.id)
                else block.id
            )
            content.append(
                {
                    "arguments": block.arguments,
                    "id": tool_call_id,
                    "name": block.name,
                    "thoughtSignature": block.thought_signature,
                    "type": "toolCall",
                }
            )
    usage = getattr(message, "usage")
    return {
        "content": content,
        "errorMessage": getattr(message, "error_message"),
        "responseId": getattr(message, "response_id"),
        "responseModel": getattr(message, "response_model"),
        "stopReason": getattr(message, "stop_reason"),
        "usage": {
            "cacheRead": usage.cache_read,
            "cacheWrite": usage.cache_write,
            "input": usage.input,
            "output": usage.output,
            "reasoning": usage.reasoning,
            "totalTokens": usage.total_tokens,
        },
    }


def _event_summary(event: object) -> dict[str, Any]:
    summary: dict[str, Any] = {"type": getattr(event, "type")}
    if hasattr(event, "delta"):
        summary["delta"] = getattr(event, "delta")
    if hasattr(event, "content") and isinstance(getattr(event, "content"), str):
        summary["content"] = getattr(event, "content")
    if hasattr(event, "tool_call"):
        tool_call = getattr(event, "tool_call")
        tool_call_id = (
            "<REQUEST_ID>"
            if re.fullmatch(r"[A-Za-z0-9_-]+_\d{10,}_\d+", tool_call.id)
            else tool_call.id
        )
        summary["toolCall"] = {
            "arguments": tool_call.arguments,
            "id": tool_call_id,
            "name": tool_call.name,
            "thoughtSignature": tool_call.thought_signature,
        }
    if getattr(event, "type") == "done":
        summary["reason"] = getattr(event, "reason")
        summary["message"] = _message_summary(getattr(event, "message"))
    elif getattr(event, "type") == "error":
        summary["reason"] = getattr(event, "reason")
        summary["message"] = _message_summary(getattr(event, "error"))
    return summary


def render_stream_case(fixture: Mapping[str, Any], case: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Decode one SSE/EventStream case into stable public event summaries."""

    stream_input = case["input"]
    api_mode = str(stream_input.get("apiMode") or fixture["apiMode"])
    model = _model_from_facts(stream_input["model"], api_mode)
    transport = get_transport(api_mode)
    if getattr(transport, "binary_stream", False):
        events = list(_parse_bedrock_events(stream_input["events"], model))
    else:
        events = list(
            parse_sse_chunks(
                stream_input["lines"],
                model,
                api_mode=getattr(transport, "api_mode", api_mode),
                include_reasoning=bool(stream_input.get("includeReasoning", True)),
                wait_for_usage_after_finish=bool(stream_input.get("waitForUsageAfterFinish", False)),
            )
        )
    return [_event_summary(event) for event in events]


__all__ = [
    "FixtureValidationError",
    "canonical_fixture_json",
    "load_wire_fixture",
    "render_request_case",
    "render_response_case",
    "render_stream_case",
    "validate_wire_fixture",
    "wire_fixture_paths",
]
