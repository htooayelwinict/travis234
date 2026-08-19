"""Azure OpenAI Responses transport family."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from travis.ai.providers.transport_families.chat_completions import _clamp_openai_prompt_cache_key
from travis.ai.providers.transport_families.responses import OpenAIResponsesTransport


def _resolve_azure_deployment_name(
    model_id: str,
    explicit: str | None,
    mappings: str,
) -> str:
    deployment_name = str(explicit or "").strip()
    if deployment_name:
        return deployment_name
    for entry in mappings.split(","):
        source, separator, target = entry.strip().partition("=")
        if separator and source.strip() == model_id and target.strip():
            return target.strip()
    return model_id


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

    @staticmethod
    def build_url(
        base_url: str,
        _model: str,
        options: object | None,
        _api_key: str | None,
    ) -> str:
        normalized = AzureOpenAIResponsesTransport._resolve_base_url(base_url, options)
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
        kwargs["model"] = _resolve_azure_deployment_name(
            model_id,
            getattr(options, "azure_deployment_name", None),
            str(os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME_MAP") or ""),
        )
        body = super().build_kwargs(**kwargs)
        body.pop("extra_headers", None)
        body.pop("prompt_cache_retention", None)
        body.pop("service_tier", None)
        body.pop("tool_choice", None)
        if session_id:
            body["prompt_cache_key"] = _clamp_openai_prompt_cache_key(str(session_id))
        return body


__all__ = ["AzureOpenAIResponsesTransport"]
