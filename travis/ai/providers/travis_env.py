"""OpenAI-compatible provider facade."""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import replace
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from travis.ai.error_redaction import is_sensitive_name
from travis.ai.env_config import ModelConfig, load_model_config
from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers._shared import blank_assistant_message, settle_callback
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.catalog import get_provider_profile
from travis.ai.providers.chat_stream import parse_sse_chunks
from travis.ai.providers.bedrock_stream import _parse_bedrock_events
from travis.ai.providers.message_translation import convert_messages
from travis.ai.providers.provider_errors import _format_provider_exception
from travis.ai.providers.provider_request import prepare_provider_request
from travis.ai.providers.provider_request import PreparedProviderRequest
from travis.ai.providers.streaming_json import _parse_streaming_json
from travis.ai.types import Context, ErrorEvent, Model

PROVIDER_API = "openai-completions"


class _BedrockEventRegistry(Protocol):
    def register(
        self,
        event_name: str,
        callback: Callable[..., None],
    ) -> object: ...


class _BedrockClientMeta(Protocol):
    @property
    def events(self) -> _BedrockEventRegistry: ...


class _BedrockHeaderClient(Protocol):
    @property
    def meta(self) -> _BedrockClientMeta: ...


class _BedrockHttpRequest(Protocol):
    headers: MutableMapping[str, str]


def _bedrock_client_target(
    options: object | None,
    model: Model,
    request_url: str,
) -> tuple[str, str]:
    region = str(
        getattr(options, "region", None)
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or ""
    ).strip()
    if not region and model.id.startswith("arn:"):
        arn_parts = model.id.split(":", 5)
        if len(arn_parts) > 3:
            region = arn_parts[3]
    parsed_url = urlsplit(request_url)
    if not region:
        match = re.match(
            r"^bedrock-runtime(?:-fips)?\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?$",
            parsed_url.hostname or "",
        )
        if match:
            region = match.group(1)
    region = region or "us-east-1"
    return region, f"{parsed_url.scheme}://{parsed_url.netloc}"


def _bedrock_boto_config(timeout_seconds: float | None) -> dict[str, object]:
    config: dict[str, object] = {
        "retries": {"max_attempts": 2, "mode": "standard"},
    }
    if timeout_seconds is not None:
        config["connect_timeout"] = timeout_seconds
        config["read_timeout"] = timeout_seconds
    return config


def _register_bedrock_custom_headers(
    client: _BedrockHeaderClient,
    headers: Mapping[str, str],
) -> None:
    custom_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"authorization", "content-type", "host"}
        and not key.lower().startswith("x-amz-")
    }
    if not custom_headers:
        return

    def add_custom_headers(
        http_request: _BedrockHttpRequest,
        **_kwargs: object,
    ) -> None:
        for key, value in custom_headers.items():
            http_request.headers[key] = value

    client.meta.events.register(
        "before-sign.bedrock-runtime.ConverseStream",
        add_custom_headers,
    )


def _notify_bedrock_response(
    options: object | None,
    response: object,
    model: Model,
) -> None:
    on_response = getattr(options, "on_response", None) if options is not None else None
    metadata = response.get("ResponseMetadata") if isinstance(response, dict) else None
    if not callable(on_response) or not isinstance(metadata, dict):
        return
    response_headers = metadata.get("HTTPHeaders")
    settle_callback(
        on_response(
            {
                "status": int(metadata.get("HTTPStatusCode") or 200),
                "headers": dict(response_headers) if isinstance(response_headers, dict) else {},
            },
            model,
        )
    )


