from __future__ import annotations

import json

from travis.ai.providers.travis_env import parse_sse_chunks
from travis.ai.types import DoneEvent, Model, StartEvent, TextDeltaEvent, TextEndEvent, TextStartEvent


def test_chat_stream_event_tuple_is_stable() -> None:
    model = Model(id="fixture", name="Fixture", api="openai-completions", provider="fixture", base_url="")
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hello"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, model))

    assert [type(event) for event in events] == [StartEvent, TextStartEvent, TextDeltaEvent, TextEndEvent, DoneEvent]
    assert events[-1].message.content[0].text == "hello"
