"""Google Generative AI and Vertex transport family."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

from travis.ai.providers.base import NormalizedResponse, ProviderProfile
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _google_requires_tool_call_id(model_id: str) -> bool:
    return model_id.startswith(("claude-", "gpt-oss-"))


def _google_valid_thought_signature(signature: str | None) -> bool:
    return bool(
        signature
        and len(signature) % 4 == 0
        and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", signature)
    )


def _google_supports_multimodal_function_response(model_id: str) -> bool:
    match = re.match(r"^gemini(?:-live)?-(\d+)", model_id.lower())
    return int(match.group(1)) >= 3 if match else True


def _google_user_content(message: UserMessage) -> dict[str, object] | None:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    if isinstance(message.content, str):
        parts: list[dict[str, object]] = [
            {"text": _sanitize_surrogates(message.content)}
        ]
    else:
        parts = []
        for block in message.content:
            if isinstance(block, TextContent):
                parts.append({"text": _sanitize_surrogates(block.text)})
            elif isinstance(block, ImageContent):
                parts.append(
                    {"inlineData": {"mimeType": block.mime_type, "data": block.data}}
                )
    return {"role": "user", "parts": parts} if parts else None


def _google_assistant_content(
    message: AssistantMessage,
    model: Model,
) -> dict[str, object] | None:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    parts: list[dict[str, object]] = []
    same_model = message.provider == model.provider and message.model == model.id
    for block in message.content:
        if isinstance(block, TextContent) and block.text.strip():
            part: dict[str, object] = {"text": _sanitize_surrogates(block.text)}
            if same_model and _google_valid_thought_signature(block.text_signature):
                part["thoughtSignature"] = block.text_signature
            parts.append(part)
        elif isinstance(block, ThinkingContent) and block.thinking.strip():
            part = {"text": _sanitize_surrogates(block.thinking)}
            if same_model:
                part["thought"] = True
                if _google_valid_thought_signature(block.thinking_signature):
                    part["thoughtSignature"] = block.thinking_signature
            parts.append(part)
        elif isinstance(block, ToolCall):
            call: dict[str, object] = {"name": block.name, "args": block.arguments or {}}
            if _google_requires_tool_call_id(model.id):
                call["id"] = block.id
            part = {"functionCall": call}
            if same_model and _google_valid_thought_signature(block.thought_signature):
                part["thoughtSignature"] = block.thought_signature
            parts.append(part)
    return {"role": "model", "parts": parts} if parts else None


def _append_to_previous_function_response(
    contents: list[dict[str, object]],
    part: dict[str, object],
) -> bool:
    if not contents or contents[-1].get("role") != "user":
        return False
    parts = contents[-1].get("parts")
    if not isinstance(parts, list):
        return False
    if not any(isinstance(item, dict) and "functionResponse" in item for item in parts):
        return False
    parts.append(part)
    return True


def _append_google_tool_result(
    contents: list[dict[str, object]],
    message: ToolResultMessage,
    model: Model,
) -> None:
    from travis.ai.providers.message_translation import _sanitize_surrogates

    text = "\n".join(
        block.text for block in message.content if isinstance(block, TextContent)
    )
    images = (
        [block for block in message.content if isinstance(block, ImageContent)]
        if "image" in model.input
        else []
    )
    response_value = _sanitize_surrogates(
        text if text else "(see attached image)" if images else ""
    )
    response = {"error" if message.is_error else "output": response_value}
    image_parts: list[dict[str, object]] = [
        {"inlineData": {"mimeType": image.mime_type, "data": image.data}}
        for image in images
    ]
    supports_multimodal = _google_supports_multimodal_function_response(model.id)
    function_response: dict[str, object] = {
        "name": message.tool_name,
        "response": response,
    }
    if images and supports_multimodal:
        function_response["parts"] = image_parts
    if _google_requires_tool_call_id(model.id):
        function_response["id"] = message.tool_call_id
    part: dict[str, object] = {"functionResponse": function_response}
    if not _append_to_previous_function_response(contents, part):
        contents.append({"role": "user", "parts": [part]})
    if images and not supports_multimodal:
        contents.append(
            {"role": "user", "parts": [{"text": "Tool result image:"}, *image_parts]}
        )


def _google_contents(context: Context, model: Model) -> list[dict[str, object]]:
    from travis.ai.providers.message_translation import _transform_messages

    contents: list[dict[str, object]] = []
    transformed = _transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _model, _source: (
            re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64]
            if _google_requires_tool_call_id(model.id)
            else tool_call_id
        ),
    )
    for message in transformed:
        if isinstance(message, UserMessage):
            converted = _google_user_content(message)
            if converted is not None:
                contents.append(converted)
        elif isinstance(message, AssistantMessage):
            converted = _google_assistant_content(message, model)
            if converted is not None:
                contents.append(converted)
        else:
            _append_google_tool_result(contents, message, model)
    return contents


def _google_tools(context: Context) -> list[dict[str, Any]] | None:
    if not context.tools:
        return None
    return [
        {
            "functionDeclarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parametersJsonSchema": tool.parameters,
                }
                for tool in context.tools
            ]
        }
    ]


class GoogleGenerativeAITransport:
    api = "google-generative-ai"
    api_mode = "google_generative_ai"
    endpoint_path = ""

    @staticmethod
    def build_url(
        base_url: str,
        model: str,
        _options: object | None = None,
        _api_key: str | None = None,
    ) -> str:
        return f"{base_url.rstrip('/')}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse"

    @staticmethod
    def _thinking_config(model: str, reasoning_config: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(reasoning_config, dict):
            return None
        enabled = reasoning_config.get("enabled", True) is not False
        effort = str(reasoning_config.get("effort") or "medium").strip().lower()
        is_gemma4 = bool(re.search(r"gemma-?4", model.lower()))
        is_gemini3_pro = bool(re.search(r"gemini-3(?:\.\d+)?-pro", model.lower()))
        is_gemini3_flash = bool(re.search(r"gemini-3(?:\.\d+)?-flash", model.lower())) or model.lower() in {
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
        }
        if not enabled or effort in {"none", "off"}:
            if is_gemini3_pro:
                return {"thinkingLevel": "LOW"}
            if is_gemini3_flash or is_gemma4:
                return {"thinkingLevel": "MINIMAL"}
            return {"thinkingBudget": 0}
        effort = effort if effort in {"minimal", "low", "medium", "high"} else "medium"
        if is_gemini3_pro:
            level = "LOW" if effort in {"minimal", "low"} else "HIGH"
            return {"includeThoughts": True, "thinkingLevel": level}
        if is_gemini3_flash:
            return {"includeThoughts": True, "thinkingLevel": effort.upper()}
        if is_gemma4:
            level = "MINIMAL" if effort in {"minimal", "low"} else "HIGH"
            return {"includeThoughts": True, "thinkingLevel": level}
        if "2.5-pro" in model:
            budget = {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}[effort]
        elif "2.5-flash-lite" in model:
            budget = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576}[effort]
        elif "2.5-flash" in model:
            budget = {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}[effort]
        else:
            budget = -1
        return {"includeThoughts": True, "thinkingBudget": budget}

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
        omit_max_tokens: bool = False,
        tool_choice: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        context: Context,
        target_model: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
        thinking = self._thinking_config(model, reasoning_config)
        if target_model.reasoning and thinking:
            generation_config["thinkingConfig"] = thinking
        body: dict[str, Any] = {"contents": _google_contents(context, target_model)}
        if generation_config:
            body["generationConfig"] = generation_config
        if context.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": context.system_prompt}]}
        google_tools = _google_tools(context)
        if google_tools:
            body["tools"] = google_tools
            if tool_choice:
                mode = tool_choice.upper() if tool_choice in {"auto", "none", "any"} else "AUTO"
                body["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
        if request_overrides:
            body.update(request_overrides)
        return body

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="stop")


class GoogleVertexTransport(GoogleGenerativeAITransport):
    api = "google-vertex"
    api_mode = "google_vertex"

    @staticmethod
    def build_url(
        base_url: str,
        model: str,
        options: object | None = None,
        api_key: str | None = None,
    ) -> str:
        if api_key:
            model_path = f"publishers/google/models/{quote(model, safe='')}"
            return f"https://aiplatform.googleapis.com/v1/{model_path}:streamGenerateContent?alt=sse"
        project = str(
            getattr(options, "project", None)
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
            or ""
        ).strip()
        location = str(getattr(options, "location", None) or os.environ.get("GOOGLE_CLOUD_LOCATION") or "").strip()
        if not project:
            raise ValueError(
                "Vertex AI requires a project ID. Set GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT or pass project in options."
            )
        if not location:
            raise ValueError("Vertex AI requires a location. Set GOOGLE_CLOUD_LOCATION or pass location in options.")
        model_path = f"publishers/google/models/{quote(model, safe='')}"
        if location in {"us", "eu"}:
            root = f"https://aiplatform.{location}.rep.googleapis.com/v1"
        else:
            root = f"https://{location}-aiplatform.googleapis.com/v1"
        return (
            f"{root}/projects/{quote(project, safe='')}/locations/{quote(location, safe='')}/"
            f"{model_path}:streamGenerateContent?alt=sse"
        )


__all__ = ["GoogleGenerativeAITransport", "GoogleVertexTransport"]