class TravisProvider:
    api = PROVIDER_API

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.provider_profile = get_provider_profile(config.provider) or ProviderProfile(
            name=config.provider,
            base_url=config.base_url,
        )
    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        stream = create_assistant_message_event_stream()
        threading.Thread(target=self._run, args=(stream, model, context, options), daemon=True).start()
        return stream

    stream_simple = stream

    def _run(self, stream: AssistantMessageEventStream, model: Model, context: Context, options) -> None:
        request: PreparedProviderRequest | None = None
        try:
            request = prepare_provider_request(
                model,
                context,
                options,
                self.config,
                self.provider_profile,
            )
            request = _authorize_google_vertex_request(request)
            if request.api_mode == "bedrock_converse_stream":
                self._run_bedrock(stream, model, options, request)
                return
            if request.api_mode == "openai_codex_responses":
                from travis.ai.providers.codex_runtime import run_codex_request

                run_codex_request(stream, model, options, request)
                return
            with httpx.Client(timeout=request.timeout_seconds) as client:
                with client.stream("POST", request.url, json=request.body, headers=request.headers) as response:
                    signal = getattr(options, "signal", None) if options is not None else None
                    unsubscribe_abort = (
                        signal.add_callback(response.close)
                        if signal is not None and hasattr(signal, "add_callback")
                        else lambda: None
                    )
                    try:
                        on_response = getattr(options, "on_response", None) if options is not None else None
                        if callable(on_response):
                            settle_callback(
                                on_response(
                                    {"status": response.status_code, "headers": dict(response.headers)},
                                    model,
                                )
                            )
                        if not response.is_success:
                            response.read()
                        response.raise_for_status()
                        for event in request.decoder(response.iter_lines()):
                            if signal is not None and signal.aborted:
                                raise RuntimeError("Operation aborted")
                            stream.push(event)
                    finally:
                        unsubscribe_abort()
        except Exception as error:
            message = blank_assistant_message(model)
            message.stop_reason = "error"
            message.error_message = _format_provider_exception(
                error,
                model,
                self.config.model,
                secrets=_provider_request_secrets(self.config.api_key, options, request),
            )
            stream.push(ErrorEvent(reason="error", error=message))

    def _run_bedrock(self, stream, model: Model, options, request) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - package dependency guard
            raise RuntimeError("Bedrock support requires the boto3 runtime dependency") from exc

        region, endpoint_url = _bedrock_client_target(options, model, request.url)
        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(**_bedrock_boto_config(request.timeout_seconds)),
        )
        _register_bedrock_custom_headers(client, request.headers)

        response = client.converse_stream(modelId=model.id, **dict(request.body))
        _notify_bedrock_response(options, response, model)
        event_stream = response.get("stream") if isinstance(response, dict) else None
        if event_stream is None:
            raise RuntimeError("Bedrock ConverseStream returned no event stream")
        signal = getattr(options, "signal", None) if options is not None else None
        try:
            for event in _parse_bedrock_events(event_stream, model):
                if signal is not None and signal.aborted:
                    raise RuntimeError("Operation aborted")
                stream.push(event)
        finally:
            close = getattr(event_stream, "close", None)
            if callable(close):
                close()


def _authorize_google_vertex_request(request: PreparedProviderRequest) -> PreparedProviderRequest:
    if request.api_mode != "google_vertex":
        return request
    if any(
        key.lower() in {"authorization", "x-goog-api-key"} and str(value).strip()
        for key, value in request.headers.items()
    ):
        return request
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError as exc:  # pragma: no cover - package dependency guard
        raise RuntimeError("Vertex ADC support requires the google-auth runtime dependency") from exc

    credentials, _project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    token = getattr(credentials, "token", None)
    if not isinstance(token, str) or not token:
        raise RuntimeError("Google Application Default Credentials returned no access token")
    headers = dict(request.headers)
    headers["Authorization"] = f"Bearer {token}"
    return replace(request, headers=headers)


def _provider_request_secrets(
    configured_api_key: str | None,
    options: object | None,
    request: PreparedProviderRequest | None,
) -> tuple[str, ...]:
    values = [configured_api_key, getattr(options, "api_key", None) if options is not None else None]
    if request is not None:
        for name, value in request.headers.items():
            if is_sensitive_name(name):
                values.append(str(value))
    return tuple(str(value) for value in values if value is not None and str(value))


__all__ = [
    "TravisProvider",
    "convert_messages",
    "parse_sse_chunks",
]
