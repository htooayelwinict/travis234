from __future__ import annotations

import json

import urllib.error

from types import SimpleNamespace

import httpx

from travis.ai.env_config import ModelConfig

import travis.ai.providers.travis_env as travis_env

from travis.ai.providers.travis_env import (
    TravisProvider,
    NullProvider,
    convert_messages,
    create_travis_provider,
    parse_sse_chunks,
)

from travis.ai.providers.base import ProviderProfile

from travis.ai.providers.params import GenerationParams

from travis.ai.providers.transports import ChatCompletionsTransport

from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    TextDeltaEvent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)

def _model() -> Model:
    return Model(id="acme/x", name="X", api="openai-completions", provider="openrouter", base_url="")

def _openrouter_provider() -> TravisProvider:
    return TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

def _run_http_status_failure(monkeypatch, response: httpx.Response) -> AssistantMessage:
    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                f"Client error '{response.status_code} {response.reason_phrase}'",
                request=response.request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)
    return _openrouter_provider().stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)

__all__ = [name for name in globals() if not (name.startswith('__') and name.endswith('__'))]
