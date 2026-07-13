"""OpenAI-compatible provider facade."""

from __future__ import annotations

import threading

import httpx

from travis.ai.env_config import ModelConfig, load_model_config
from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers._shared import blank_assistant_message
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.catalog import get_provider_profile
from travis.ai.providers.chat_stream import PARTIAL_STREAM_STUB_ID, parse_sse_chunks
from travis.ai.providers.message_translation import convert_messages
from travis.ai.providers.provider_errors import _format_provider_exception
from travis.ai.providers.provider_request import prepare_provider_request
from travis.ai.providers.runtime_auth import NullProvider, RuntimeAuthProvider
from travis.ai.providers.streaming_json import _parse_streaming_json
from travis.ai.providers.transports import get_transport
from travis.ai.stream import ApiProvider
from travis.ai.types import Context, ErrorEvent, Model

PROVIDER_API = "openai-completions"


class TravisProvider:
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
        stream = create_assistant_message_event_stream()
        threading.Thread(target=self._run, args=(stream, model, context, options), daemon=True).start()
        return stream

    stream_simple = stream

    def _run(self, stream: AssistantMessageEventStream, model: Model, context: Context, options) -> None:
        try:
            request = prepare_provider_request(
                model,
                context,
                options,
                self.config,
                self.provider_profile,
                self._transport_for_profile,
            )
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
                            on_response({"status": response.status_code, "headers": dict(response.headers)})
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
            message.error_message = _format_provider_exception(error, model, self.config.model)
            stream.push(ErrorEvent(reason="error", error=message))


def create_travis_provider(
    prefix: str = "TRAVIS234_WORKER_LLM",
    dotenv_path: str = ".env",
    *,
    config: ModelConfig | None = None,
) -> ApiProvider:
    config = config or load_model_config(prefix, dotenv_path)
    configured = TravisProvider(config)
    implementation = configured if config.enabled else RuntimeAuthProvider(configured, PROVIDER_API)
    return ApiProvider(
        api=PROVIDER_API,
        stream=implementation.stream,
        stream_simple=implementation.stream_simple,
    )


__all__ = [
    "NullProvider",
    "RuntimeAuthProvider",
    "TravisProvider",
    "convert_messages",
    "create_travis_provider",
    "parse_sse_chunks",
]
