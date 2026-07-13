"""Runtime provider authentication selection."""

from __future__ import annotations

from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers._shared import blank_assistant_message
from travis.ai.types import Context, ErrorEvent, Model


class NullProvider:
    def __init__(self, api: str = "openai-completions") -> None:
        self.api = api

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        del context, options
        stream = create_assistant_message_event_stream()
        error = blank_assistant_message(model)
        error.stop_reason = "error"
        error.error_message = "model transport not configured"
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    stream_simple = stream


class RuntimeAuthProvider:
    def __init__(self, configured: object, api: str = "openai-completions") -> None:
        self.api = api
        self.configured = configured
        self.null = NullProvider(api)

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        api_key = getattr(options, "api_key", None) if options is not None else None
        if isinstance(api_key, str) and api_key.strip():
            return self.configured.stream(model, context, options)
        return self.null.stream(model, context, options)

    stream_simple = stream
