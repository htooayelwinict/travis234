from __future__ import annotations

from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from tests._provider_runtime import register_api_provider, reset_api_providers, stream
from travis.ai.types import Context, UserMessage, now_ms


def setup_function() -> None:
    reset_api_providers()


def _ctx() -> Context:
    return Context(messages=[UserMessage(content="q", timestamp=now_ms())])


def test_faux_text_response_event_sequence() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "hello world")))
    s = stream(model, _ctx())
    types = [e.type for e in s]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert "text_delta" in types
    assert s.result_sync().content[0].text == "hello world"


def test_faux_tool_call_response() -> None:
    model = faux_model()
    register_api_provider(
        create_faux_provider(lambda m, c: tool_call_response_events(m, "read", {"path": "a.txt"}))
    )
    msg = stream(model, _ctx()).result_sync()
    assert msg.stop_reason == "toolUse"
    tool_call = msg.content[0]
    assert tool_call.type == "toolCall"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "a.txt"}
