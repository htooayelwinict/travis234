"""Scripted faux provider for tests."""

from __future__ import annotations

import json
from typing import Callable

from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers._shared import blank_assistant_message as _blank_message
from travis.ai.stream import ApiProvider
from travis.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)

FauxScript = Callable[[Model, Context], "list[AssistantMessageEvent]"]


def faux_model(api: str = "faux") -> Model:
    return Model(id="faux-model", name="Faux", api=api, provider="faux", base_url="")


def text_response_events(model: Model, text: str) -> list[AssistantMessageEvent]:
    msg = _blank_message(model)
    msg.content = [TextContent(text="")]
    events: list[AssistantMessageEvent] = [
        StartEvent(partial=msg),
        TextStartEvent(content_index=0, partial=msg),
    ]
    chunks = [text[i : i + 4] for i in range(0, len(text), 4)] or [""]
    for chunk in chunks:
        msg.content[0].text += chunk
        events.append(TextDeltaEvent(content_index=0, delta=chunk, partial=msg))
    events.append(TextEndEvent(content_index=0, content=text, partial=msg))
    final = _blank_message(model)
    final.content = [TextContent(text=text)]
    events.append(DoneEvent(reason="stop", message=final))
    return events


def tool_call_response_events(
    model: Model, tool_name: str, arguments: dict, call_id: str = "call_1"
) -> list[AssistantMessageEvent]:
    msg = _blank_message(model)
    partial_call = ToolCall(id=call_id, name=tool_name, arguments={})
    msg.content = [partial_call]
    payload = json.dumps(arguments)
    events: list[AssistantMessageEvent] = [
        StartEvent(partial=msg),
        ToolcallStartEvent(content_index=0, partial=msg),
        ToolcallDeltaEvent(content_index=0, delta=payload, partial=msg),
        ToolcallEndEvent(
            content_index=0,
            tool_call=ToolCall(id=call_id, name=tool_name, arguments=arguments),
            partial=msg,
        ),
    ]
    final = _blank_message(model)
    final.stop_reason = "toolUse"
    final.content = [ToolCall(id=call_id, name=tool_name, arguments=arguments)]
    events.append(DoneEvent(reason="toolUse", message=final))
    return events


def create_faux_provider(script: FauxScript, api: str = "faux") -> ApiProvider:
    def _stream(model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        for event in script(model, context):
            s.push(event)
        return s

    return ApiProvider(api=api, stream=_stream, stream_simple=_stream)


_ = Usage  # exported type kept available for test authors
